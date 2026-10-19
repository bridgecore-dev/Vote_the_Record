"""Campaign finance totals from the Texas Ethics Commission (TEC) for one ballot's candidates.

Usage:
  python src/collectors/states/tx/tec_finance.py \
      --ballot data/states/tx/localities/harris-county/elections/2026-11-03/ballot.yaml \
      (--zip <TEC_CF_CSV.zip> | --download) [--write]

Without --write it only prints what it would add. With --write it adds facts to candidate
and race files under data/ (a data/ change, which needs the owner's approval).

What it does, for every candidate in a race whose reports are filed with TEC (state offices
and district judges; site/site.yaml campaign_finance):
  - Finds the candidate's TEC filer. An exact first name + surname match must also hold this
    office (TEC office code). A nickname, middle name, initial, or a surname printed differently
    on the ballot is accepted only when the office code AND the district both match.
  - Takes the filer's latest report for a period ending between --since and --today; a
    correction filed later replaces the version it corrects.
  - Downloads that report's own PDF from TEC (the filed Form C/OH), checks that every total
    from the data appears in it, and cites the PDF (URL + SHA-256), so the fact can be archived
    and verified like any other document. A report whose PDF can't be found or doesn't match is
    left for a person to check.
  - Adds a funding fact (M-01, M-02, ...) when that report is new for the candidate. Existing
    facts are never edited, so published IDs and verified facts stay as they are.
    (--relink is a one-time exception: it repoints unverified facts that still cite the bulk
    file to their report's PDF, changing only source fields.)
  - For candidates with no such report, adds one race fact (S-FUND) saying so, if the race has
    none yet; otherwise it prints a note to update by hand.
Every fact is written unverified (CLAUDE.md rule 3a).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "src" / "collectors"))
from yamlio import NotRoundTrip, read_doc, write_doc  # noqa: E402

TEC_URL = "https://prd.tecprd.ethicsefile.com/public/cf/public/TEC_CF_CSV.zip"  # linked from TEC_SEARCH (moved by October 2026)
TEC_TITLE = "Texas Ethics Commission: Campaign Finance Data, bulk CSV download"
TEC_SEARCH = "https://www.ethics.state.tx.us/search/cf/"
# Each filed report's PDF, as linked from TEC's report-number search; the folder is a year.
REPORT_PDF = "https://prd.tecprd.ethicsefile.com/public/cf/{year}/pdfs/ScrubbedReport_{rid}.PDF"
USER_AGENT = "Mozilla/5.0 (compatible; VoteTheRecord/1.0; +https://github.com/bridgecore-dev/Vote_the_Record)"
FILER_TYPES = ("COH", "JCOH")  # candidate/officeholder and judicial candidate/officeholder

# TEC office codes (filers.csv ctaSeekOfficeCd / filerHoldOfficeCd) for each office type.
OFFICE_CODES = {
    "governor": {"GOVERNOR"}, "lieutenant-governor": {"LTGOVERNOR"}, "attorney-general": {"ATTYGEN"},
    "comptroller": {"COMPTROLLER"}, "land-commissioner": {"LANDCOMM"}, "agriculture-commissioner": {"AGRICULTUR"},
    "railroad-commissioner": {"RRCOMM"}, "supreme-court-justice": {"JUSTICE_SC", "CHIEFJUSTICE_SC"},
    "cca-judge": {"JUDGE_COCA", "PRESIDINGJUDGE_COCA"}, "sboe-member": {"STATEEDU"}, "state-senator": {"STATESEN"},
    "state-representative": {"STATEREP"}, "court-of-appeals-justice": {"JUSTICE_COA", "CHIEFJUSTICE_COA"},
    "district-judge": {"JUDGEDIST", "JUDGEDIST_FAMILY", "CRIMINAL_JUDGEDIST", "JUDGEDIST_MULTI"},
}
AMOUNTS = (("totalContribAmount", "in total political contributions"),
           ("totalExpendAmount", "in total political expenditures"),
           ("contribsMaintainedAmount", "in contributions on hand at the end of the period"),
           ("loanBalanceAmount", "in outstanding loans"))


def norm(text: str) -> list[str]:
    """Upper-case ASCII name tokens, without quoted nicknames or Jr./Sr./II-IV."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r'"[^"]*"', " ", text)
    text = re.sub(r"\b(JR|SR|II|III|IV)\b\.?", " ", text.upper())
    return re.sub(r"[^A-Z ]+", " ", text).split()


def race_district(race_path: str) -> str | None:
    m = re.search(r"(?:state-house|state-senate|sboe|court-of-appeals|district-court)-(\d+)", race_path)
    return m.group(1).lstrip("0") if m else None


def ymd(value: str) -> str:
    return f"{value[:4]}-{value[4:6]}-{value[6:]}"


class TecData:
    """Filers (every row per filer) and each filer's latest report in a date window."""

    def __init__(self, zip_path: Path, since: str, today: str):
        self.rows: dict[str, list[dict]] = {}
        self.by_last: dict[str, list[tuple[str, dict]]] = {}
        self.latest: dict[str, dict] = {}
        lo, hi = since.replace("-", ""), today.replace("-", "")
        with zipfile.ZipFile(zip_path) as z:
            with z.open("filers.csv") as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                    if r["filerTypeCd"] in FILER_TYPES:
                        self.rows.setdefault(r["filerIdent"], []).append(r)
            with z.open("cover.csv") as fh:
                for r in csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace")):
                    end = r["periodEndDt"]
                    if r["filerTypeCd"] not in FILER_TYPES or not end or not lo <= end <= hi:
                        continue
                    cur = self.latest.get(r["filerIdent"])
                    rank = (end, r["filedDt"] or "", r["receivedDt"] or "")
                    if cur is None or rank > (cur["periodEndDt"], cur["filedDt"] or "", cur["receivedDt"] or ""):
                        self.latest[r["filerIdent"]] = r
        for fid, rows in self.rows.items():  # index every row: a filer's first row may hold a placeholder name
            for r in rows:
                last = "".join(norm(r["filerNameLast"]))
                if last:
                    self.by_last.setdefault(last, []).append((fid, r))

    def codes(self, fid: str) -> set[str]:
        return {c for r in self.rows[fid] for c in (r["ctaSeekOfficeCd"], r["filerHoldOfficeCd"],
                                                     r.get("contestSeekOfficeCd", ""))} - {"", "NONE", "OTHER"}

    def districts(self, fid: str) -> set[str]:
        return {x.strip().lstrip("0") for r in self.rows[fid]
                for x in (r["ctaSeekOfficeDistrict"], r["filerHoldOfficeDistrict"], r.get("contestSeekOfficeDistrict", ""))
                if x and x.strip()}

    def match(self, name: str, office_type: str, district: str | None) -> dict:
        """{'status': 'report'|'no-report'|'not-found'|'ambiguous', ...}"""
        toks = norm(name)
        if len(toks) < 2:
            return {"status": "not-found"}
        first, wanted = toks[0], OFFICE_CODES.get(office_type, set())
        found: dict[str, tuple[str, dict]] = {}  # fid -> (how, row)

        def corroborated(fid: str) -> bool:
            return bool(wanted & self.codes(fid)) and (district is None or district in self.districts(fid))

        def first_ok(row: dict, loose: bool) -> bool:
            given, short = norm(row["filerNameFirst"]), norm(row["filerNameShort"])
            if given and given[0] == first:
                return True
            return loose and (first in given or (short and short[0] == first) or any(g[0] == first[0] for g in given))

        for split in range(1, len(toks)):  # every surname split, for compound surnames
            for fid, row in self.by_last.get("".join(toks[split:]), []):
                if first_ok(row, loose=False) and wanted & self.codes(fid):
                    found.setdefault(fid, ("first name", row))
                elif corroborated(fid) and first_ok(row, loose=True):
                    found.setdefault(fid, ("nickname, middle name, or initial, with office and district", row))
        if not found:  # surname printed differently on the ballot (a part added or dropped)
            joined = "".join(toks[1:])
            for key, entries in self.by_last.items():
                if not (any(len(t) > 3 and t in key for t in toks[1:]) or (len(key) > 3 and key in joined)):
                    continue
                for fid, row in entries:
                    if corroborated(fid) and first_ok(row, loose=True):
                        found.setdefault(fid, ("surname variant, with office and district", row))
        with_report = [fid for fid in sorted(found) if fid in self.latest]
        if any(found[f][0] == "first name" for f in with_report):
            with_report = [f for f in with_report if found[f][0] == "first name"]
        if district is not None and len(with_report) > 1:
            exact = [f for f in with_report if district in self.districts(f)]
            with_report = exact or with_report
        if len(with_report) == 1:
            fid = with_report[0]
            return {"status": "report", "filer": fid, "filer_name": found[fid][1]["filerName"],
                    "matched_by": found[fid][0], "report": self.latest[fid]}
        if len(with_report) > 1:
            return {"status": "ambiguous", "filers": with_report}
        return {"status": "no-report" if found else "not-found"}


def money(v: str) -> str:
    return f"${float(v):,.2f}"


def day(value: str) -> str:
    d = dt.date(int(value[:4]), int(value[4:6]), int(value[6:]))
    return f"{d:%b} {d.day}"


def period(start: str, end: str) -> str:
    return f"{day(start)}{', ' + start[:4] if start[:4] != end[:4] else ''} - {day(end)}, {end[:4]}"


def http_get(url: str) -> bytes | None:
    """The body, or None for a 404. Other errors raise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise


def pdf_text(data: bytes, max_pages: int | None = None) -> str:
    from pypdf import PdfReader
    pages = PdfReader(io.BytesIO(data)).pages
    return " ".join((page.extract_text() or "") for page in list(pages)[:max_pages])


def report_pdf(rep: dict, getter=http_get) -> dict:
    """{'url', 'sha256', 'problem'}: the report's PDF, checked against the data's totals."""
    rid = rep["reportInfoIdent"]
    years = dict.fromkeys([rep["filedDt"][:4], rep["periodEndDt"][:4], str(int(rep["filedDt"][:4]) + 1)])
    for year in years:
        url = REPORT_PDF.format(year=year, rid=rid)
        data = getter(url)
        if data is None:
            continue
        if not data.startswith(b"%PDF"):
            return {"url": url, "sha256": None, "problem": "the report link didn't return a PDF"}
        wanted = [f"{float(rep[k]):,.2f}" for k, _ in AMOUNTS if rep[k]]
        text = pdf_text(data, 8)  # the cover and totals pages come first
        if any(v not in text for v in wanted):
            text = pdf_text(data)
        missing = [v for v in wanted if v not in text]
        problem = f"totals not found in the PDF: {', '.join(missing)}" if missing else None
        return {"url": url, "sha256": hashlib.sha256(data).hexdigest(), "problem": problem}
    return {"url": None, "sha256": None, "problem": "no PDF found for this report"}


def pdf_source(m: dict, pdf: dict, retrieved: str) -> dict:
    """The source fields of a funding fact that cites the report's own PDF."""
    rep = m["report"]
    notes = (f"Report ID {rep['reportInfoIdent']}, form {rep['formTypeCd']}, TEC report type {rep['reportTypeCd1']}; "
             f"filer ID {m['filer']} ({m['filer_name']}). Found in TEC's bulk campaign finance data ({TEC_URL}, "
             f"cover.csv) and matched by {m['matched_by']}; every total in this fact was checked against the PDF. This "
             f"is the filer's latest report for a period ending by {retrieved}; a correction filed later replaces the "
             f"version it corrects.")
    return {"source_url": pdf["url"],
            "source_title": f"Texas Ethics Commission: {m['filer_name']}, campaign finance report {rep['reportInfoIdent']} "
                            f"(filed {day(rep['filedDt'])}, {rep['filedDt'][:4]})",
            "source_kind": "document", "sha256": pdf["sha256"], "archive_url": None, "retrieved": retrieved,
            "notes": notes}


def funding_fact(fact_id: str, m: dict, zip_sha: str, retrieved: str, pdf: dict | None = None) -> dict:
    rep = m["report"]
    vals = {k: rep[k] for k, _ in AMOUNTS}
    c, e, h = vals["totalContribAmount"], vals["totalExpendAmount"], vals["contribsMaintainedAmount"]
    span = period(rep["periodStartDt"], rep["periodEndDt"])
    bits = []
    if c:
        bits.append(f"Raised ${round(float(c)):,}")
    if h:
        bits.append(f"{'had' if bits else 'Had'} ${round(float(h)):,} on hand")
    if not bits and e:
        bits.append(f"Spent ${round(float(e)):,}")
    parts = [f"{money(vals[k])} {label}" for k, label in AMOUNTS if vals[k]]
    missing = [label.removeprefix("in ") for k, label in AMOUNTS if not vals[k]]
    listed = (", ".join(parts[:-1]) + (", and " if len(parts) > 1 else "") + parts[-1]) if parts else "no totals listed"
    statement = f"Campaign finance report covering {span}, filed {day(rep['filedDt'])}, {rep['filedDt'][:4]}: {listed}."
    if missing:
        statement += " The report data lists no amount for " + " or ".join(missing) + "."
    notes = (f"From cover.csv in the Texas Ethics Commission's bulk campaign finance data, downloaded {retrieved} "
             f"(sha256 is of the zip file). Report ID {rep['reportInfoIdent']}, form {rep['formTypeCd']}, TEC report "
             f"type {rep['reportTypeCd1']}; filer ID {m['filer']} ({m['filer_name']}). Matched by {m['matched_by']}. "
             f"This is the filer's latest report for a period ending by {retrieved}; a correction filed later "
             f"replaces the version it corrects. The bulk file is too large for the Wayback Machine; the same report "
             f"can be checked in TEC's search at {TEC_SEARCH}.")
    fact = {"id": fact_id, "headline": (" and ".join(bits) or "Report filed") + f" ({span})", "statement": statement,
            "label": "official_record", "claim_status": "documented", "contradicted_by": [], "source_url": TEC_URL,
            "source_title": f"{TEC_TITLE} (report {rep['reportInfoIdent']})", "source_kind": "document",
            "sha256": zip_sha, "archive_url": None, "event_date": ymd(rep["periodEndDt"]), "retrieved": retrieved,
            "verification": "unverified", "verified_on": None, "reviewer": None, "notes": notes}
    return dict(fact, **pdf_source(m, pdf, retrieved)) if pdf else fact


def fund_check_fact(fact_id: str, names: list[str], since: str, zip_sha: str, retrieved: str) -> dict:
    who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    d = dt.date.fromisoformat(retrieved)
    return {"id": fact_id, "headline": "Campaign finance search with no recent report",
            "statement": (f"The Texas Ethics Commission's campaign finance data, downloaded {d:%B} {d.day}, {d.year}, "
                          f"has no report for a period ending on or after {since} for {who}."),
            "label": "official_record", "claim_status": "documented", "contradicted_by": [], "source_url": TEC_URL,
            "source_title": TEC_TITLE, "source_kind": "document", "sha256": zip_sha, "archive_url": None,
            "event_date": retrieved, "retrieved": retrieved, "verification": "unverified", "verified_on": None,
            "reviewer": None,
            "notes": ("Searched filers.csv and cover.csv (candidate and judicial officeholder filers) by name, with "
                      "office and district checks for nicknames. Absence in the bulk data is not proof that no report "
                      f"exists; check TEC's search at {TEC_SEARCH}. The bulk file is too large for the Wayback Machine.")}


def tec_races(root: Path, ballot_path: Path, config: dict) -> list[tuple[str, dict]]:
    """(race path under data/states, race doc) for every contest whose reports go to TEC."""
    state_types = set(((config.get("campaign_finance") or {}).get("tx") or {}).get("state_office_types") or [])
    ballot = yaml.safe_load(ballot_path.read_text(encoding="utf-8"))
    out = []
    for path in ballot["contests"]:
        race_file = root / "data/states" / path / "race.yaml"
        if not race_file.exists():
            continue  # a measure
        race = yaml.safe_load(race_file.read_text(encoding="utf-8"))
        office = race["office_type"]
        powers = root / "offices" / office / "powers.yaml"
        level = yaml.safe_load(powers.read_text(encoding="utf-8")).get("level") if powers.exists() else None
        if office in OFFICE_CODES and (level == "state" or office in state_types):
            out.append((path, race))
    return out


def report_id_of(fact: dict) -> str | None:
    m = re.search(r"\breport (\d+)\b", fact.get("source_title", ""))
    return m.group(1) if m else None


def plan(root: Path, ballot_path: Path, tec: TecData, zip_sha: str, retrieved: str, since: str,
         config: dict, getter=http_get, relink: bool = False) -> tuple[list[tuple[Path, dict]], list[str]]:
    """Files to rewrite (path, new doc) and a printable log."""
    changes: dict[Path, tuple[str, dict]] = {}
    log = []
    for race_path, race in tec_races(root, ballot_path, config):
        district = race_district(race_path)
        missing = []
        for cand_file in sorted((root / "data/states" / race_path / "candidates").glob("*.yaml")):
            try:
                header, cand = read_doc(cand_file)
            except NotRoundTrip as err:
                log.append(f"SKIP {err}")
                continue
            m = tec.match(cand["name"], race["office_type"], district)
            if m["status"] != "report":
                if m["status"] == "ambiguous":
                    log.append(f"AMBIGUOUS {race_path}: {cand['name']} matches filers {', '.join(m['filers'])}")
                missing.append(cand["name"])
                continue
            report_id = m["report"]["reportInfoIdent"]
            have = {report_id_of(f) for f in cand.get("funding") or []}
            if relink:
                for f in cand.get("funding") or []:
                    if f.get("source_url") != TEC_URL or f.get("verification") != "unverified":
                        continue
                    if report_id_of(f) != report_id:
                        log.append(f"BY HAND {f['id']}: cites report {report_id_of(f)}, not the latest; relink it by hand")
                        continue
                    pdf = report_pdf(m["report"], getter)
                    if pdf["problem"]:
                        log.append(f"BY HAND {f['id']}: {pdf['problem']} ({pdf['url'] or 'no URL'})")
                        continue
                    f.update(pdf_source(m, pdf, retrieved))
                    changes[cand_file] = (header, cand)
                    log.append(f"RELINK {f['id']}: {pdf['url']}")
            if report_id in have:
                continue
            pdf = report_pdf(m["report"], getter)
            if pdf["problem"]:
                log.append(f"BY HAND {race_path}: {cand['name']}, report {report_id}: {pdf['problem']} ({pdf['url'] or 'no URL'})")
                continue
            numbers = [int(x) for f in cand.get("funding") or [] for x in re.findall(r"#M-(\d+)$", f["id"])]
            fact_id = f"{race_path}/candidates/{cand_file.stem}#M-{max(numbers, default=0) + 1:02d}"
            cand["funding"] = list(cand.get("funding") or []) + [funding_fact(fact_id, m, zip_sha, retrieved, pdf)]
            changes[cand_file] = (header, cand)
            log.append(f"ADD {fact_id}: report {report_id} ({m['matched_by']})")
        if missing:
            race_file = root / "data/states" / race_path / "race.yaml"
            try:
                header, race_doc = read_doc(race_file)
            except NotRoundTrip as err:
                log.append(f"SKIP {err}")
                continue
            existing = [f for f in race_doc.get("sources") or [] if f["id"].endswith("#S-FUND")]
            if existing:
                if not all(n in existing[0]["statement"] for n in missing):
                    log.append(f"BY HAND {race_path}: no report for {', '.join(missing)}; S-FUND already exists")
                continue
            race_doc["sources"] = list(race_doc.get("sources") or []) + [
                fund_check_fact(f"{race_path}/race#S-FUND", missing, since, zip_sha, retrieved)]
            changes[race_file] = (header, race_doc)
            log.append(f"ADD {race_path}/race#S-FUND: no report for {', '.join(missing)}")
    return [(p, hd) for p, hd in changes.items()], log


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ballot", type=Path, required=True)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", type=Path, help="a downloaded TEC_CF_CSV.zip")
    src.add_argument("--download", action="store_true", help=f"download it into cache/tec/ (about 1 GB)")
    parser.add_argument("--since", help="earliest period end to count (default: Jan 1 of the year before the election)")
    parser.add_argument("--today", default=dt.date.today().isoformat())
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--write", action="store_true", help="write the changes (otherwise only print them)")
    parser.add_argument("--relink", action="store_true",
                        help="one-time: repoint unverified facts that cite the bulk file to their report's PDF")
    args = parser.parse_args(argv)

    zip_path = args.zip
    if args.download:
        zip_path = args.root / "cache" / "tec" / "TEC_CF_CSV.zip"
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {TEC_URL} ...")
        urllib.request.urlretrieve(TEC_URL, zip_path)
    election = yaml.safe_load(args.ballot.read_text(encoding="utf-8"))["election_date"]
    since = args.since or f"{int(election[:4]) - 1}-01-01"
    config = yaml.safe_load((args.root / "site" / "site.yaml").read_text(encoding="utf-8")) or {}
    tec = TecData(zip_path, since, args.today)
    changes, log = plan(args.root, args.ballot, tec, sha256(zip_path), args.today, since, config, relink=args.relink)
    print("\n".join(log) or "Nothing new.")
    if args.write:
        for path, (header, doc) in changes:
            write_doc(path, header, doc)
        print(f"Wrote {len(changes)} file(s). Every new fact is unverified.")
    else:
        print(f"{len(changes)} file(s) would change. Run again with --write to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
