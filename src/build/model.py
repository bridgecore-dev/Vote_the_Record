"""Load a validated data repo into plain dicts for the templates.

The validator has already checked every file, so this module only reads and
arranges: it never repairs or guesses at data.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "validate"))
from validate import _NoDateLoader, classify, measure_level, race_area, race_parts  # noqa: E402

# Per-state wording used on pages. Add a state here before adding its data.
STATES = {
    "TX": {"name": "Texas", "judicial_code": "the Texas Code of Judicial Conduct",
           "address_example": "1001 Preston St, Houston, TX", "zip_example": "77002"},
    "WA": {"name": "Washington", "judicial_code": "the Washington Code of Judicial Conduct",
           "address_example": "500 4th Ave, Seattle, WA", "zip_example": "98104"},
}
STATE_NAMES = {code: s["name"] for code, s in STATES.items()}


def load_yaml(path: Path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_NoDateLoader)


def title_from_slug(slug: str) -> str:
    return " ".join(word.capitalize() for word in slug.split("-"))


def load_docs(root: Path) -> dict[str, tuple[str, object]]:
    docs = {}
    for base in ("data", "offices", "corrections"):
        for file in sorted((root / base).rglob("*.yaml")):
            rel = file.relative_to(root).as_posix()
            kind = classify(rel)
            if kind:
                docs[rel] = (kind, load_yaml(file))
    return docs


def race_location(rel: str) -> dict:
    """data/states/tx/<statewide|districts/x|localities/x>/elections/<date>/<slug>/race.yaml -> parts."""
    p = race_parts(rel)
    return {"state": p["state"], "scope": p["scope"], "locality": p["place"],
            "election_date": p["election_date"], "slug": p["slug"]}


# Ballot order, closest to home first (docs/METHODOLOGY.md, "How your ballot is ordered").
LEVELS = [
    ("local", "Closest to home", "City, school district, and neighborhood offices and questions."),
    ("county", "Your county", "County government and the local courts."),
    ("state", "Your state", "{state} government, lawmakers, and state courts."),
    ("federal", "National", "Congress: your U.S. senators and representative."),
]
LEVEL_RANK = {key: i for i, (key, _, _) in enumerate(LEVELS)}


def assemble_ballot(docs: dict, rel: str, doc: dict, races_by_path: dict, root: Path | None = None) -> dict:
    folder = rel.rsplit("/", 1)[0]
    state, locality, date = folder.split("/")[2], folder.split("/")[4], folder.split("/")[6]
    contests = [races_by_path[c] for c in doc["contests"]]
    order = {c["path"]: i for i, c in enumerate(contests)}
    ranked = sorted(contests, key=lambda r: (LEVEL_RANK[r["level"]], order[r["path"]]))
    groups = []
    for key, title, blurb in LEVELS:
        members = [{"n": i, "race": r} for i, r in enumerate(ranked) if r["level"] == key]
        if members:
            groups.append({"level": key, "title": title, "blurb": blurb.format(state=STATE_NAMES.get(state.upper(), state.upper())),
                           "races": members})
    zips_doc = docs.get(f"{folder}/zips.yaml", (None, None))[1] or {}
    has_shapes = bool(root) and (root / folder / "precinct-shapes.json").is_file()
    has_area_shapes = has_shapes and (root / folder / "area-shapes.json").is_file()
    pct_doc = docs.get(f"{folder}/precincts.yaml", (None, None))[1] or {}
    # Precincts are sent as indexes into one area list, to keep the page small.
    def flat(entries):
        return [a for e in entries for a in (e if isinstance(e, list) else [e])]

    area_list = sorted({a for entries in (pct_doc.get("precincts") or {}).values() for a in flat(entries)})
    area_index = {a: i for i, a in enumerate(area_list)}

    def pack(entries):
        return [[area_index[a] for a in e] if isinstance(e, list) else area_index[e] for e in entries]
    payload = {
        "name": doc["name"],
        "implied": [state, locality],
        "unmapped": doc.get("unmapped") or [],
        "zips": zips_doc.get("zips") or {},
        "zipEverywhere": zips_doc.get("everywhere") or [],
        "areas": area_list,
        "precincts": {k: pack(v) for k, v in (pct_doc.get("precincts") or {}).items()},
        "precinctEverywhere": pct_doc.get("everywhere") or [],
        "locality": locality,
        "shapesUrl": f"/geo/{state}/{locality}/{date}/precinct-shapes.json" if has_shapes else None,
        "areaShapesUrl": f"/geo/{state}/{locality}/{date}/area-shapes.json" if has_area_shapes else None,
        "localAreas": sorted(json.loads((root / folder / "area-shapes.json").read_text(encoding="utf-8"))["areas"])
                      if has_area_shapes else [],
        "races": [{"n": i, "area": r["area"], "office": r["office_type"], "kind": r.get("kind") or "race",
                   "topics": r.get("topics") or []} for i, r in enumerate(ranked)],
        # Races with a quiz (never judges'; see METHODOLOGY "Quiz for your whole ballot").
        "quiz": [{"n": i, "key": f"{r['locality']}/{r['slug']}", "name": r["name"], "url": r["url"],
                  "questions": r["payload"]["questions"], "candidates": r["payload"]["candidates"]}
                 for i, r in enumerate(ranked)
                 if r.get("kind") != "measure" and r.get("questions") and not r.get("judicial")],
    }
    return {
        "name": doc["name"], "state": state, "locality": locality, "election_date": date,
        "state_info": STATES.get(state.upper(), {"name": state.upper(), "judicial_code": "the state's code of judicial conduct",
                                                 "address_example": "", "zip_example": ""}),
        "url": f"/ballot/{state}/{locality}/{date}/",
        "lookup_url": doc.get("lookup_url"), "sources": doc["sources"],
        "sample_ballot_url": doc.get("sample_ballot_url") or doc["sources"][0]["source_url"],
        "complete": doc.get("complete", True),
        "unmapped_races": [{"n": i, "race": r} for i, r in enumerate(ranked) if r["area"] in set(doc.get("unmapped") or [])],
        "zip_sources": zips_doc.get("sources") or [],
        "precinct_sources": pct_doc.get("sources") or [],
        "topics_used": sorted({t for r in ranked for t in r.get("topics") or []}),
        "has_zips": bool(payload["zips"]), "has_precincts": bool(payload["precincts"]), "groups": groups, "ranked": ranked,
        "count": len(ranked), "payload": payload, "rel": rel,
        "shapes_src": (root / folder / "precinct-shapes.json") if has_shapes else None,
        "area_shapes_src": (root / folder / "area-shapes.json") if has_area_shapes else None,
    }


def load_site(root: Path, today: str) -> dict:
    docs = load_docs(root)

    races = []
    for rel, (kind, doc) in docs.items():
        if kind != "race":
            continue
        loc = race_location(rel)
        office_doc = docs.get(f"offices/{doc['office_type']}/powers.yaml", (None, None))[1] or {}
        races.append({
            **loc,
            "kind": "race",
            "name": doc["name"],
            "office_type": doc["office_type"],
            "area": race_area(rel, doc),
            "level": doc.get("level") or office_doc.get("level"),
            "detail": doc.get("detail", "full"),
            "seats": doc.get("seats", 1),
            "path": rel.removeprefix("data/states/").removesuffix("/race.yaml"),
            "state_name": STATE_NAMES.get(loc["state"].upper(), loc["state"].upper()),
            "locality_name": {"statewide": "Statewide", "districts": "Districts"}.get(
                loc["scope"], title_from_slug(loc["locality"])),
            "url": f"/races/{loc['state']}/{loc['locality']}/{loc['election_date']}/{loc['slug']}/",
            "rel": rel,
        })
    races.sort(key=lambda r: (r["state"], r["locality"] != "statewide", r["locality_name"],
                              r["election_date"], r["name"].casefold()))

    measures = []
    for rel, (kind, doc) in docs.items():
        if kind != "measure":
            continue
        loc = race_location(rel)
        measures.append({
            **loc,
            "kind": "measure",
            "measure_kind": doc.get("kind"),
            "name": doc["name"],
            "office_type": None,
            "area": race_area(rel, doc),
            "level": measure_level(rel, doc),
            "detail": "basic",
            "path": rel.removeprefix("data/states/").removesuffix("/measure.yaml"),
            "state_name": STATE_NAMES.get(loc["state"].upper(), loc["state"].upper()),
            "locality_name": {"statewide": "Statewide", "districts": "Districts"}.get(
                loc["scope"], title_from_slug(loc["locality"])),
            "url": f"/measures/{loc['state']}/{loc['locality']}/{loc['election_date']}/{loc['slug']}/",
            "rel": rel,
            "ballot_text": doc["ballot_text"],
            "choices": doc["choices"],
            "explained": doc.get("explained") or [],
            "sources": doc["sources"],
            "candidates": [], "plain_summary": None, "questions": [], "seats": 1,
        })

    essentials = []
    for rel, (kind, doc) in docs.items():
        if kind != "voter_essentials" or doc["election_date"] < today:
            continue
        items = sorted(doc["items"], key=lambda i: i.get("date") or i.get("start"))
        essentials.append({
            "state": doc["state"],
            "state_name": STATE_NAMES.get(doc["state"], doc["state"]),
            "election_date": doc["election_date"],
            "entries": items,
            "at_the_polls": doc.get("at_the_polls") or [],
        })
    essentials.sort(key=lambda e: (e["election_date"], e["state"]))

    corrections = docs.get("corrections/log.yaml", (None, []))[1] or []
    corrections = sorted(corrections, key=lambda c: c["date"], reverse=True)

    facts = fact_index(docs)
    for race in races:
        race.update(assemble_race(docs, race, facts))

    races_by_path = {r["path"]: r for r in races + measures}
    ballots = [assemble_ballot(docs, rel, doc, races_by_path, root)
               for rel, (kind, doc) in docs.items() if kind == "ballot" and doc["election_date"] >= today]
    ballots.sort(key=lambda b: (b["election_date"], b["state"], b["name"].casefold()))
    on_ballot = {r["path"] for b in ballots for r in b["ranked"]}
    county_index: dict[str, list[dict]] = {}
    for b in ballots:
        if b["payload"]["shapesUrl"]:
            county_index.setdefault(b["locality"], []).append({"name": b["name"], "url": b["url"]})
    zip_index: dict[str, list[dict]] = {}
    for b in ballots:
        for zip_code in b["payload"]["zips"]:
            zip_index.setdefault(zip_code, []).append({"name": b["name"], "url": b["url"]})

    return {"races": races, "measures": measures, "essentials": essentials, "corrections": corrections,
            "ballots": ballots, "other_races": [r for r in races if r["path"] not in on_ballot],
            "zip_index": zip_index, "county_index": county_index, "who_decides": load_who_decides(docs)}


FACT_SECTIONS = ("summary", "records", "funding", "endorsements")
RUNNING_ON = ("policy_proposals", "attack_messaging", "contested_claims")


def fact_index(docs: dict) -> dict[str, dict]:
    """Every fact in the repo by ID, so promises and contradictions can link across files."""
    index = {}
    for kind, doc in docs.values():
        if kind == "actions":
            index.update((f["id"], f) for f in doc.get("facts") or [])
        elif kind == "candidate":
            for section in FACT_SECTIONS:
                index.update((f["id"], f) for f in doc.get(section) or [])
            for section in RUNNING_ON:
                index.update((f["id"], f) for f in (doc.get("running_on") or {}).get(section) or [])
    return index


def short(fact: dict) -> str:
    """The fact's headline if it has one, else its full statement."""
    return fact.get("headline") or fact["statement"]


