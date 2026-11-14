"""Draft Instagram story posts of official election results, one county at a time, for that county's Highlight.

    python src/social/results_posts.py --county harris-county [--election 2026-11-03]   # print the post files
    python src/social/results_posts.py --county harris-county --write                  # save them to social/posts/
    python src/social/results_posts.py --list                                          # counties with official results

Only result.yaml files with `status: official` are used; unofficial ones are skipped and counted. Contests are the
county ballot's local contests (tx/localities/...), in the ballot's order; statewide and district contests are left
out (one result shared by many counties would post the same record in every county's Highlight). On each slide the
candidates are alphabetical, never by votes (a measure keeps the ballot's order of choices), with each one's count
exactly as the file has it, and other write-in votes on their own line; result.yaml holds counts only, so the
percentages (of all votes shown, write-ins included) are worked out from those counts, and every slide says so. The
outcome is shown only as the file records it from the canvass, in the same words for every contest. No party
appears anywhere. A contest with more than 12 names is listed as rows, 8 to a slide.

The county's own contests (tx/localities/<county>/...) go in posts for its Highlight ("Harris Co."); the city,
school and utility district contests on its ballot go in <date>-results-<county>-cities-districts posts for the
"Results" Highlight. A contest crossing county lines is posted only from the county whose count the file holds.
One slide per contest, at most 10 per post: more contests make <date>-results-<county>-1.yaml, -2, ...
`approval: auto` only when every source is on a .gov/.us/.mil host (CLAUDE.md rule 9); otherwise `review`.
Posts are drafts: run `python src/social/build.py --check` on each, and open one pull request per post.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
MAX_SLIDES = 10
RESULT_ROWS = (1, 12)  # how many candidates build.py's `results` field takes
ROWS_PER_SLIDE = 8  # build.py's `rows` field
STATES = {"tx": "Texas", "wa": "Washington"}
AP_MONTHS = ["Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
OUTCOME_WORDS = {"elected": "Elected", "runoff": "Runoff", "passed": "Passed", "failed": "Failed"}  # as on the site
PERCENT_NOTE = ("Percent of all votes shown, write-ins included, worked out from the official counts "
                "(the report gives counts only).")


def official(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith((".gov", ".us", ".mil"))


def build_has_results() -> bool:
    """Whether build.py has the `results` slide field (a candidate list with numbers); else rows are used."""
    try:
        return "results" in social_build().SLIDE_KEYS
    except Exception:
        return False


def social_build():
    """src/social/build.py, loaded by path: another module named build (src/build/build.py) may be imported."""
    import importlib.util
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))  # build.py imports clip.py beside it
    spec = importlib.util.spec_from_file_location("social_build", here / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


def short_date(value) -> str:
    """'2026-11-03' -> 'Nov. 3, 2026' (AP style, as in the other posts)."""
    d = dt.date.fromisoformat(str(value))
    return f"{AP_MONTHS[d.month - 1]} {d.day}, {d.year}"


def find_ballot(root: Path, county: str, election: str) -> Path | None:
    hits = sorted((root / "data/states").glob(f"*/localities/{county}/elections/{election}/ballot.yaml"))
    return hits[0] if hits else None


def county_names(root: Path, ballot: Path, ballot_doc: dict) -> tuple[str, str]:
    """('Harris County', 'Harris County, Texas') from the ballot's own name."""
    state = ballot.relative_to(root / "data/states").parts[0]
    name = str(ballot_doc.get("name") or "").removesuffix(" ballot").strip() or \
        " ".join(w.capitalize() for w in ballot.parts[ballot.parts.index("localities") + 1].split("-"))
    return name, f"{name}, {STATES.get(state, state.upper())}"


def highlight_names(highlights_path: Path) -> set[str]:
    spec = load(highlights_path) or {}
    return {h["name"] for group in spec.values() for h in group or []}


def highlight_for(county_name: str, highlights_path: Path) -> str | None:
    """The county's Highlight as highlights.yaml spells it ('Harris County' -> 'Harris Co.'), or None."""
    spec = load(highlights_path) or {}
    names = {h["name"] for h in spec.get("places") or []}
    for want in (county_name.replace(" County", " Co."), county_name):
        if want in names:
            return want
    return None


