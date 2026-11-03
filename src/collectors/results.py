"""Election results from an official results export into result.yaml files, for one ballot's contests.

Usage:
  python src/collectors/results.py (--ballot data/states/tx/localities/harris-county/elections/2026-11-03/ballot.yaml
                                    | --statewide TX 2026-11-03) \
      --file <export.csv|.json|.pdf> --source-url URL --source-title TITLE --as-of YYYY-MM-DD \
      [--status unofficial|official] [--source-kind page|document] [--whole PATH ...] [--map "CONTEST=PATH" ...] [--write]

Without --write it only prints what it would write.

The export is a table of contest, choice, and votes (CSV with a header row, or JSON: a list of such rows, or
{"contests": [{"name", "choices": [{"name", "votes"}]}]}). Column names are matched loosely ("Contest Name",
"Race", "Choice", "Candidate", "Total Votes", ...), so a Clarity ENR "summary.csv" reads as is. A PDF is read as a
Hart "Cumulative Report" or ES&S "Summary Results Report" (parse_hart_text).

How contests are matched to the ballot (never by guessing):
  - a race: the ballot race whose candidate names match the contest's choices (every listed candidate found,
    write-ins aside). Office titles are printed differently everywhere; names are not.
  - a measure: the ballot measure whose name has the same proposition label and place words.
  - --map "Contest as printed=tx/localities/x/elections/2026-11-03/slug" settles anything else by hand.
Unmatched contests and choices are listed, never written.

What gets written, per matched contest (CLAUDE.md rules 2, 3, 3a):
  - counts by candidate id (or by measure choice as printed); write-in totals go to other_votes; over/under votes
    are left out;
  - status and as_of; covers "whole" for the county's own contests and any --whole path, else "partial" with a
    note naming the county (a city or district that crosses county lines). With --ballot, statewide and district
    races are skipped unless listed in --whole: their totals come from the Secretary of State (--statewide);
  - one source fact (S-01, S-02, ... a new ID each update), written unverified.
An official or verified result is never replaced, a whole count never by a partial one (a county's share of a
statewide race), and an unofficial one only by a report at least as new. --statewide TX DATE matches every statewide
and district contest instead of one county's ballot, and counts them in full (the Secretary of State's report).
No outcome is written: who was elected (or whether a measure passed) is added by hand from the canvass.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import re
import sys
import unicodedata
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "collectors"))
from yamlio import to_yaml  # noqa: E402

CONTEST_COLS = ("contest name", "contest", "race", "office", "contest title")
CHOICE_COLS = ("choice name", "choice", "candidate name", "candidate", "selection", "name")
VOTES_COLS = ("total votes", "votes", "total", "vote count", "count")
SKIP_CHOICES = re.compile(r"(?i)^(over\s*votes?|under\s*votes?|overvotes?|undervotes?|blank|registered voters|ballots cast|"
                          r"total votes cast|not assigned|cast votes:?)$")
WRITE_IN_TOTAL = re.compile(r"(?i)^write[\s-]*in totals?$")
WRITE_IN = re.compile(r"(?i)write[\s-]*in|\(w\)\s*$")


def key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\([^)]*\)|\"[^\"]*\"|'[^']*'", " ", s)  # party tags and nicknames
    return re.sub(r"[^a-z0-9]", "", s)


def name_keys(name: str) -> set[str]:
    """Ways a candidate's name may be printed: full, and first + last (middle names, initials, suffixes dropped)."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", unicodedata.normalize("NFKD", name)
                                   .encode("ascii", "ignore").decode())
             if w.lower().rstrip(".") not in ("jr", "sr", "ii", "iii", "iv")]
    out = {key(name)}
    if len(words) >= 2:
        out.add(key(words[0] + words[-1]))
    return out


PARTY_CODE = re.compile(r"^(?:REP|DEM|LIB|GRN|IND|NP)\s+")
COUNT = re.compile(r"^\d{1,3}(?:,\d{3})*$")
REPORT_NOISE = re.compile(r"(?i)(cumulative report|summary results report|election summary -|total number of voters|"
                          r"^page \d|page \d+ of|votes:|precincts|counted|registered|ballots cast|voter turnout|"
                          r"party candidate|candidate party|^(?:election|early|absentee|total|party|candidate)(?:\s|$))")


VOTE_FOR = re.compile(r"(.*?),?\s*Vote For \d+\s*$")
TITLE_STOP = re.compile(r"(?i)^(under\s*votes?:?|undervotes)(\s|$)")