def clip(text: str, limit: int = 110) -> str:
    """Shorten at a word boundary for the at-a-glance card; the full text stays in the details."""
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def card_text(fact: dict) -> str:
    """Card line: the headline, or else the statement shortened (never reworded)."""
    return fact.get("headline") or clip(fact["statement"])


def topic(fact: dict) -> str:
    """Short topic for the Priorities line, taken from wording already in the fact.

    'Affordability: stop tax increases' (headline) -> 'Affordability'
    'Campaign platform, Flood Protection: ...' (statement) -> 'Flood Protection'
    """
    head = fact.get("headline")
    if head and ":" in head:
        return head.split(":", 1)[0].strip()
    m = re.match(r"Campaign platform, ([^:]{1,40}):", fact["statement"])
    return m.group(1).strip() if m else clip(short(fact), 40)


def glance(cand: dict) -> list[dict]:
    """At-a-glance lines for a candidate card, built only from facts already on file.

    Each line links to the fact(s) it came from, so nothing on the card lacks a source.
    """
    lines = []
    for fact in cand.get("summary") or []:
        lines.append({"icon": "info", "text": card_text(fact), "facts": [fact]})
    for fact in cand.get("funding") or []:
        lines.append({"icon": "money", "text": card_text(fact), "facts": [fact]})
    proposals = (cand.get("running_on") or {}).get("policy_proposals") or []
    if proposals:
        lines.append({"icon": "target", "label": "Priorities",
                      "text": " · ".join(topic(f) for f in proposals), "facts": proposals})
    endorsements = cand.get("endorsements") or []
    if endorsements:
        lines.append({"icon": "megaphone", "label": "Endorsed by",
                      "text": " · ".join(card_text(f) for f in endorsements), "facts": endorsements})
    return lines


