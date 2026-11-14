"""Texas Legislature: each member's party, as the Texas Legislative Reference Library lists it (CLAUDE.md rule 5).

Fills `party` on every member of one session's members.yaml (data/states/tx/statewide/legislature/<session>/),
and `party_source`, the pages it comes from. The source is the Library's "Texas Legislators: Past & Present"
search, one page per chamber for the session's Legislature:
  https://lrl.texas.gov/legeLeaders/members/membersearch.cfm?leg=89&chamber=H
(the same results as the search form at https://lrl.texas.gov/legeLeaders/members/lrlhome.cfm). Each row
prints a member's district, and one line per term with the chamber, years, Legislatures, and party code.

Members are matched by chamber and district, never by name: a member gets the party of the one Library row whose
term in that chamber covers this Legislature and the session's year, and whose district is that district. The
row prints a single district, for its first term line; when the term that covers this Legislature isn't the first
line, the member's own Library page (memberDisplay.cfm?memberID=...) is read for that term's district and cited
too. The name is only a check: if it doesn't agree, the member is left without a party and listed in notes.

Usage (preview first; --write to apply):
  python src/collectors/states/tx/lrl_party.py --members data/states/tx/statewide/legislature/89r/members.yaml
      [--saved <folder>] [--retrieved YYYY-MM-DD] [--write]

--saved keeps the downloaded pages there and reuses any already saved (then pass --retrieved, the date they were
downloaded). Pages are fetched one at a time with a pause between requests; a page that isn't the Library's
results (a bot check, an error page) stops the run. Everything written is unverified; a file whose party_source
is already verified is never changed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import re
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BASE = "https://lrl.texas.gov/legeLeaders/members/"
SEARCH = BASE + "membersearch.cfm?leg={leg}&chamber={code}"
DETAIL = BASE + "memberDisplay.cfm?memberID={id}"
USER_AGENT = "Mozilla/5.0 (compatible; VoteTheRecord-collector/1.0; +https://github.com/bridgecore-dev/Vote_the_Record)"
PAUSE = 1.5  # seconds between requests
CODES = {"D": "Democrat", "R": "Republican"}  # the codes the Library prints; schemas/other.schema.json allows these
# The calendar year each session met, to pick the right member when a seat changed hands within a Legislature.
SESSION_YEAR = {"89R": 2025, "892": 2025}
CHAMBERS = {"house": "H", "senate": "S"}
ROOM = {"H": "House", "S": "Senate"}
NOTE_PREFIX = "Party codes (Texas Legislative Reference Library): "


class Stop(RuntimeError):
    """The Library didn't return its results page; stop rather than work around it."""


def ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _lines(cell: str) -> list[str]:
    return [t for t in (_text(x) for x in re.split(r"<br\s*/?>", cell)) if t]


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold()


