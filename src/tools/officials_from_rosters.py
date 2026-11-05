"""Seat and person files (docs/OFFICIALS.md) from official rosters saved to disk.

Usage:
  python src/tools/officials_from_rosters.py <roster.json> [<roster.json> ...] [--write]

Each roster.json describes one official page or document (a government's own list of who holds
its offices) and the seats it shows:

  {"source_url": "https://...", "source_title": "Bexar County: Elected Officials",
   "source_kind": "page" | "document", "retrieved": "2026-11-05",
   "saved": "bexar-elected-officials.txt",        # the page as saved, next to roster.json
   "original": "elected-officials.pdf",            # documents only: the downloaded file, for sha256
   "archive_url": null,
   "seats": [{"seat": "tx/localities/bexar-county/offices/county-judge", "name": "County Judge",
              "office_type": "county-judge", "area": null,
              "holders": ["Peter Sakai"], "quote": "County Judge Peter Sakai"}]}

A holder is the name as printed, or {"printed": "Alders, Daniel", "name": "Daniel Alders"} when the page prints
it last name first: the two must have the same words. Every holder's printed name and every quote must appear in the saved text (spacing and capitals aside), or
the seat is refused: nothing is filled in from memory (CLAUDE.md rule 3). A seat file that already
exists is left alone; one whose holders differ from the roster is reported, never changed. A
person is reused only when their file already has this name and holds a seat in the same place.
Writes only unverified facts (rule 3a). Preview first; --write to apply.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_officials import longdate, slugify, to_yaml  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "states"
SEAT = re.compile(r"^[a-z]{2}/(statewide|districts/[a-z0-9-]+|localities/[a-z0-9-]+)/offices/[a-z0-9]+(-[a-z0-9]+)*$")


def norm(text: str) -> str:
    text = text.replace(" ", " ").replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().casefold()


def place_of(seat: str) -> str:
    return seat.split("/offices/")[0]


def existing_people() -> dict[tuple[str, str], dict]:
    """(state, slug) -> {"name", "places"} for every person file and the places of their seats."""
    out = {}
    for f in DATA.glob("*/people/*.yaml"):
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        out[(f.parts[-3], f.stem)] = {"name": doc["name"], "places": set()}
    for f in DATA.glob("*/**/offices/*.yaml"):
        rel = f.relative_to(DATA).as_posix().removesuffix(".yaml")
        for h in yaml.safe_load(f.read_text(encoding="utf-8")).get("holders") or []:
            if (rel.split("/")[0], h["person"]) in out:
                out[(rel.split("/")[0], h["person"])]["places"].add(place_of(rel))
    return out


def plan(rosters: list[Path]) -> tuple[list[tuple[Path, str]], list[str]]:
    people = existing_people()
    writes, notes = [], []
    offices = {p.name for p in (REPO / "offices").iterdir() if (p / "powers.yaml").exists()}
    for path in rosters:
        roster = json.loads(path.read_text(encoding="utf-8"))
        saved = path.parent / roster["saved"]
        if not saved.is_file():
            notes.append(f"REFUSE {path}: saved page {saved} not found")
            continue
        if not re.match(r"^https?://\S+$", roster.get("source_url") or ""):
            notes.append(f"REFUSE {path}: source_url {roster.get('source_url')!r} is not a web address")
            continue
        text = norm(saved.read_text(encoding="utf-8", errors="replace"))
        kind = roster["source_kind"]
        sha = None
        if kind == "document":
            original = path.parent / (roster.get("original") or "")
            if not roster.get("original") or not original.is_file():
                notes.append(f"REFUSE {path}: a document roster needs its downloaded file ('original') for sha256")
                continue
            sha = hashlib.sha256(original.read_bytes()).hexdigest()
        dt.date.fromisoformat(roster["retrieved"])
        for seat in roster["seats"]:
            rel = seat["seat"]
            printed = [h["printed"] if isinstance(h, dict) else h for h in seat["holders"]]
            names = [h["name"] if isinstance(h, dict) else h for h in seat["holders"]]
            reordered = [h for h in seat["holders"] if isinstance(h, dict)
                         and sorted(norm(h["printed"]).replace(",", " ").split()) != sorted(norm(h["name"]).split())]
            missing = [n for n in printed + [seat["quote"]] if norm(n) not in text]
            if not SEAT.match(rel):
                notes.append(f"REFUSE {rel}: not a seat path (<st>/<scope>/offices/<seat>)")
            elif seat["office_type"] not in offices:
                notes.append(f"REFUSE {rel}: office type {seat['office_type']} has no powers.yaml")
            elif missing:
                notes.append(f"REFUSE {rel}: not in the saved page: {missing}")
            elif reordered:
                notes.append(f"REFUSE {rel}: a name is not the printed name's words: {reordered}")
            elif not seat["holders"] and not seat.get("vacant"):
                notes.append(f"REFUSE {rel}: no holders and not marked vacant")
            else:
                target = DATA / f"{rel}.yaml"
                state = rel.split("/")[0]
                if target.exists():
                    old = yaml.safe_load(target.read_text(encoding="utf-8"))
                    now = sorted(people.get((state, h["person"]), {"name": h["person"]})["name"]
                                 for h in old["holders"] if not h["end"])
                    if sorted(norm(n) for n in now) != sorted(norm(n) for n in names):
                        notes.append(f"DIFFERS {rel}: on file {now}, roster {names} ({roster['source_url']})")
                    continue
                slugs = []
                for name in names:
                    slug = slugify(name)
                    have = people.get((state, slug))
                    if have and not (norm(have["name"]) == norm(name) and place_of(rel) in have["places"]):
                        slug = f"{slug}-{'statewide' if rel.split('/')[1] == 'statewide' else rel.split('/')[2]}"
                        have = people.get((state, slug))
                        if have and norm(have["name"]) != norm(name):
                            notes.append(f"REFUSE {rel}: person ID {slug} is taken by {have['name']}")
                            break
                    if not have:
                        people[(state, slug)] = {"name": name, "places": set()}
                        writes.append((DATA / state / "people" / f"{slug}.yaml",
                                       "# One person's record in office (docs/OFFICIALS.md). Unverified.\n" + to_yaml(
                                           {"id": slug, "name": name, "candidacies": [], "research": "pending",
                                            "records": [], "statements": [], "funding": [], "promise_tracker": []})))
                    people[(state, slug)]["places"].add(place_of(rel))
                    slugs.append(slug)
                else:
                    fid = f"{rel}#S-01"
                    who = " and ".join(names) if names else "nobody"
                    doc = {"name": seat["name"], "office_type": seat["office_type"],
                           **({"area": seat["area"]} if seat.get("area") else {}),
                           **({"vacant": True} if not seat["holders"] else {}),
                           "holders": [{"person": s, "start": seat.get("start"), "end": None, "basis": fid}
                                       for s in slugs],
                           "sources": [{
                               "id": fid, "headline": "Who holds this office now",
                               "statement": f"The official {'list' if kind == 'page' else 'document'} "
                                            f"({roster['source_title']}), checked on {longdate(roster['retrieved'])}, "
                                            f"shows {who} as {seat['name']}. It reads: \"{seat['quote']}\"",
                               "label": "official_record", "claim_status": "documented", "contradicted_by": [],
                               "source_url": roster["source_url"], "source_title": roster["source_title"],
                               "source_kind": kind, "sha256": sha, "archive_url": roster.get("archive_url"),
                               "event_date": roster["retrieved"], "retrieved": roster["retrieved"],
                               "verification": "unverified", "verified_on": None, "reviewer": None}]}
                    writes.append((target, "# Who holds this seat, from official rosters. Unverified.\n" + to_yaml(doc)))
    return writes, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rosters", nargs="+", type=Path, help="roster files, or @list.txt naming one per line")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    rosters = [Path(line) for p in map(str, args.rosters)
               for line in (Path(p[1:]).read_text(encoding="utf-8").split() if p.startswith("@") else [p])]
    writes, notes = plan(rosters)
    for n in notes:
        print(n)
    for target, text in writes:
        print(f"{'WRITE' if args.write else 'WOULD WRITE'} {target.relative_to(REPO).as_posix()}")
        if args.write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
    seats = sum(1 for t, _ in writes if "/offices/" in t.as_posix())
    print(f"{seats} seats and {len(writes) - seats} people {'written' if args.write else 'to write'}; "
          f"{sum(n.startswith('REFUSE') for n in notes)} refused, {sum(n.startswith('DIFFERS') for n in notes)} differ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