RECORD_ON_CARD = 3


def record_top(cand: dict, facts: dict) -> dict:
    """The record block that leads each card, with results.

    Order (same rule for every candidate): recorded votes first, then other actions;
    newest first within each group. Every item is a fact on file; follow-ups are
    resolved to their own sourced facts.
    """
    records = sorted(cand.get("records") or [], key=lambda r: r.get("event_date") or "", reverse=True)
    records.sort(key=lambda r: r["record_type"] != "vote")  # stable: keeps newest-first within groups
    items = []
    for r in records[:RECORD_ON_CARD]:
        items.append({
            "fact": r,
            "kind": r["record_type"],
            "text": card_text(r),
            "year": (r.get("event_date") or "")[:4],
            "result": r.get("result"),
            "followups": [facts[f] for f in r.get("followups") or [] if f in facts],
        })
    return {"items": items, "total": len(records), "more": max(0, len(records) - RECORD_ON_CARD),
            "summary": record_count_text(records)}


RECORD_WORDS = {"vote": ("vote", "votes"), "sponsored": ("measure sponsored", "measures sponsored"),
                "promise_kept": ("promise kept", "promises kept"),
                "promise_broken": ("promise broken", "promises broken"),
                "statement": ("statement", "statements")}


def record_count_text(records: list[dict]) -> str:
    """'2 votes, 1 statement on record' or 'No record on file yet'."""
    if not records:
        return "No record on file yet"
    counts: dict[str, int] = {}
    for r in records:
        counts[r["record_type"]] = counts.get(r["record_type"], 0) + 1
    parts = [f"{n} {RECORD_WORDS[t][0 if n == 1 else 1]}" for t, n in counts.items()]
    return ", ".join(parts) + " on record"