def parse_search(text: str) -> list[dict]:
    """Rows of the Library's member search results: id, name, printed district, and one entry per term line."""
    if 'id="tableToSort"' not in text:
        raise Stop("the page has no results table (a bot check or an error page?)")
    body = text[text.find("<tbody"):text.find("</tbody>")]
    rows = []
    for tr in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        mid = re.search(r"memberDisplay\.cfm\?memberID=(\d+)", tr)
        name = re.search(r'alt="Headshot of [^>]*>(?:&nbsp;|\s)*([^<]+)</a>', tr)
        cells = [_lines(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if not (mid and name) or len(cells) < 6:
            raise Stop(f"a results row isn't in the expected layout: {_text(tr)[:120]!r}")
        _notes, district, chambers, years, sessions, parties = cells[:6]
        if not (len(chambers) == len(years) == len(sessions) == len(parties)):
            raise Stop(f"{_text(name[1])}: term lines don't line up")
        terms = []
        for ch, yr, se, pa in zip(chambers, years, sessions, parties):
            y = [int(x) for x in re.findall(r"\d{4}", yr)]
            s = [int(x) for x in re.findall(r"(\d+)(?:st|nd|rd|th)", se)]
            if not (y and s):
                raise Stop(f"{_text(name[1])}: can't read the term {yr!r} {se!r}")
            terms.append({"chamber": ch, "years": (y[0], y[-1]), "legs": (s[0], s[-1]), "party": pa})
        rows.append({"member_id": mid[1], "name": _text(name[1]), "terms": terms,
                     "district": int(district[0]) if district and district[0].isdigit() else None})
    if not rows:
        raise Stop("the results table is empty")
    return rows


def parse_detail(text: str) -> list[dict]:
    """A member's own Library page: one entry per term, with its chamber, district, Legislatures, and party."""
    if 'class="termTable"' not in text:
        raise Stop("the member page has no terms table (a bot check or an error page?)")
    terms = []
    for tr in re.findall(r'<tr class="resultRow\d">(.*?)</tr>', text, re.S):
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) >= 5 and cells[1].isdigit():
            terms.append({"chamber": cells[0], "district": int(cells[1]),
                          "legs": [int(x) for x in re.findall(r"(\d+)(?:st|nd|rd|th)", cells[3])], "party": cells[4]})
    return terms


def covering(row: dict, code: str, leg: int, year: int) -> list[int]:
    """Indexes of the row's term lines in this chamber that cover this Legislature and year."""
    return [i for i, t in enumerate(row["terms"])
            if t["chamber"] == code and t["legs"][0] <= leg <= t["legs"][1] and t["years"][0] <= year <= t["years"][1]]


def surname_agrees(ours: str, theirs: str) -> bool:
    words = [w for w in re.findall(r"[a-z]+", fold(ours)) if w not in {"jr", "sr", "ii", "iii", "iv"}]
    return bool(words) and words[-1] in re.findall(r"[a-z]+", fold(theirs))


def base_fact(fid: str, headline: str, statement: str, url: str, title: str, retrieved: str, notes: str) -> dict:
    return {"id": fid, "headline": headline, "statement": statement, "label": "official_record",
            "claim_status": "documented", "contradicted_by": [], "source_url": url, "source_title": title,
            "source_kind": "page", "sha256": None, "archive_url": None, "event_date": retrieved,
            "retrieved": retrieved, "verification": "unverified", "verified_on": None, "reviewer": None,
            "notes": notes}


def plan(doc: dict, prefix: str, get, retrieved: str) -> tuple[dict, list[str]]:
    """(members doc with party and party_source filled, log). `get(url)` returns a page's text."""
    session = doc["session"]
    if session not in SESSION_YEAR:
        raise SystemExit(f"session {session}: add the year it met to SESSION_YEAR in {Path(__file__).name}")
    leg, year = int(session[:2]), SESSION_YEAR[session]
    log: list[str] = []
    sources: list[dict] = []
    seats: dict[tuple[str, int], list[tuple[dict, dict]]] = {}  # (code, district) -> [(row, term)]
    for chamber, code in CHAMBERS.items():
        if not any(m["chamber"] == chamber for m in doc["members"]):
            continue
        url = SEARCH.format(leg=leg, code=code)
        rows = parse_search(get(url))
        sources.append(base_fact(
            f"{prefix}#P-{code}", f"{ROOM[code]} members of the {ordinal(leg)} Legislature, with party",
            f"The Texas Legislative Reference Library's member list for the {ordinal(leg)} Legislature, {ROOM[code]}, "
            f"prints each member's district and, for each term, the years, Legislatures, and party code.",
            url, f"Texas Legislative Reference Library: Texas Legislators: Past & Present, {ordinal(leg)} Legislature, "
                 f"{ROOM[code]}", retrieved,
            f"The same results as the search form at {BASE}lrlhome.cfm with Legislature {leg} and chamber "
            f"{ROOM[code]}. {len(rows)} rows. archive_url is left for src/tools/archive.py."))
        for row in rows:
            hits = covering(row, code, leg, year)
            if len(hits) != 1:
                log.append(f"SKIPPED LRL {ROOM[code]} row {row['name']!r}: {len(hits)} terms cover {year}")
                continue
            term = row["terms"][hits[0]]
            district = row["district"] if hits[0] == 0 else None
            if district is None:  # the printed district belongs to another term line: read the member's own page
                durl = DETAIL.format(id=row["member_id"])
                dterms = [t for t in parse_detail(get(durl)) if t["chamber"] == code and leg in t["legs"]]
                if len(dterms) != 1:
                    log.append(f"SKIPPED LRL {ROOM[code]} {row['name']!r}: its page lists {len(dterms)} "
                               f"{ordinal(leg)} Legislature terms")
                    continue
                district = dterms[0]["district"]
                if CODES.get(term["party"]) != dterms[0]["party"]:
                    log.append(f"SKIPPED LRL {ROOM[code]} {row['name']!r}: the list prints {term['party']!r}, "
                               f"its page {dterms[0]['party']!r}")
                    continue
                sources.append(base_fact(
                    f"{prefix}#P-{code}-{district:03d}", f"{ROOM[code]} District {district}: {row['name']}",
                    f"The Texas Legislative Reference Library's page for {row['name']} lists {ROOM[code]} District "
                    f"{district} for the {ordinal(leg)} Legislature, party {dterms[0]['party']}.",
                    durl, f"Texas Legislative Reference Library: {row['name']}, member profile", retrieved,
                    "Read because the member list prints this member's district for a different term line. "
                    "archive_url is left for src/tools/archive.py."))
            seats.setdefault((code, district), []).append((row, term))
    unmatched = []
    members = []
    for m in doc["members"]:
        code, room = CHAMBERS[m["chamber"]], ROOM[CHAMBERS[m["chamber"]]]
        found = seats.get((code, m["district"]), [])
        party, problem = None, None
        if len(found) != 1:
            problem = f"{len(found)} Library rows for {room} District {m['district']}"
        elif not surname_agrees(m["name"], found[0][0]["name"]):
            problem = f"the Library's row for {room} District {m['district']} names {found[0][0]['name']}"
        elif found[0][1]["party"] not in CODES:
            problem = f"the Library prints party {found[0][1]['party']!r}, not one of {sorted(CODES)}"
        else:
            party = found[0][1]["party"]
        if problem:
            unmatched.append(f"{room} District {m['district']} ({m['name']}): {problem}")
        row = {}
        for key, value in m.items():
            if key != "party":
                row[key] = value
            if key == "person":
                row["party"] = party
        members.append(row)
    out = {}
    for key, value in doc.items():
        if key not in ("party_source", "notes"):
            out[key] = members if key == "members" else value
    out["party_source"] = sources
    ours = {(CHAMBERS[m["chamber"]], m["district"]) for m in doc["members"]}
    absent = [f"{ROOM[code]} District {d} ({', '.join(r['name'] for r, _ in found)})"
              for (code, d), found in sorted(seats.items()) if (code, d) not in ours]
    kept = [p for p in (doc.get("notes") or "").split("\n\n") if p and not p.startswith(NOTE_PREFIX)]
    said = []
    if unmatched:
        said.append("no party for " + "; ".join(unmatched) + ".")
    if absent:
        said.append("the Library also lists these members, who have no entry in this file, so a vote that names "
                    "them shows no party totals: " + "; ".join(absent) + ".")
    if said:
        kept.append(NOTE_PREFIX + " ".join(said))
    if kept:
        out["notes"] = "\n\n".join(kept)
    log += [f"UNMATCHED {u}" for u in unmatched] + [f"NOT IN FILE {a}" for a in absent]
    return out, log


def keep_reviewed(old: dict, new: dict) -> str | None:
    """Carry archive links onto the same sources, and refuse to replace a verified party_source (rule 3a).
    Returns why the file must be left alone, or None."""
    before = {f["id"]: f for f in old.get("party_source") or []}
    if any(f.get("verification") == "verified" for f in before.values()):
        if [m.get("party") for m in old["members"]] == [m.get("party") for m in new["members"]]:
            new["party_source"] = old["party_source"]
            return None
        return "its party_source is verified and the parties would change; change it by hand"
    for f in new["party_source"]:
        o = before.get(f["id"])
        if o and o.get("archive_url") and o["source_url"] == f["source_url"]:
            f["archive_url"] = o["archive_url"]
    return None


def fetcher(saved: Path | None):
    last = [0.0]

    def get(url: str) -> str:
        name = re.sub(r"[^A-Za-z0-9]+", "-", url.split("/members/", 1)[1]).strip("-") + ".html"
        if saved and (saved / name).exists():
            return (saved / name).read_text(encoding="utf-8")
        time.sleep(max(0.0, PAUSE - (time.monotonic() - last[0])))
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                status, raw = resp.status, resp.read()
        except OSError as err:
            raise Stop(f"{url}: {err}") from err
        finally:
            last[0] = time.monotonic()
        if status != 200:
            raise Stop(f"{url}: HTTP {status}")
        text = raw.decode("utf-8", errors="replace")
        if saved:
            saved.mkdir(parents=True, exist_ok=True)
            (saved / name).write_text(text, encoding="utf-8")
        return text
    return get


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(REPO / "src" / "collectors"))
    from yamlio import read_doc, write_doc
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--members", type=Path, required=True, help="a session's members.yaml")
    parser.add_argument("--saved", type=Path, help="folder to keep (and reuse) the downloaded Library pages")
    parser.add_argument("--retrieved", type=dt.date.fromisoformat, default=dt.date.today(),
                        help="the date the pages were downloaded (default: today)")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    path = args.members.resolve()
    prefix = path.relative_to(REPO / "data" / "states").with_suffix("").as_posix()
    header, old = read_doc(path)
    try:
        new, log = plan(old, prefix, fetcher(args.saved), args.retrieved.isoformat())
    except Stop as err:
        print(f"STOPPED: {err}. Nothing written.")
        return 1
    refused = keep_reviewed(old, new)
    for line in log:
        print(line)
    if refused:
        print(f"KEPT {path.name}: {refused}")
        return 1
    counts = {}
    for m in new["members"]:
        counts.setdefault(m["chamber"], [0, 0])[m["party"] is None] += 1
    for chamber, (got, missing) in counts.items():
        print(f"{chamber}: {got} of {got + missing} members matched")
    changed = new != old
    if changed and args.write:
        write_doc(path, header, new)
    print(f"{path.relative_to(REPO).as_posix()}: {'written' if changed and args.write else 'would change' if changed else 'no change'}. "
          "Everything is unverified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
