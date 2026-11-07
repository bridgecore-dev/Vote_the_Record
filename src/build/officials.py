"""Officials pages (docs/OFFICIALS.md): seats by place, and one profile per person.

Like model.py, this only reads and arranges validated data. A person's profile shows their own
facts and, unchanged, the facts in the candidate files they are linked to.
"""

from __future__ import annotations

import re

from model import LEVELS, LEVEL_RANK, STATE_NAMES, promise_score, record_count_text, title_from_slug

DISTRICT_NAMES = [
    (r"us-house-(\d+)", "U.S. House District {}"),
    (r"state-house-(\d+)", "{state} House District {}"),
    (r"state-senate-(\d+)", "{state} Senate District {}"),
    (r"sboe-(\d+)", "State Board of Education District {}"),
    (r"court-of-appeals-(\d+)", "Court of Appeals, District {}"),
    (r"judicial-district-(\d+)", "Judicial District {}"),
]
UPPER = {"isd", "cisd", "mud", "mmd", "esd", "wcid", "fwsd", "sud", "pud", "ud", "rud", "pid", "lid", "gcd", "cad",
         "sd", "ii", "iii"}
# Within a level: the governing body first, then other elected offices, then the courts.
FIRST = ["governor", "lieutenant-governor", "attorney-general", "comptroller", "land-commissioner",
         "agriculture-commissioner", "railroad-commissioner", "us-senator", "us-representative", "state-senator",
         "state-representative", "sboe-member", "county-judge", "county-commissioner", "sheriff", "county-attorney",
         "district-attorney", "criminal-district-attorney", "county-clerk", "district-clerk", "tax-assessor-collector",
         "county-treasurer", "constable", "justice-of-the-peace", "mayor", "city-council-member"]
JUDGES = re.compile(r"judge|justice|cca-")


