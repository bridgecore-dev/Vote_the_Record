"""Add sourced records (votes, actions) to officials' person files from official documents saved to disk.

Usage:
  python src/tools/records_from_sources.py <records.json> [<records.json> ...] [--write]

Each records.json describes one official source (meeting minutes, a Tax Code § 26.06 tax-rate notice,
a roll call) and the records it shows:

  {"source_url": "https://...", "source_title": "Anderson County: 2025 Notice of Public Hearing on Tax Increase",
   "source_kind": "document" | "page", "retrieved": "2026-11-05", "event_date": "2025-08-26",
   "saved": "notice.txt", "original": "notice.pdf", "archive_url": null,
   "records": [{"seat": "tx/localities/anderson-county/offices/commissioner-precinct-1",
                "printed_name": "Greg Chapin",
                "record_type": "vote",
                "headline": "Voted for proposing a 2025 county tax rate of $0.5500 (Aug. 2025)",
                "statement": "As a member of Commissioners Court, voted FOR the proposal ... The notice lists ...",
                "result": "Proposed 4-1.",
                "quote": "FOR: Judge Smith, Commissioner Chapin ...",
                "notes": null}]}

A record goes to whoever holds the seat now, and only if the name printed in the source matches them (their
last name appears in printed_name), the printed name and the quote both appear in the saved text, and the
same source isn't already on their file for that headline. IDs continue the person's R-NN numbering.
Everything is written unverified (CLAUDE.md rule 3a). Preview first; --write to apply.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from officials_from_rosters import norm  # noqa: E402
from seed_officials import to_yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "states"
RECORD_TYPES = {"vote", "sponsored", "statement"}


def fold(text: str) -> str:
    """norm() without accents or punctuation, for comparing a printed name with the one on file."""
    text = unicodedata.normalize("NFKD", norm(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", text)


def amounts(headline: str | None) -> set[str]:
    """The dollar figures in a headline: two records of one vote from one source name the same rate."""
    return set(re.findall(r"\$[\d.,]*\d", headline or "")) - {"$100"}  # "per $100" is the unit, not a rate


def candidacy_records(person: dict) -> list[dict]:
    """Records already shown on the profile from the person's candidate files."""
    out = []
    for path in person.get("candidacies") or []:
        out += yaml.safe_load((DATA / f"{path}.yaml").read_text(encoding="utf-8")).get("records") or []
    return out


def holder(seat: str, today: str) -> str | None:
    f = DATA / f"{seat}.yaml"
    if not f.is_file():
        return None
    now = [h["person"] for h in yaml.safe_load(f.read_text(encoding="utf-8"))["holders"]
           if not h["end"] or h["end"] >= today]
    return now[0] if len(now) == 1 else None


def plan(files: list[Path], today: str) -> tuple[dict[Path, dict], list[str]]:
    people: dict[Path, dict] = {}
    notes = []
    for path in files:
        src = json.loads(path.read_text(encoding="utf-8"))
        saved = path.parent / src["saved"]
        if not saved.is_file():
            notes.append(f"REFUSE {path}: saved text {saved} not found")
            continue
        text = norm(saved.read_text(encoding="utf-8", errors="replace"))
        sha = None
        if src["source_kind"] == "document":
            original = path.parent / (src.get("original") or "")
            if not src.get("original") or not original.is_file():
                notes.append(f"REFUSE {path}: a document needs its downloaded file ('original') for sha256")
                continue
            sha = hashlib.sha256(original.read_bytes()).hexdigest()
        for rec in src["records"]:
            seat, printed = rec["seat"], rec["printed_name"]
            who = holder(seat, today)
            if not who:
                notes.append(f"REFUSE {seat}: no single current holder on file")
                continue
            target = DATA / seat.split("/")[0] / "people" / f"{who}.yaml"
            doc = people.get(target) or yaml.safe_load(target.read_text(encoding="utf-8"))
            last = fold(re.sub(r"(,? (jr|sr|ii|iii|iv|ed\.?d|m\.?d|ph\.?d)\.?)+$", "", doc["name"], flags=re.I)).split()[-1]
            missing = [x for x in (printed, rec["quote"]) if norm(x) not in text]
            if rec["record_type"] not in RECORD_TYPES:
                notes.append(f"REFUSE {seat}: record_type {rec['record_type']}")
            elif missing:
                notes.append(f"REFUSE {seat}: not in the saved text: {missing}")
            elif last not in fold(printed):
                notes.append(f"REFUSE {seat}: the source names {printed!r}, but the seat's holder is {doc['name']!r}")
            elif any(r["source_url"] == src["source_url"] and amounts(r.get("headline")) == amounts(rec["headline"])
                     for r in doc["records"] + candidacy_records(doc)):
                notes.append(f"SKIP {who}: already has a record of this from the same source ({rec['headline']!r})")
            else:
                numbers = [int(m[1]) for r in doc["records"] if (m := re.search(r"#R-(\d+)$", r["id"]))]
                fact = {"id": f"{seat.split('/')[0]}/people/{who}#R-{max(numbers, default=0) + 1:02d}",
                        "record_type": rec["record_type"], "headline": rec["headline"],
                        **({"result": rec["result"]} if rec.get("result") else {}),
                        "statement": rec["statement"], "label": "official_record", "claim_status": "documented",
                        "contradicted_by": [], "source_url": src["source_url"], "source_title": src["source_title"],
                        "source_kind": src["source_kind"], "sha256": sha, "archive_url": src.get("archive_url"),
                        "event_date": src["event_date"], "retrieved": src["retrieved"],
                        "verification": "unverified", "verified_on": None, "reviewer": None,
                        **({"notes": rec["notes"]} if rec.get("notes") else {})}
                doc["records"].append(fact)
                doc.pop("research", None)
                people[target] = doc
                notes.append(f"ADD {fact['id']}: {rec['headline']}")
    return people, notes


def main(argv: list[str] | None = None) -> int:
    import datetime as dt
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--today", default=dt.date.today().isoformat())
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    people, notes = plan(args.files, args.today)
    for n in notes:
        print(n)
    if args.write:
        for target, doc in people.items():
            head = target.read_text(encoding="utf-8").split("\n", 1)[0]
            target.write_text((head + "\n" if head.startswith("#") else "") + to_yaml(doc), encoding="utf-8")
    added = sum(n.startswith("ADD") for n in notes)
    print(f"{added} records {'added' if args.write else 'to add'} to {len(people)} people; "
          f"{sum(n.startswith('REFUSE') for n in notes)} refused")
    return 0


if __name__ == "__main__":
    sys.exit(main())