PROMISE_MIN_DECIDED = 3  # same bar as the quiz: fewer decided promises show counts only
DECIDED = ("kept", "broken", "opposite")


def checked(fact: dict | None) -> bool:
    return bool(fact) and fact.get("verification") == "verified" and fact.get("claim_status") == "documented"


def promise_score(promises: list[dict]) -> dict:
    """Counts and kept rate for an incumbent's promise tracker (docs/METHODOLOGY.md).

    A promise counts only if the promise itself is verified and documented and, when it
    is decided, every piece of evidence is too. The rate is kept / decided, shown only
    once PROMISE_MIN_DECIDED promises are decided. Display-only: never affects the quiz.
    """
    counts = {s: 0 for s in (*DECIDED, "pending")}
    unchecked = 0
    for p in promises:
        ok = checked(p["promise_fact"]) and (
            p["status"] == "pending" or all(checked(e) for e in p["evidence_facts"]))
        if ok:
            counts[p["status"]] += 1
        else:
            unchecked += 1
    decided = sum(counts[s] for s in DECIDED)
    rate = round(100 * counts["kept"] / decided) if decided >= PROMISE_MIN_DECIDED else None
    return {"counts": counts, "made": decided + counts["pending"], "decided": decided,
            "rate": rate, "unchecked": unchecked}