def seat_order(seat: dict) -> tuple:
    kind = seat["office_type"]
    group = 0 if kind in FIRST else 2 if JUDGES.search(kind) else 1
    natural = [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", seat["name"].casefold())]
    return (LEVEL_RANK.get(seat["level"], 0), group, FIRST.index(kind) if kind in FIRST else 0, natural)


RUNNING_ON = {"policy_proposals": "Campaign proposal", "attack_messaging": "Campaign message about an opponent",
              "contested_claims": "Contested campaign claim"}


def place_name(scope: str, slug: str, state: str) -> str:
    state_name = STATE_NAMES.get(state.upper(), state.upper())
    if scope == "statewide":
        return f"{state_name} statewide offices"
    if scope == "districts":
        for pattern, text in DISTRICT_NAMES:
            if m := re.fullmatch(pattern, slug):
                return text.format(m[1], state=state_name)
    return " ".join(w.upper() if w in UPPER else w for w in title_from_slug(slug).split())


def office_rows(holders: list[dict], today: str) -> tuple[list[dict], list[dict]]:
    """(current, past) holders: those with no end, or an end not yet reached, hold it now."""
    current = [h for h in holders if not h["end"] or h["end"] >= today]
    return current, [h for h in holders if h not in current]


HOW = {"yeas": "Voted yes", "nays": "Voted no", "present_not_voting": "Present, not voting",
       "absent": "Absent", "absent_excused": "Absent, excused", "absent_excused_committee": "Absent, excused (committee meeting)"}
CHAMBER = {"house": "House", "senate": "Senate"}


def legislature_votes(docs: dict) -> dict[tuple[str, str], list[dict]]:
    """(state, person) -> their final-passage votes on bills that became law, newest first.

    Names are matched only through the session's members file, exactly as the journal prints them. A Senate
    vote printed only as a tally counts a senator as yes only when it is marked every_member, and only a senator
    on the session's printed roll (senate_roll) who isn't named as a nay.
    """
    members: dict[tuple[str, str, str], dict[str, str]] = {}  # (state, session, chamber) -> printed -> person
    roster: dict[tuple[str, str, str], list[str]] = {}
    for rel, (kind, doc) in docs.items():
        if kind == "legislature_members":
            state, folder = rel.split("/")[2], rel.split("/")[5]
            for m in doc["members"]:
                if m["person"]:
                    for name in m["printed"]:
                        members.setdefault((state, folder, m["chamber"]), {})[name] = m["person"]
            if doc.get("senate_roll"):
                roster[(state, folder, "senate")] = doc["senate_roll"]["names"]
    out: dict[tuple[str, str], list[dict]] = {}
    for rel, (kind, doc) in docs.items():
        if kind != "legislature_bill":
            continue
        state, folder = rel.split("/")[2], rel.split("/")[5]
        for v in doc["votes"]:
            key = (state, folder, v["chamber"])
            how: dict[str, str] = {}
            for cat, names in (v["names"] or {}).items():
                for name in names:
                    if name in members.get(key, {}):
                        how[members[key][name]] = cat
            if v["every_member"]:
                for name in roster.get(key, []):
                    if name in members.get(key, {}):
                        how.setdefault(members[key][name], "yeas")
            tally = v["tally"]
            result = (f"{'Passed' if v['action'].startswith('passed') else 'Approved in'} the {CHAMBER[v['chamber']]} "
                      f"{tally['yeas']}-{tally['nays']}") if tally else f"No record vote in the {CHAMBER[v['chamber']]}"
            for person, cat in how.items():
                out.setdefault((state, person), []).append({
                    "bill": doc["bill"], "caption": doc["caption"], "session": doc["session"], "how": HOW[cat],
                    "cat": cat, "action": v["action"], "date": v["date"], "result": result, "fact": v["fact"],
                    "chamber": CHAMBER[v["chamber"]]})
    for votes in out.values():
        votes.sort(key=lambda r: (r["date"], r["bill"]), reverse=True)
    return out


def leg_summary(votes: list[dict]) -> dict | None:
    if not votes:
        return None
    counts = {label: sum(v["how"] == label for v in votes) for label in dict.fromkeys(HOW.values())}
    return {"total": len(votes), "bills": len({(v["session"], v["bill"]) for v in votes}),
            "counts": {k: n for k, n in counts.items() if n}}


def assemble_officials(docs: dict, races: list[dict], facts: dict, today: str) -> dict:
    people = {}
    for rel, (kind, doc) in docs.items():
        if kind == "person":
            state = rel.split("/")[2]
            people[(state, doc["id"])] = {**doc, "state": state, "url": f"/people/{state}/{doc['id']}/",
                                          "seats": [], "past_seats": []}
    races_by_path = {r["path"]: r for r in races}

    places: dict[str, dict] = {}
    for rel, (kind, doc) in docs.items():
        if kind != "office":
            continue
        parts = rel.split("/")
        state, scope = parts[2], parts[3]
        slug = "statewide" if scope == "statewide" else parts[4]
        office_doc = docs.get(f"offices/{doc['office_type']}/powers.yaml", (None, None))[1] or {}
        key = f"{state}/{slug}"
        place = places.setdefault(key, {
            "state": state, "slug": slug, "scope": scope, "name": place_name(scope, slug, state),
            "state_name": STATE_NAMES.get(state.upper(), state.upper()),
            "url": f"/officials/{state}/{slug}/", "seats": [], "inside": [], "counties": []})
        current, past = office_rows(doc["holders"], today)
        own = {s["id"]: s for s in doc["sources"]}
        seat = {"name": doc["name"], "office_type": doc["office_type"], "vacant": doc.get("vacant", False),
                "level": office_doc.get("level") or "local", "notes": doc.get("notes"), "place": place,
                "sources": doc["sources"], "rel": rel,
                "current": [{**h, "who": people[(state, h["person"])], "basis_fact": own[h["basis"]]} for h in current],
                "past": [{**h, "who": people[(state, h["person"])], "basis_fact": own[h["basis"]]} for h in past]}
        place["seats"].append(seat)
        for h in seat["current"]:
            h["who"]["seats"].append(seat)
        for h in seat["past"]:
            h["who"]["past_seats"].append(seat)

    # Which county a city or district sits in, from the ballots that carried its contests.
    for rel, (kind, doc) in docs.items():
        if kind != "ballot":
            continue
        state, county = rel.split("/")[2], rel.split("/")[4]
        for path in doc["contests"]:
            bits = path.split("/")
            if bits[1] == "localities" and bits[2] != county and f"{state}/{bits[2]}" in places \
                    and f"{state}/{county}" in places:
                inner, outer = places[f"{state}/{bits[2]}"], places[f"{state}/{county}"]
                if inner not in outer["inside"]:
                    outer["inside"].append(inner)
                if outer not in inner["counties"]:
                    inner["counties"].append(outer)

    for place in places.values():
        place["seats"].sort(key=seat_order)
        place["inside"].sort(key=lambda p: p["name"].casefold())
        place["counties"].sort(key=lambda p: p["name"].casefold())
        place["groups"] = [{"title": title, "seats": [s for s in place["seats"] if s["level"] == key]}
                           for key, title, _ in LEVELS if any(s["level"] == key for s in place["seats"])]
        place["people_count"] = len({h["person"] for s in place["seats"] for h in s["current"]})

    for (state, slug), votes in legislature_votes(docs).items():
        if (state, slug) in people:
            people[(state, slug)]["leg_votes"] = votes

    for person in people.values():
        cands = []
        for path in person["candidacies"]:
            cand = docs[f"data/states/{path}.yaml"][1]
            race = races_by_path.get(path.rsplit("/candidates/", 1)[0])
            result = race and race.get("result")
            row = next((r for r in (result or {}).get("rows") or [] if r["key"] == cand["id"]), None)
            cands.append({"cand": cand, "race": race, "result": result, "row": row})
        records = list(person["records"]) + [r for c in cands for r in c["cand"].get("records") or []]
        records.sort(key=lambda r: r.get("event_date") or "", reverse=True)
        records.sort(key=lambda r: r["record_type"] != "vote")  # votes first, newest first within each group
        said = sorted(person["statements"], key=lambda f: f.get("event_date") or "", reverse=True)
        campaign = sorted(
            [{**f, "said_as": RUNNING_ON[section], "race_name": c["race"]["name"] if c["race"] else ""}
             for c in cands for section in RUNNING_ON for f in (c["cand"].get("running_on") or {}).get(section) or []],
            key=lambda f: f.get("event_date") or "", reverse=True)
        tracker = list(person["promise_tracker"]) + [e for c in cands for e in c["cand"].get("promise_tracker") or []]
        promises = [{**e, "promise_fact": facts.get(e["promise"]),
                     "evidence_facts": [facts.get(x) for x in e.get("evidence") or []]} for e in tracker]
        money = sorted(list(person["funding"]) + [f for c in cands for f in c["cand"].get("funding") or []],
                       key=lambda f: (f.get("event_date") or "", f["id"]), reverse=True)
        summary = list(person.get("summary") or []) + [f for c in cands for f in c["cand"].get("summary") or []]
        person.update({
            "candidacy_rows": cands, "all_records": records, "record_summary": record_count_text(records),
            "said": said, "campaign": campaign, "promises": promises,
            "promise_card": promise_score(promises) if promises else None,
            "money": money, "summary_facts": summary,
            "leg_votes": person.get("leg_votes") or [],
            "leg_summary": leg_summary(person.get("leg_votes") or []),
            "researched": person.get("research") != "pending" or bool(records or said or campaign or money
                                                                      or person.get("leg_votes")),
            "initials": "".join(w[0] for w in person["name"].split()[:2]).upper(),
        })
        person["seats"].sort(key=lambda s: LEVEL_RANK.get(s["level"], 0))

    states = {}
    for place in places.values():
        states.setdefault(place["state"], []).append(place)
    index = []
    for state, members in sorted(states.items()):
        members.sort(key=lambda p: ({"statewide": 0, "districts": 1}.get(p["scope"], 2), p["name"].casefold()))
        counties = [p for p in members if p["scope"] == "localities" and p["slug"].endswith("-county")]
        index.append({"state": state, "state_name": STATE_NAMES.get(state.upper(), state.upper()),
                      "statewide": [p for p in members if p["scope"] == "statewide"],
                      "districts": [p for p in members if p["scope"] == "districts"],
                      "counties": counties,
                      "others": [p for p in members if p["scope"] == "localities" and p not in counties
                                 and not p["counties"]],
                      "seat_count": sum(len(p["seats"]) for p in members),
                      "people_count": sum(1 for (s, _), _p in people.items() if s == state)})
    return {"places": list(places.values()), "people": list(people.values()), "index": index}