def contest_doc(folder: Path) -> tuple[str, dict] | None:
    for kind in ("race", "measure"):
        doc = load(folder / f"{kind}.yaml")
        if isinstance(doc, dict):
            return kind, doc
    return None


def candidate_names(folder: Path) -> dict[str, dict]:
    out = {}
    for p in sorted((folder / "candidates").glob("*.yaml")):
        doc = load(p) or {}
        if doc.get("id"):
            out[doc["id"]] = doc
    return out


def gather(root: Path, ballot: Path, ballot_doc: dict) -> tuple[list[dict], dict[str, int]]:
    """The ballot's local contests with official results, in ballot order, plus counts of what was left out.

    A city or district crossing county lines has one result.yaml, one county's share ("Votes counted in X County
    only."): it is posted from that county's ballot only, so the same record never appears in two posts."""
    county = ballot.parts[ballot.parts.index("localities") + 1]
    name = county_names(root, ballot, ballot_doc)[0]
    skipped = {"unofficial": 0, "no_result": 0, "statewide_or_district": 0, "other_county": 0}
    contests = []
    for path in ballot_doc.get("contests") or []:
        if "/localities/" not in f"/{path}":
            skipped["statewide_or_district"] += 1
            continue
        folder = root / "data/states" / path
        result = load(folder / "result.yaml")
        if not isinstance(result, dict):
            skipped["no_result"] += 1
            continue
        if result.get("status") != "official":
            skipped["unofficial"] += 1
            continue
        if result.get("covers") == "partial" and f" {name} " not in f" {result.get('covers_note') or ''} ":
            skipped["other_county"] += 1  # another county's share: posted from that county's ballot
            continue
        found = contest_doc(folder)
        if not found:
            skipped["no_result"] += 1
            continue
        contests.append({"path": path, "folder": folder, "kind": found[0], "doc": found[1], "result": result,
                         "county_run": path.split("/")[2] == county})
    return contests, skipped


def choices(c: dict) -> list[dict]:
    """[{name, votes, key}]: candidates alphabetical by name (never by votes), a declared write-in marked after the
    name; a measure's choices in the ballot's order (FOR, AGAINST)."""
    counts = c["result"]["counts"]
    if c["kind"] == "measure":
        rows = [{"key": k, "name": str(k), "votes": counts[k]} for k in c["doc"].get("choices") or counts if k in counts]
    else:
        cands = candidate_names(c["folder"])
        rows = [{"key": k, "name": (cands.get(k) or {}).get("name") or k, "votes": v,
                 "write_in": bool((cands.get(k) or {}).get("write_in"))} for k, v in counts.items()]
    for r in rows:
        if r.get("write_in"):
            r["name"] += " (write-in)"
    if c["kind"] != "measure":
        rows.sort(key=lambda r: (r["name"].casefold(), r["name"]))
    return rows


def outcome(c: dict, rows: list[dict]) -> str | None:
    """The canvass outcome exactly as result.yaml records it, in the same words for every contest
    ("Elected: Zoe Adams", "Passed"), or None if it isn't recorded."""
    o = c["result"].get("outcome") or {}
    if not o.get("result"):
        return None
    word = OUTCOME_WORDS.get(o["result"], str(o["result"]))
    if c["kind"] == "measure":
        return word
    names = {r["key"]: r["name"] for r in rows}
    who = sorted((names.get(k, k) for k in o.get("candidates") or []), key=str.casefold)
    return f"{word}: {'; '.join(who)}" if who else word


