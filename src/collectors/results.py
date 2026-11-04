"""Election results from an official results export into result.yaml files, for one ballot's contests.

Usage:
  python src/collectors/results.py (--ballot data/states/tx/localities/harris-county/elections/2026-11-03/ballot.yaml
                                    | --statewide TX 2026-11-03) \
      --file <export.csv|.json|.pdf> --source-url URL --source-title TITLE --as-of YYYY-MM-DD \
      [--status unofficial|official] [--source-kind page|document] [--source-file PDF] [--whole PATH ...]
      [--map "CONTEST=PATH" ...] [--write]

Without --write it only prints what it would write.

The export is a table of contest, choice, and votes (CSV with a header row, or JSON: a list of such rows, or
{"contests": [{"name", "choices": [{"name", "votes"}]}]}). Column names are matched loosely ("Contest Name",
"Race", "Choice", "Candidate", "Total Votes", ...), so a Clarity ENR "summary.csv" reads as is, as does the
"summary.zip" it comes in. A precinct-by-precinct CSV (one column per choice, a COUNTY TOTALS row) reads from that
row. A PDF is read as a Hart "Cumulative Report" or ES&S "Summary Results Report" (parse_hart_text), or as a Hart
report whose text comes out one value per line (parse_hart_columns). A scanned report is typed up (OCR, each row
checked) as the JSON above; --source-file then names the downloaded report, which is what sha256 is taken of.

How contests are matched to the ballot (never by guessing):
  - a race: the ballot race whose candidate names match the contest's choices (every listed candidate found,
    write-ins aside). Office titles are printed differently everywhere; names are not.
  - a measure: the ballot measure whose name has the same proposition label and place words.
  - --map "Contest as printed=tx/localities/x/elections/2026-11-03/slug" settles anything else by hand, and --skip
    leaves a contest out (e.g. one measure the report counts in two parts, "Full Purpose" and "Limited Purpose").
Unmatched contests and choices are listed, never written.

What gets written, per matched contest (CLAUDE.md rules 2, 3, 3a):
  - counts by candidate id (or by measure choice as printed); write-in totals go to other_votes; over/under votes
    are left out;
  - status and as_of; covers "whole" for the county's own contests and any --whole path, else "partial" with a
    note naming the county (a city or district that crosses county lines). With --ballot, statewide and district
    races are skipped unless listed in --whole: their totals come from the Secretary of State (--statewide);
  - one source fact (S-01, S-02, ... a new ID each update), written unverified.
An official or verified result is never replaced, a whole count never by a partial one (a county's share of a
statewide race), and an unofficial one only by a report at least as new. A contest crossing county lines shows one
county's share, the one with the most votes counted (another county's smaller share is kept out). --statewide TX DATE matches every statewide
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
                          r"total votes cast|not assigned|cast votes:?|contest totals)$")
WRITE_IN_TOTAL = re.compile(r"(?i)^write[\s-]*in totals?$")
WRITE_IN = re.compile(r"(?i)write[\s-]*in|\(w\)\s*$")


def key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\([^)]*\)|\"[^\"]*\"|'[^']*'", " ", s)  # party tags and nicknames
    return re.sub(r"[^a-z0-9]", "", s)


def name_keys(name: str) -> set[str]:
    """Ways a candidate's name may be printed: full, and first + last (middle names, initials, suffixes dropped)."""
    name = PARTY_CODE.sub("", name)  # Fort Bend's Clarity file prints "REP ASHA REDDI"
    name = PARTY_AFTER.sub("", name)  # Hidalgo's prints "Juan Zamora DEM"
    name = re.sub(r"\([^)]*\)", " ", name)  # "(I)" marks the incumbent in the Secretary of State's returns
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", unicodedata.normalize("NFKD", name)
                                   .encode("ascii", "ignore").decode())
             if w.lower().rstrip(".") not in ("jr", "sr", "ii", "iii", "iv")]
    out = {key(name)}
    if len(words) >= 2:
        out.add(key(words[0] + words[-1]))
    # a PDF may print an accented letter as an unreadable character ("Pe�a", or "PE?A" in a Hart report): match on
    # the plain letters around it
    out.add(re.sub(r"[^a-z0-9]", "", re.sub(r"[^\x00-\x7f]|\?", "", name).lower()))
    return out