def pick_why_it_matters(race_doc: dict, locality_doc: dict | None, office_doc: dict | None) -> list:
    """The most specific level wins: race, then locality, then office type."""
    for doc in (race_doc, locality_doc, office_doc):
        if doc and doc.get("why_it_matters"):
            return doc["why_it_matters"]
    return []


DECISIONS_ON_PAGE = 6

# Plain names for office types in the "Who decides?" guide.
OFFICE_NAMES = {
    "us-representative": "U.S. representative", "us-senator": "U.S. senator", "sboe-member": "State Board of Education member",
    "cca-judge": "Court of Criminal Appeals judge", "jp": "Justice of the peace",
    "esd-commissioner": "Emergency services district commissioner",
}


def office_name(slug: str) -> str:
    return OFFICE_NAMES.get(slug) or slug.replace("-", " ").capitalize()


def anchor(item_id: str) -> str:
    """tx/who-decides#sanctuary-cities -> sanctuary-cities"""
    return item_id.rsplit("#", 1)[1].lower()


def race_decisions(decisions: list[dict], locality: str, facts: dict) -> list[dict]:
    """Decisions for a race page: this place's first, then the rest of the state, newest first in each group."""
    out = []
    for d in decisions:
        cited = [facts[f] for f in d["facts"] if f in facts]
        out.append(dict(d, here=d.get("locality") == locality,
                        sources=[{"url": f["source_url"], "title": f["source_title"]} for f in cited]))
    out.sort(key=lambda d: d["date"], reverse=True)
    out.sort(key=lambda d: not d["here"])
    return out


def load_who_decides(docs: dict) -> list[dict]:
    """The "Who decides?" guide, one section per state."""
    decisions = {d["id"]: d for kind, doc in docs.values() if kind == "decisions" for d in doc.get("decisions") or []}
    out = []
    for rel, (kind, doc) in sorted(docs.items()):
        if kind != "who_decides":
            continue
        issues = []
        for i in doc.get("issues") or []:
            issues.append(dict(i, anchor=anchor(i["id"]),
                               deciders=[dict(d, names=[office_name(o) for o in d["office_types"]]) for d in i["deciders"]],
                               examples=[decisions[x] for x in i.get("decisions") or [] if x in decisions]))
        out.append({"state": doc["state"], "state_name": STATE_NAMES.get(doc["state"], doc["state"]), "issues": issues})
    return out