def slides_for(c: dict, election: str, use_results: bool) -> list[dict]:
    """One slide per contest (more only when a contest lists more names than the results field takes)."""
    rows = choices(c)
    res = c["result"]
    won = outcome(c, rows)
    other = res.get("other_votes") or 0
    as_of = short_date(res["as_of"])
    base = {"chip": "Official results", "kicker": f"{short_date(election)} election", "head": c["doc"]["name"]}
    if use_results and RESULT_ROWS[0] <= len(rows) <= RESULT_ROWS[1]:
        r = {"contest": "Votes by choice" if c["kind"] == "measure" else "Votes by candidate", "status": "official",
             "as_of": as_of, "candidates": [{"name": x["name"], "votes": x["votes"]} for x in rows]}
        if c["kind"] == "measure":
            r["measure"] = True
        if other:
            r["other_votes"] = other
        r["outcome"] = won  # dropped below when not recorded
        if res.get("covers_note"):
            r["note"] = str(res["covers_note"])
        slide = {**base, "results": {k: v for k, v in r.items() if v is not None}, "by": PERCENT_NOTE}
        if not won:
            slide["text"] = "Canvass outcome: not recorded yet."
        return [slide]
    # rows: [name, "votes · percent"], the same denominator as the results field (all votes shown)
    lines = [[x["name"], x["votes"]] for x in rows] + ([["Other write-ins", other]] if other else [])
    total = sum(v for _, v in lines)
    lines = [[n, f"{v:,} votes · {100 * v / total if total else 0:.1f}%"] for n, v in lines]
    text = " ".join(filter(None, [f"Canvass outcome: {won}." if won else "Canvass outcome: not recorded yet.",
                                  res.get("covers_note") and str(res["covers_note"])]))
    out, size = [], ROWS_PER_SLIDE
    for i in range(0, max(len(lines), 1), size):
        part = len(lines) > size and f" ({i // size + 1} of {-(-len(lines) // size)})" or ""
        out.append({**base, "head": base["head"] + part, "rows": lines[i:i + size],
                    "text": text, "by": f"Official results as of {as_of}. {PERCENT_NOTE}"})
    return out


def sources_for(contests: list[dict]) -> list[dict]:
    """Each contest's result source once: title, URL, and the file's retrieved date (as_of if none)."""
    out, seen = [], set()
    for c in contests:
        for s in c["result"].get("sources") or []:
            if s.get("source_url") and s["source_url"] not in seen:
                seen.add(s["source_url"])
                out.append({"title": s.get("source_title"), "url": s["source_url"],
                            "retrieved": str(s.get("retrieved") or c["result"]["as_of"])})
    return out


def post_for(cs, slides, highlight, whose, place, county, election, ballot, root, part) -> dict:
    sources = sources_for(cs)
    partial = [c["doc"]["name"] for c in cs if c["result"].get("covers") == "partial"]
    state = ballot.relative_to(root / "data/states").parts[0]
    return {
        "kind": "story", "level": "local", "place": place, "topic": "Election results",
        "approval": "auto" if sources and all(official(s["url"]) for s in sources) else "review",
        "layout": "card", "highlight": highlight, "sources": sources, "slides": slides,
        "caption": (f"Official results for {whose} contests on the {short_date(election)} ballot in {place}{part}, "
                    "from the canvass. Contests are in ballot order, candidates in alphabetical order. Percentages "
                    "are of all votes shown, write-ins included, worked out from the official vote counts."),
        "hashtags": [county.replace("-", ""), "electionresults", STATES.get(state, "elections").lower()],
        "record_ids": [record_id(c) for c in cs],
        "notes": " ".join(filter(None, [
            f"Drafted by src/social/results_posts.py from {len(cs)} result.yaml files (status: official).",
            "The result files list counts only, so the percentages are computed (said on every slide).",
            f"Partial counts (crossing county lines; the slide says which county): {'; '.join(partial)}." if partial else "",
            "Check each outcome line against the canvass before merging.",
        ])),
    }


def record_id(c: dict) -> str:
    return f"{c['path']}/result/official"