def parse_hart_text(text: str) -> list[tuple[str, str, int]]:
    """(contest, choice, votes) from the text of a county results report: Hart InterCivic "Cumulative Report" (Harris and
    many Texas counties) or ES&S Electionware "Summary Results Report" (Dallas, Bexar, Williamson, ...).

    A contest's title ends at its "Vote For N" line: either on that line (Hart) or on the lines just above it (ES&S, or a
    wrapped title), back to the previous contest's under-vote line. Each choice line has the choice name and its counts
    by voting method plus the total; the text extractor may shuffle the numbers, so the total is the largest count on the
    line (every other count is part of it). Cast, over, and under vote lines are skipped.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not REPORT_NOISE.search(ln.strip())]
    # a text line repeated on many pages (report name, election, county, column headings) is page furniture
    seen: dict[str, int] = {}
    for ln in lines:
        seen[ln] = seen.get(ln, 0) + 1
    lines = [ln for ln in lines if seen[ln] < 3 or VOTE_FOR.match(ln) or re.search(r"\d", ln)]
    titles, title_lines = {}, set()
    for i, line in enumerate(lines):
        if not (m := VOTE_FOR.match(line)):
            continue
        before, j = [], i - 1
        if not m[1] or (j >= 0 and lines[j].endswith(",")):
            while j >= 0 and len(before) < 3 and not TITLE_STOP.match(lines[j]) and not VOTE_FOR.match(lines[j]) \
                    and not (before and re.search(r"\d", lines[j]) and re.search(r"[A-Za-z]", lines[j])
                             and not lines[j].endswith(",")):
                before.insert(0, lines[j])
                j -= 1
        title_lines.update(range(j + 1, i + 1))
        titles[i] = " ".join(before + [m[1]]).strip(" ,")
    rows, contest = [], None
    for i, line in enumerate(lines):
        if i in titles:
            contest = titles[i]
            continue
        if i in title_lines:
            continue
        tokens = line.split()
        counts = [int(t.replace(",", "")) for t in tokens if COUNT.match(t)]
        name = " ".join(t for t in tokens if not COUNT.match(t) and not t.endswith("%")).strip()
        if counts and name and contest:
            rows.append((contest, PARTY_CODE.sub("", name), max(counts)))
    return rows


def read_rows(path: Path) -> list[tuple[str, str, int]]:
    """(contest, choice, votes) rows from a CSV, JSON, or Hart cumulative-report PDF export."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        return parse_hart_text("\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages))
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and "contests" in data:
            return [(c["name"], ch["name"], int(ch["votes"])) for c in data["contests"] for ch in c["choices"]]
        rows = [{k.lower().strip(): v for k, v in r.items()} for r in data]
    else:
        rows = [{(k or "").lower().strip(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]
    if not rows:
        return []

    def col(names):
        found = next((n for n in names if n in rows[0]), None)
        if not found:
            raise SystemExit(f"{path}: no column named any of {names}; columns are {list(rows[0])}")
        return found

    c, ch, v = col(CONTEST_COLS), col(CHOICE_COLS), col(VOTES_COLS)
    return [(r[c].strip(), r[ch].strip(), int(str(r[v]).replace(",", "") or 0)) for r in rows if r[c] and r[ch]]


def statewide_contests(root: Path, state: str, date: str) -> list[str]:
    """Every statewide and district contest for one election (what the Secretary of State reports in full)."""
    base = root / "data/states" / state
    folders = [*(base / "statewide/elections" / date).glob("*"), *base.glob(f"districts/*/elections/{date}/*")]
    return sorted(f.relative_to(root / "data/states").as_posix() for f in folders
                  if (f / "race.yaml").exists() or (f / "measure.yaml").exists())


def load_ballot(ballot: Path | None, root: Path = REPO, paths: list[str] | None = None) -> list[dict]:
    """The ballot's contests (or the given contest paths): path, kind, name, election date, and candidates."""
    rels = paths if paths is not None else yaml.safe_load(ballot.read_text(encoding="utf-8"))["contests"]
    out = []
    for rel in rels:
        folder = root / "data/states" / rel
        if (folder / "race.yaml").exists():
            race = yaml.safe_load((folder / "race.yaml").read_text(encoding="utf-8"))
            cands = [yaml.safe_load(f.read_text(encoding="utf-8")) for f in sorted((folder / "candidates").glob("*.yaml"))]
            out.append({"path": rel, "kind": "race", "name": race["name"], "election_date": race["election_date"],
                        "candidates": [{"id": c["id"], "name": c["name"], "write_in": bool(c.get("write_in"))}
                                       for c in cands]})
        else:
            m = yaml.safe_load((folder / "measure.yaml").read_text(encoding="utf-8"))
            out.append({"path": rel, "kind": "measure", "name": m["name"], "election_date": m["election_date"],
                        "choices": m["choices"]})
    return out


def match_choices(contest: dict, choices: list[str]) -> tuple[dict, list[str], list[str]] | None:
    """{choice as printed: candidate id or measure choice}, write-in choices, unknown choices; None if no fit."""
    named = [c for c in choices if not SKIP_CHOICES.match(c) and not WRITE_IN.search(c)]
    writeins = [c for c in choices if WRITE_IN.search(c)]
    found, unknown = {}, []
    if contest["kind"] == "measure":
        by_key = {key(c): c for c in contest["choices"]}
        for c in named:
            k = key(c)
            hit = by_key.get(k) or next((v for kk, v in by_key.items() if kk.startswith(k) or k.startswith(kk)), None)
            (found.__setitem__(c, hit) if hit else unknown.append(c))
        return (found, writeins, unknown) if set(found.values()) == set(contest["choices"]) else None
    listed = [c for c in contest["candidates"] if not c["write_in"]]
    for c in named:
        k = name_keys(c)
        hits = [x for x in contest["candidates"] if k & name_keys(x["name"])]
        (found.__setitem__(c, hits[0]["id"]) if len(hits) == 1 else unknown.append(c))
    if not listed or {x["id"] for x in listed} - set(found.values()):
        return None
    return found, writeins, unknown


PROP = re.compile(r"(?i)\b(?:prop(?:osition)?|measure|propuesta)\s*(?:no\.?\s*)?([a-z0-9]{1,3})\b")


def measure_fits(contest: dict, title: str) -> bool:
    a, b = PROP.search(contest["name"]), PROP.search(title)
    if not (a and b) or a[1].lower() != b[1].lower():
        return False
    place = [w for w in re.findall(r"[a-z]+", contest["name"].lower().split("proposition")[0])
             if w not in ("city", "of", "the", "county", "isd", "district", "town", "village")]
    return all(w in title.lower() for w in place)


def plan(contests: list[dict], rows: list[tuple[str, str, int]], manual: dict[str, str]) -> tuple[list, list[str]]:
    by_title: dict[str, list[tuple[str, int]]] = {}
    for title, choice, votes in rows:
        by_title.setdefault(title, []).append((choice, votes))
    by_path = {c["path"]: c for c in contests}
    matched, problems, used = [], [], {}
    for title, choices in by_title.items():
        names = [c for c, _ in choices]
        if title in manual:
            cands = [by_path[manual[title]]] if manual[title] in by_path else []
            if not cands:
                problems.append(f"--map: {manual[title]} is not on this ballot")
                continue
        else:
            cands = [c for c in contests if (c["kind"] == "race" or measure_fits(c, title))]
        fits = [(c, m) for c in cands if (m := match_choices(c, names))]
        if len(fits) != 1:
            problems.append(f"{'NO MATCH' if not fits else 'AMBIGUOUS'}: {title} {names[:6]}")
            continue
        contest, (found, writeins, unknown) = fits[0]
        if contest["path"] in used:
            problems.append(f"DUPLICATE: {title} and {used[contest['path']]} both match {contest['path']}")
            continue
        used[contest["path"]] = title
        if unknown:
            problems.append(f"UNKNOWN CHOICES in {title}: {unknown} (left out of {contest['path']})")
        votes = dict(choices)
        counts = {}
        for printed, target in found.items():
            counts[target] = counts.get(target, 0) + votes[printed]
        matched.append({"contest": contest, "title": title, "counts": counts,
                        # a "Write-In Totals" line already sums the write-in lines listed under it (ES&S)
                        "other_votes": next((votes[w] for w in writeins if WRITE_IN_TOTAL.match(w)),
                                            sum(votes[w] for w in writeins))})
    return matched, problems


def county_name(ballot: Path) -> tuple[str, str]:
    locality = ballot.parts[ballot.parts.index("localities") + 1]
    return locality, " ".join(w.capitalize() for w in locality.split("-"))


def result_doc(m: dict, args, locality: str, county: str, n: int, sha: str | None) -> dict:
    contest = m["contest"]
    whole = (locality is None or contest["path"].startswith(f"tx/localities/{locality}/")
             or contest["path"] in (args.whole or []))
    doc = {"election_date": contest["election_date"], "status": args.status, "as_of": args.as_of,
           "covers": "whole" if whole else "partial"}
    if not whole:
        doc["covers_note"] = f"Votes counted in {county} only."
    doc["counts"] = m["counts"]
    if m["other_votes"]:
        doc["other_votes"] = m["other_votes"]
    doc["sources"] = [{
        "id": f"{contest['path']}/result#S-{n:02d}",
        "statement": f"{args.source_title} lists these vote counts for \"{m['title']}\" ({args.status}, as of {args.as_of}).",
        "label": "official_record", "claim_status": "documented", "contradicted_by": [],
        "source_url": args.source_url, "source_title": args.source_title, "source_kind": args.source_kind,
        "sha256": sha, "archive_url": None, "event_date": contest["election_date"],
        "retrieved": args.retrieved, "verification": "unverified", "verified_on": None, "reviewer": None,
    }]
    return doc


def next_number(old: dict | None) -> int:
    nums = [int(m[1]) for s in (old or {}).get("sources") or [] if (m := re.search(r"#S-(\d+)$", s["id"]))]
    return max(nums, default=0) + 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--ballot", type=Path, help="a county's ballot.yaml (the county's own results report)")
    where.add_argument("--statewide", nargs=2, metavar=("STATE", "DATE"),
                       help="every statewide and district contest, counted in full (the Secretary of State's report)")
    ap.add_argument("--file", type=Path, required=True, help="the results export (CSV, JSON, or Hart PDF)")
    ap.add_argument("--source-url", required=True)
    ap.add_argument("--source-title", required=True)
    ap.add_argument("--source-kind", choices=("page", "document"), default="page")
    ap.add_argument("--status", choices=("unofficial", "official"), default="unofficial")
    ap.add_argument("--as-of", required=True, help="date of the results report (YYYY-MM-DD)")
    ap.add_argument("--retrieved", default=dt.date.today().isoformat())
    ap.add_argument("--whole", nargs="*", help="contest paths this report counts in full (e.g. a city inside the county)")
    ap.add_argument("--map", nargs="*", default=[], help='"Contest as printed=contest path" for contests matched by hand')
    ap.add_argument("--root", type=Path, default=REPO, help="data repo root (default: this repo)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    manual = dict(m.split("=", 1) for m in args.map)
    if args.ballot:
        ballot = args.ballot if args.ballot.is_absolute() else root / args.ballot
        # statewide and district races come in full from the Secretary of State (--statewide), not one county's share
        contests = [c for c in load_ballot(ballot, root)
                    if c["path"].split("/")[1] == "localities" or c["path"] in (args.whole or [])]
        locality, county = county_name(ballot)
    else:
        contests = load_ballot(None, root, statewide_contests(root, args.statewide[0].lower(), args.statewide[1]))
        locality, county = None, None
    matched, problems = plan(contests, read_rows(args.file), manual)
    sha = hashlib.sha256(args.file.read_bytes()).hexdigest() if args.source_kind == "document" else None
    wrote = skipped = 0
    for m in matched:
        target = root / "data/states" / m["contest"]["path"] / "result.yaml"
        old = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None
        if old:
            verified = any(s.get("verification") == "verified" for s in old.get("sources") or [])
            new = result_doc(m, args, locality, county, 1, None)
            other_county = old["covers"] == "partial" and new["covers"] == "partial" and old["covers_note"] != new["covers_note"]
            if verified or (old["covers"] == "whole" and new["covers"] == "partial") or other_county \
                    or (old["status"] == "official" and args.status == "unofficial") or str(old["as_of"]) > args.as_of:
                skipped += 1
                print(f"KEEP {m['contest']['path']}: has a {old['status']} {old['covers']} result as of {old['as_of']}"
                      f"{' (verified)' if verified else ''}")
                continue
        doc = result_doc(m, args, locality, county, next_number(old), sha)
        text = "# Vote counts from an official results report. Unverified.\n" + to_yaml(doc)
        print(f"{'WRITE' if args.write else 'WOULD WRITE'} {target.relative_to(root).as_posix()}  "
              f"{', '.join(f'{k}={v}' for k, v in m['counts'].items())}")
        if args.write:
            target.write_text(text, encoding="utf-8")
        wrote += 1
    for p in problems:
        print(p)
    print(f"{len(matched)} contests matched, {wrote} {'written' if args.write else 'to write'}, {skipped} kept; "
          f"{len(problems)} problem(s); {len(contests) - len(matched)} ballot contests not in this report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