def assemble_race(docs: dict, race: dict, facts: dict) -> dict:
    office = race["office_type"]
    race_doc = docs[race["rel"]][1]
    office_doc = docs.get(f"offices/{office}/powers.yaml", (None, None))[1]
    survey_doc = docs.get(f"offices/{office}/survey.yaml", (None, None))[1] or {}
    override_doc = docs.get(f"data/states/{race['state']}/office-overrides/{office}.yaml", (None, None))[1]
    locality_doc = None
    if race["scope"] == "localities":
        locality_doc = docs.get(
            f"data/states/{race['state']}/localities/{race['locality']}/locality.yaml", (None, None))[1]

    race_dir = race["rel"].rsplit("/", 1)[0]
    candidates = [doc for rel, (kind, doc) in docs.items()
                  if kind == "candidate" and rel.startswith(race_dir + "/candidates/")]
    candidates.sort(key=lambda c: (c["name"].casefold(), c["name"]))
    cand_dir = race_dir + "/candidates"
    for cand in candidates:
        cand.setdefault("summary", [])
        cand["glance"] = glance(cand)
        cand["record_top"] = record_top(cand, facts)
        cand["initials"] = "".join(w[0] for w in cand["name"].split()[:2]).upper()
        photo = cand.get("photo")
        cand["photo_src"] = f"/media/{cand_dir.removeprefix('data/')}/{photo['file']}" if photo else None
        cand["photo_path"] = f"{cand_dir}/{photo['file']}" if photo else None
        cand["promises"] = [
            {**entry, "promise_fact": facts.get(entry["promise"]),
             "evidence_facts": [facts.get(e) for e in entry.get("evidence") or []]}
            for entry in cand.get("promise_tracker") or []
        ]
        cand["promise_card"] = promise_score(cand["promises"]) if cand.get("incumbent") else None
        # Newest report first on the card; earlier ones stay listed (facts are never edited).
        money = sorted(cand.get("funding") or [], key=lambda f: (f.get("event_date") or "", f["id"]), reverse=True)
        cand["funding_latest"], cand["funding_earlier"] = money[:1], money[1:]

    # A power with `only_in` applies only to races in those localities or districts (e.g. one county's courts).
    def applies(power):
        return not power.get("only_in") or race["locality"] in power["only_in"]

    office_powers = [p for p in (office_doc or {}).get("powers") or [] if applies(p)]
    state_powers = [p for p in (override_doc or {}).get("powers") or [] if applies(p)]
    shown = {p["id"] for p in office_powers + state_powers}
    # A scenario is asked only where every power it rests on applies, and only in the states it was written for.
    questions = [
        {"id": f"{office}/{s['id']}", "title": s.get("title") or s["id"], "text": s["scenario"], "powers": s["powers"],
         "options": [{"id": o["id"], "text": o["text"]} for o in s["options"]]}
        for s in survey_doc.get("scenarios") or []
        if set(s["powers"]) <= shown and (not s.get("states") or race["state"].upper() in s["states"])
    ]
    payload = {
        "race": race["name"],
        "questions": [{k: q[k] for k in ("id", "title", "text", "options")} for q in questions],
        "candidates": [
            {
                "id": c["id"],
                "name": c["name"],
                "records": [
                    {k: r.get(k) for k in ("id", "record_type", "statement", "label", "claim_status",
                                           "verification", "same_event", "source_url", "source_title")}
                    for r in c.get("records") or []
                ],
                "mappings": [
                    {k: m[k] for k in ("record", "question", "options", "rationale", "verification")}
                    for m in c.get("mappings") or []
                ],
            }
            for c in candidates
        ],
    }
    plain = (override_doc or {}).get("plain_summary") or (office_doc or {}).get("plain_summary")
    if plain:  # drop bullets that only summarize powers that don't apply here
        plain = dict(plain, points=[pt for pt in plain.get("points") or []
                                    if not pt.get("powers") or set(pt["powers"]) & shown])
    all_powers = office_powers + state_powers
    decisions_doc = docs.get(f"data/states/{race['state']}/decisions/{office}.yaml", (None, None))[1] or {}
    decisions = race_decisions(decisions_doc.get("decisions") or [], race["locality"], facts)
    issues_doc = docs.get(f"data/states/{race['state']}/who-decides.yaml", (None, None))[1] or {}
    issues = [{"question": i["question"], "anchor": anchor(i["id"]),
               "role": next(d["role"] for d in i["deciders"] if office in d["office_types"])}
              for i in issues_doc.get("issues") or [] if any(office in d["office_types"] for d in i["deciders"])]
    return {
        "decisions": decisions[:DECISIONS_ON_PAGE],
        "decisions_more": max(0, len(decisions) - DECISIONS_ON_PAGE),
        "issues": issues,
        "topics": sorted({t for p in all_powers for t in p.get("topics") or []}),
        "judicial": bool((office_doc or {}).get("judicial")),
        "plain_summary": plain,
        "powers": office_powers,
        "state_powers": state_powers,
        "why_it_matters": pick_why_it_matters(race_doc, locality_doc, office_doc),
        "sources": race_doc.get("sources") or [],
        "office_level": (office_doc or {}).get("level"),
        # A race-level fact recording a campaign finance search that found no report for someone.
        "funding_check": next((f for f in race_doc.get("sources") or [] if f["id"].endswith("#S-FUND")), None),
        "candidates": candidates,
        "incumbents": [c for c in candidates if c.get("incumbent")],
        "questions": questions,
        "payload": payload,
        "facts": facts,
    }