def drafts(root: Path, county: str, election: str, today: str, highlights_path: Path,
           use_results: bool | None = None) -> tuple[list[tuple[str, dict]], list[str]]:
    """[(file name, post)] and notes for the person running it."""
    ballot = find_ballot(root, county, election)
    if not ballot:
        raise SystemExit(f"no ballot.yaml for {county} on {election}")
    ballot_doc = load(ballot) or {}
    name, place = county_names(root, ballot, ballot_doc)
    contests, skipped = gather(root, ballot, ballot_doc)
    use_results = build_has_results() if use_results is None else use_results
    notes = [f"{name}: {len(contests)} local contests with official results; skipped {skipped['unofficial']} unofficial, "
             f"{skipped['no_result']} with no result yet, {skipped['statewide_or_district']} statewide/district contests, "
             f"{skipped['other_county']} counted in another county (posted from that county)."]
    if not use_results:
        notes.append("build.py has no `results` slide field here: candidates are listed as rows instead.")
    names = highlight_names(highlights_path)
    highlight = highlight_for(name, highlights_path)
    if not highlight:
        highlight = "Local"
        notes.append(f"No Highlight named for {name} in social/highlights.yaml: using 'Local'; a "
                     f"'{name.replace(' County', ' Co.')}' Highlight could be added.")
    others = "Results" if "Results" in names else "Local"
    if others == "Local":
        notes.append("No 'Results' Highlight in social/highlights.yaml: city and district contests use 'Local'.")
    posts = []
    # the county's own contests go in its Highlight; cities, school and utility districts in "Results"
    for county_run, stem, hl, whose in ((True, f"{today}-results-{county}", highlight, f"{name}'s own"),
                                        (False, f"{today}-results-{county}-cities-districts", others,
                                         f"city and district")):
        groups: list[list[tuple[dict, list[dict]]]] = [[]]
        for c in (c for c in contests if c["county_run"] == county_run):
            sl = slides_for(c, election, use_results)
            if sum(len(x) for _, x in groups[-1]) + len(sl) > MAX_SLIDES:  # at most 10 slides; contests stay whole
                groups.append([])
            groups[-1].append((c, sl))
        groups = [g for g in groups if g]
        for k, g in enumerate(groups, 1):
            posts.append((stem + (f"-{k}" if len(groups) > 1 else "") + ".yaml",
                          post_for([c for c, _ in g], [x for _, sl in g for x in sl], hl, whose, place, county, election,
                                   ballot, root, f" (part {k} of {len(groups)})" if len(groups) > 1 else "")))
    return posts, notes


def county_list(root: Path, election: str) -> list[tuple[str, int, int, int]]:
    """(county, official, unofficial, local contests) for every county ballot on that date."""
    out = []
    for ballot in sorted((root / "data/states").glob(f"*/localities/*/elections/{election}/ballot.yaml")):
        county = ballot.parts[ballot.parts.index("localities") + 1]
        contests, skipped = gather(root, ballot, load(ballot) or {})
        out.append((county, len(contests), skipped["unofficial"], len(contests) + skipped["unofficial"] + skipped["no_result"] + skipped["other_county"]))
    return out


def dump(post: dict) -> str:
    return yaml.safe_dump(post, sort_keys=False, allow_unicode=True, width=10000, default_flow_style=None)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--county", help="county slug, e.g. harris-county")
    ap.add_argument("--election", default="2026-11-03")
    ap.add_argument("--list", action="store_true", help="counties with how many contests have official results")
    ap.add_argument("--write", action="store_true", help="save to --out instead of printing")
    ap.add_argument("--out", type=Path, default=ROOT / "social/posts")
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="the post files' date (default today)")
    ap.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)  # tests: a fixture tree
    ap.add_argument("--highlights", type=Path, default=ROOT / "social/highlights.yaml", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.list:
        rows = county_list(args.root, args.election)
        for county, off, unoff, local in rows:
            print(f"{county}: {off} official, {unoff} unofficial, of {local} local contests")
        print(f"{sum(r[1] > 0 for r in rows)} of {len(rows)} counties have official results")
        return 0
    if not args.county:
        ap.error("--county or --list")
    posts, notes = drafts(args.root, args.county, args.election, args.date, args.highlights)
    for n in notes:
        print(f"# {n}", file=sys.stderr)
    if not posts:
        print("# no posts: no official results yet", file=sys.stderr)
    for name, post in posts:
        if args.write:
            path = args.out / name
            if path.exists():
                print(f"SKIP {path}: already exists", file=sys.stderr)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dump(post), encoding="utf-8")
            print(f"wrote {path} ({len(post['slides'])} slides, approval {post['approval']})")
        else:
            print(f"---\n# {name}\n{dump(post)}", end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