PARTY_CODE = re.compile(r"^(?:REP|DEM|LIB|GRN|IND|NP)\s+")
PARTY_AFTER = re.compile(r"\s+(?:REP|DEM|LIB|GRN|IND|NP)$")
COUNT = re.compile(r"^\d{1,3}(?:,\d{3})*$")
REPORT_NOISE = re.compile(r"(?i)(cumulative report|summary results report|election summary -|total number of voters|"
                          r"^page \d|page \d+ of|votes:|precincts|vote centers reporting|counted|registered|ballots cast|voter turnout|"
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


PARTY_LINE = re.compile(r"^(?:REP|DEM|LIB|GRN|GRE|IND|NP|NON)$")
NUMBER_LINE = re.compile(r"^(?:\d{1,3}(?:,\d{3})*|\d+(?:\.\d+)?%)$")
STAT_LINE = re.compile(r"(?i)^(cast votes|undervotes|overvotes|under votes|over votes|rejected write-in votes):?$")
REPEAT_MARK = re.compile(r" \[\d+\]$")  # the mark on a title printed a second time (parse_hart_columns, mark_repeats)
TABLE_HEADINGS = {"Choice", "Party", "Total", "Absentee", "Absentee Voting", "Early Voting", "Election Day",
                  "Election Day Voting"}


def drop_page_tops(pages: list[str]) -> list[str]:
    """Lines of each page without the block printed at the top of every page (county, election, page number, turnout,
    run time). A line counts as that block if it starts most pages (page numbers and digits aside). Some reports'
    text puts that block last on the page (Montgomery, Nueces), so it is taken off the end the same way; counts, vote
    lines and the table headings never count as part of it."""
    split = [[ln.strip() for ln in p.splitlines() if ln.strip()] for p in pages]
    shape = lambda ln: re.sub(r"\d+", "#", ln)  # noqa: E731
    keep = lambda ln: NUMBER_LINE.match(ln) or STAT_LINE.match(ln) or ln in TABLE_HEADINGS  # noqa: E731
    starts: dict[str, int] = {}
    ends: dict[str, int] = {}
    for lines in split:
        for ln in set(map(shape, lines[:30])):
            starts[ln] = starts.get(ln, 0) + 1
        for ln in set(map(shape, (x for x in lines[-30:] if not keep(x)))):
            ends[ln] = ends.get(ln, 0) + 1
    least = max(2, len(pages) * 0.6)
    common = {ln for ln, n in starts.items() if n >= least} - {"Choice", "Party"}
    common_end = {ln for ln, n in ends.items() if n >= least}
    out = []
    for lines in split:
        i, j = 0, len(lines)
        while i < len(lines) and shape(lines[i]) in common and lines[i] != "Choice":
            i += 1
        while j > i and shape(lines[j - 1]) in common_end and not keep(lines[j - 1]):
            j -= 1
        out.extend(lines[i:j])
    return out


def parse_hart_columns(pages: list[str]) -> list[tuple[str, str, int]]:
    """(contest, choice, votes) from a Hart "Cumulative Results Report" whose text comes out one value per line (Harris
    and Tarrant since 2024): the contest title, then the headings "Choice", "Party", the voting methods, and "Total";
    then each choice's name, its party code if any, and a count and percent per method, the total last. The total is
    the largest count in the choice's block. Cast, under, and over vote lines end the choices."""
    lines = drop_page_tops(pages)
    rows, contest, i = [], None, 0
    name, counts, stats = [], [], False
    seen: set[str] = set()
    printed = None  # the current contest's title as printed (contest may carry a " #2")

    def flush():
        if contest and name and counts and not stats:
            rows.append((contest, " ".join(name), max(counts)))

    while i < len(lines):
        ln = lines[i]
        if ln == "Choice" and i + 1 < len(lines) and lines[i + 1] == "Party":
            flush()
            title, j = [], i - 1
            while j >= 0 and len(title) < 3 and not NUMBER_LINE.match(lines[j]) and not STAT_LINE.match(lines[j]) \
                    and lines[j] != "Total":
                title.insert(0, lines[j])
                j -= 1
            title_text = re.sub(r"(?i)\s*-\s*vote for .*$", "", " ".join(title)).strip(" ,")
            # a contest carried onto the next page repeats its headings, not always its title
            if title_text and title_text != printed:
                # two places' measures may both print as "Proposition A" (Nueces): the later one is "Proposition A [2]"
                printed, contest, n = title_text, title_text, 2
                while contest in seen:
                    contest, n = f"{printed} [{n}]", n + 1
                seen.add(contest)
            while i < len(lines) and lines[i] != "Total":
                i += 1
            name, counts, stats = [], [], False
        elif STAT_LINE.match(ln):
            flush()
            name, counts, stats = [], [], True
        elif NUMBER_LINE.match(ln):
            if not ln.endswith("%"):
                counts.append(int(ln.replace(",", "")))
        elif PARTY_LINE.match(ln) and name and not counts:
            pass
        else:
            if counts or stats:
                flush()
                name, counts, stats = [], [], False
            name.append(ln)
        i += 1
    flush()
    return rows


def parse_precinct_csv(text: str) -> list[tuple[str, str, int]]:
    """(contest, choice, votes) from a precinct-by-precinct CSV (Collin County): a header row of contest names, a row
    of parties, a row of choice names, then one row per precinct, the county totals row last. Uses the totals row."""
    table = list(csv.reader(io.StringIO(text)))
    contests, choices = table[0], table[2]
    body = [r for r in table[3:] if any(c.strip() for c in r)]
    totals = [r for r in body if any(re.search(r"(?i)county\s+totals?", c) for c in r[:3])]
    if len(totals) != 1:
        raise SystemExit("precinct CSV: no single COUNTY TOTALS row; not adding up precincts by guess")
    row = totals[0]
    start = next(k for k, c in enumerate(contests) if k >= 3 and c.strip() and not re.search(
        r"(?i)registered|ballots cast", c))
    return [(contests[k].strip(), choices[k].strip(), int(row[k].replace(",", "") or 0))
            for k in range(start, len(contests)) if contests[k].strip() and choices[k].strip()]


def mark_repeats(rows: list[tuple[str, str, int]]) -> list[tuple[str, str, int]]:
    """A title that comes back after other contests, or whose choices start over (For, Against, For, ...), is another
    contest of the same name (two districts' "Proposition A", or titles a Clarity export cut short): the second becomes
    "Proposition A [2]", as parse_hart_columns marks it."""
    out, runs, prev, seen = [], {}, None, set()
    for title, choice, votes in rows:
        if title != prev or choice in seen:
            runs[title] = runs.get(title, 0) + 1
            prev, seen = title, set()
        seen.add(choice)
        out.append((f"{title} [{runs[title]}]" if runs[title] > 1 else title, choice, votes))
    return out


def read_rows(path: Path) -> list[tuple[str, str, int]]:
    """(contest, choice, votes) rows from a CSV, JSON, Clarity summary zip, or Hart/ES&S results-report PDF."""
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        pages = [p.extract_text() or "" for p in PdfReader(str(path)).pages]
        if any(re.search(r"(?m)^\s*Choice\s*\n\s*Party\s*$", p) for p in pages):
            return parse_hart_columns(pages)
        return parse_hart_text("\n".join(pages))
    if path.suffix.lower() == ".zip":
        import zipfile
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise SystemExit(f"{path}: expected one CSV inside (Clarity summary.zip), found {names}")
            raw = z.read(names[0])
    else:
        raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:  # county exports (and some Clarity summary.csv files) are Windows-1252
        text = raw.decode("cp1252")
    if path.suffix.lower() in (".csv", ".zip") and re.match(r"(?i)[^\n]*precinct (code|name)", text):
        return parse_precinct_csv(text)
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if isinstance(data, dict) and "contests" in data:
            return [(c["name"], ch["name"], int(ch["votes"])) for c in data["contests"] for ch in c["choices"]]
        if isinstance(data, dict) and "ballotItems" in (data.get("results") or {}):  # Enhanced Voting "Media Export"
            return [(b["name"], o["name"], int(o["voteCount"])) for b in data["results"]["ballotItems"]
                    for o in b["ballotOptions"]]
        rows = [{k.lower().strip(): v for k, v in r.items()} for r in data]
    else:
        rows = [{(k or "").lower().strip(): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(text))]
    if not rows:
        return []
    if "tbcontestname" in rows[0] and "choicename" in rows[0]:
        # Hart's "Cumulative Results Report" saved as CSV (Hays): one column of votes per voting method
        # (ID1__Votes, ID2__Votes, ...); the other column names are the report's own text boxes
        methods = [k for k in rows[0] if re.fullmatch(r"id\d+__votes", k)]
        return [(r["tbcontestname"].strip(), r["choicename"].strip(),
                 sum(int(r[k].replace(",", "") or 0) for k in methods)) for r in rows
                if r["tbcontestname"] and r["choicename"]]

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
    # every printed name must belong to the race: two people can share a name (Tarrant's two David Cooks, one in a
    # Senate race, one alone in a county court race)
    if not listed or {x["id"] for x in listed} - set(found.values()) or unknown:
        return None
    return found, writeins, unknown


def split_title(contests: list[dict], names: list[str]) -> list[tuple[dict, set[str]]] | None:
    """Races whose candidates together are exactly the named choices under one title, each choice in one race."""
    named = [n for n in names if not SKIP_CHOICES.match(n) and not WRITE_IN.search(n)]
    parts, taken = [], set()
    for c in contests:
        listed = [x for x in c.get("candidates") or [] if not x["write_in"]]
        own = {n for n in named if any(name_keys(n) & name_keys(x["name"]) for x in listed)}
        if listed and len(own) == len(listed) and all(any(name_keys(n) & name_keys(x["name"]) for n in own)
                                                      for x in listed):
            if own & taken:
                return None
            parts.append((c, own))
            taken |= own
    return parts if len(parts) > 1 and taken == set(named) else None


PROP = re.compile(r"(?i)\b(?:prop(?:osition)?|measure|propuesta)\s*(?:no\.?\s*)?([a-z0-9]{1,3})\b")


DISTRICT_TYPES = [(re.compile(r"(?i)\b" + long + r"\b"), short) for long, short in (
    (r"municipal utility district", "mud"), (r"municipal management district", "mmd"),
    (r"water control and improvement district", "wcid"),
    (r"fresh water supply district", "fwsd"), (r"emergency services district", "esd"),
    (r"independent school district", "isd"))]


NAME_WORD = re.compile(r"\d+[a-z](?![a-z])|[a-z]+|\d+")  # "265a" (MUD No. 265A) stays one word


def measure_fits(contest: dict, title: str) -> int:
    """How well a printed title fits a ballot measure: 0 if not at all, else the number of the measure's name words
    the title shares (so "City of Lake Dallas" beats "City of Dallas", and "Georgetown ISD" beats "City of Georgetown")."""
    a, b = PROP.search(contest["name"]), PROP.search(title)
    if not (a and b) or a[1].lower() != b[1].lower():
        return 0
    name = contest["name"]
    for long, short in DISTRICT_TYPES:  # Denton prints "Municipal Utility District No. 7"; most of our names say "MUD"
        title, name = long.sub(short, title), long.sub(short, name)
    named = [w for w in NAME_WORD.findall(name.lower().split("proposition")[0]) if w not in ("of", "the", "no")]
    place = [w for w in named if w not in ("city", "county", "isd", "district", "town", "village")]
    # a district number must match whole ("MUD No. 2" is not "MUD No. 12" or "MUD No. 4"; "MUD No. 8A" is not "8B")
    numbers = re.findall(r"\d+[a-z](?![a-z])|\d+", title.replace(b[0], " ").lower())
    if not all(w in numbers if w[0].isdigit() else w in title.lower() for w in place):
        return 0
    words = set(NAME_WORD.findall(title.lower()))
    return sum(w in words for w in named) or 1


def plan(contests: list[dict], rows: list[tuple[str, str, int]], manual: dict[str, str]) -> tuple[list, list[str]]:
    by_title: dict[str, list[tuple[str, int]]] = {}
    for title, choice, votes in rows:
        by_title.setdefault(title, []).append((choice, votes))
    by_path = {c["path"]: c for c in contests}
    matched, problems, used = [], [], {}
    manual, queue, shown = dict(manual), list(by_title.items()), {}
    while queue:
        title, choices = queue.pop(0)
        names = [c for c, _ in choices]
        if title in manual:
            cands = [by_path[manual[title]]] if manual[title] in by_path else []
            if not cands:
                problems.append(f"--map: {manual[title]} is not on this ballot")
                continue
        else:
            cands = [c for c in contests if (c["kind"] == "race" or measure_fits(c, title))]
        fits = [(c, m) for c in cands if (m := match_choices(c, names))]
        if len(fits) > 1 and all(c["kind"] != "race" for c, _ in fits):
            # two measures fit the title: the one whose name the title spells out more fully ("City of Lake Dallas")
            best = max(measure_fits(c, title) for c, _ in fits)
            fits = [(c, m) for c, m in fits if measure_fits(c, title) == best]
        if len(fits) != 1 and title not in manual and (parts := split_title(contests, names)):
            # several seats printed under one title (Fort Bend's appraisal district): each race takes its own names
            for c, own in parts:
                # keyed by the whole path: Brazos prints two cities' races as plain "MAYOR"
                part = f"{title} [{c['path']}]"
                manual[part], shown[part] = c["path"], title
                queue.append((part, [x for x in choices if x[0] in own]))
            continue
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
        matched.append({"contest": contest, "title": REPEAT_MARK.sub("", shown.get(title, title)), "counts": counts,
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


def total(doc: dict) -> int:
    return sum(doc["counts"].values()) + (doc.get("other_votes") or 0)


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
    ap.add_argument("--source-file", type=Path,
                    help="the downloaded document, when --file is a transcription of it (a scan read by OCR)")
    ap.add_argument("--status", choices=("unofficial", "official"), default="unofficial")
    ap.add_argument("--as-of", required=True, help="date of the results report (YYYY-MM-DD)")
    ap.add_argument("--retrieved", default=dt.date.today().isoformat())
    ap.add_argument("--whole", nargs="*", help="contest paths this report counts in full (e.g. a city inside the county)")
    ap.add_argument("--map", nargs="*", default=[], help='"Contest as printed=contest path" for contests matched by hand')
    ap.add_argument("--skip", nargs="*", default=[],
                    help="contest paths to leave out (the report counts them in a way that doesn't fit one result)")
    ap.add_argument("--root", type=Path, default=REPO, help="data repo root (default: this repo)")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    manual = dict(m.rsplit("=", 1) for m in args.map)  # a printed title may hold "=" ("74 of 91 = 81.32%")
    if args.ballot:
        ballot = args.ballot if args.ballot.is_absolute() else root / args.ballot
        # statewide and district races come in full from the Secretary of State (--statewide), not one county's share
        contests = [c for c in load_ballot(ballot, root)
                    if c["path"].split("/")[1] == "localities" or c["path"] in (args.whole or [])]
        locality, county = county_name(ballot)
    else:
        contests = load_ballot(None, root, statewide_contests(root, args.statewide[0].lower(), args.statewide[1]))
        locality, county = None, None
    contests = [c for c in contests if c["path"] not in args.skip]
    matched, problems = plan(contests, mark_repeats(read_rows(args.file)), manual)
    sha = hashlib.sha256((args.source_file or args.file).read_bytes()).hexdigest()         if args.source_kind == "document" else None
    wrote = skipped = 0
    for m in matched:
        target = root / "data/states" / m["contest"]["path"] / "result.yaml"
        old = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else None
        if old:
            verified = any(s.get("verification") == "verified" for s in old.get("sources") or [])
            new = result_doc(m, args, locality, county, 1, None)
            # a contest crossing county lines shows the county share with the most votes counted
            # (report dates only order one county's own reports)
            other_county = old["covers"] == "partial" and new["covers"] == "partial" \
                and old.get("covers_note") != new.get("covers_note")
            if verified or (old["covers"] == "whole" and new["covers"] == "partial") \
                    or (old["status"] == "official" and args.status == "unofficial") \
                    or (total(old) >= total(new) if other_county else str(old["as_of"]) > args.as_of):
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
