"""Seat and person files (docs/OFFICIALS.md) from the "Who holds this office now" facts already on file.

Usage:
  python src/tools/seed_officials.py [--election 2026-11-03] [--write]

Every race with a #S-HOLDER fact names the roster that shows who holds the seat. For each one:
  - the seat file <scope>/offices/<seat>.yaml (the race folder's name, without "-unexpired") gets a
    "Who holds this office now" fact restating that roster check, and the holder;
  - the holder gets a person file. An incumbent candidate's person file lists the candidacy, so the
    records gathered for the campaign show on the profile; a holder who was not running (named in
    quotes in the fact) gets a person file marked research: pending.
Rosters that name nobody are left for the roster collectors. Existing files are never changed.
Writes only unverified facts (CLAUDE.md rule 3a). Preview first; --write to apply.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import unicodedata
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "states"

# A seat whose race folder name doesn't say which seat it is.
SEAT_NAMES = {"tx/statewide/us-senate": "us-senator-class-2"}
NAMED = re.compile(r'lists "([^"]+)" as holding this office')
SMALL = {"de", "del", "la", "las", "los", "van", "von", "y"}
SUFFIX = {"ii", "iii", "iv", "jr", "jr.", "sr", "sr."}


def proper(name: str) -> str:
    """'ADRIAN GARCIA' -> 'Adrian Garcia' (ballots print capitals); mixed case is kept as printed."""
    if name != name.upper():
        return name

    def word(w: str, first: bool) -> str:
        low = w.lower()
        if low in SUFFIX:
            return low.upper() if low.rstrip(".") in ("ii", "iii", "iv") else low.capitalize()
        if low in SMALL and not first:
            return low
        out = "-".join(p[:1].upper() + p[1:] for p in low.split("-"))
        out = re.sub(r"\b(Mc|O')([a-z])", lambda m: m[1] + m[2].upper(), out)
        return re.sub(r'(["“(])([a-z])', lambda m: m[1] + m[2].upper(), out)

    return " ".join(word(w, i == 0) for i, w in enumerate(name.split()))


TITLES = re.compile(r"^(?:rep\.|sen\.|hon\.|judge|justice|the honorable)\s+", re.I)


def printed_name(name: str) -> str:
    """A roster's 'Rep. Bell Jr., Cecil' -> 'Cecil Bell Jr.': title dropped, last name moved to the end."""
    name = TITLES.sub("", name.strip())
    if m := re.fullmatch(r"([^,]+), ([^,]+)", name):
        name = f"{m[2]} {m[1]}"
    return name


def slugify(name: str) -> str:
    name = re.sub(r'["“”][^"“”]*["“”]', " ", name)  # drop quoted nicknames: Bradford "Brad" Hall -> bradford-hall
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def seat_rel(race_dir: str) -> str:
    """tx/localities/x/elections/2026-11-03/council-place-5-unexpired -> tx/localities/x/offices/council-place-5"""
    scope, _, rest = race_dir.partition("/elections/")
    slug = re.sub(r"-unexpired(-term)?$", "", rest.split("/", 1)[1])
    key = f"{scope}/{slug}"
    return f"{scope}/offices/{SEAT_NAMES.get(key, slug)}"


def seat_name(race_name: str) -> str:
    return re.sub(r"(?i)\s*[,(-]?\s*unexpired term\)?$", "", race_name).strip()


def longdate(value: str) -> str:
    d = dt.date.fromisoformat(value)
    return f"{d:%B} {d.day}, {d.year}"


def to_yaml(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)


def plan(election: str) -> tuple[dict, dict, list[str]]:
    seats, people, notes = {}, {}, []
    taken = {p.stem: p for p in DATA.glob("*/people/*.yaml")}
    for race_file in sorted(DATA.glob(f"*/**/elections/{election}/*/race.yaml")):
        race_dir = race_file.parent.relative_to(DATA).as_posix()
        race = yaml.safe_load(race_file.read_text(encoding="utf-8"))
        holder = next((s for s in race.get("sources") or [] if s["id"].endswith("#S-HOLDER")), None)
        if not holder:
            continue
        cands = [(f, yaml.safe_load(f.read_text(encoding="utf-8"))) for f in sorted((race_file.parent / "candidates").glob("*.yaml"))]
        incumbents = [(f, c) for f, c in cands if c.get("incumbent")]
        named = NAMED.search(holder["statement"])
        if not incumbents and not named:
            continue
        rel = seat_rel(race_dir)
        state = rel.split("/")[0]
        if incumbents:
            holders = [(proper(c["name"]), c["id"], f"{race_dir}/candidates/{f.stem}",
                        c.get("research") == "pending" or race.get("detail") == "basic") for f, c in incumbents]
        else:
            holders = [(printed_name(named[1]), slugify(printed_name(named[1])), None, True)]
        names = sorted(h[0] for h in holders)
        if rel in seats:
            if seats[rel]["holder_names"] != names:
                notes.append(f"SKIP {rel}: races disagree on the holder ({seats[rel]['holder_names']} / {names})")
                seats.pop(rel)
            continue
        slugs = []
        for name, slug, candidacy, pending in holders:
            if slug in people or slug in taken:
                # the same name on two seats is treated as two people until a source shows otherwise
                slug = f"{slug}-{rel.split('/')[2]}"
            if slug in people or slug in taken:
                notes.append(f"SKIP {rel}: person ID {slug} is taken")
                break
            slugs.append(slug)
        else:
            for (name, _, candidacy, pending), slug in zip(holders, slugs):
                people[slug] = {"name": name, "candidacy": candidacy, "pending": pending, "state": state}
        if len(slugs) != len(holders):
            continue
        local = f"{rel}#S-01"
        who = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
        stmt = (f"The official {'page' if 'page' in holder['statement'][:30] else 'list'} ({holder['source_title']}), "
                f"checked on {longdate(holder['event_date'])}, shows {who} holding {'this office' if len(names) == 1 else 'seats on this board'} "
                f"(recorded for the {longdate(election)} election: {race['name']}).")
        fact = {k: holder[k] for k in ("source_url", "source_title", "source_kind", "sha256", "archive_url",
                                       "event_date", "retrieved")}
        seats[rel] = {"holder_names": names, "doc": {
            "name": seat_name(race["name"]),
            "office_type": race["office_type"],
            **({"area": race["area"]} if race.get("area") else {}),
            "holders": [{"person": s, "start": None, "end": None, "basis": local} for s in slugs],
            "sources": [{"id": local, "headline": "Who holds this office now", "statement": stmt,
                         "label": "official_record", "claim_status": "documented", "contradicted_by": [],
                         **fact, "verification": "unverified", "verified_on": None, "reviewer": None,
                         "notes": f"From the roster check recorded in {holder['id']}."
                                  + (" Lists only the members whose seats were on that ballot." if len(names) > 1 else "")}],
        }, "state": state}
    return seats, people, notes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--election", default="2026-11-03")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    seats, people, notes = plan(args.election)
    new_seats = new_people = 0
    for rel, seat in seats.items():
        target = DATA / f"{rel}.yaml"
        if target.exists():
            continue
        new_seats += 1
        if args.write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# Who holds this seat, from official rosters. Unverified.\n" + to_yaml(seat["doc"]),
                              encoding="utf-8")
    seated = {h["person"] for s in seats.values() for h in s["doc"]["holders"]}
    for slug, p in people.items():
        target = DATA / p["state"] / "people" / f"{slug}.yaml"
        if target.exists() or slug not in seated:
            continue
        new_people += 1
        doc = {"id": slug, "name": p["name"], "candidacies": [p["candidacy"]] if p["candidacy"] else [],
               **({"research": "pending"} if p["pending"] else {}),
               "records": [], "statements": [], "funding": [], "promise_tracker": []}
        if args.write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# One person's record in office (docs/OFFICIALS.md). Unverified.\n" + to_yaml(doc),
                              encoding="utf-8")
    for n in notes:
        print(n)
    print(f"{'Wrote' if args.write else 'Would write'} {new_seats} seat files and {new_people} person files "
          f"({len(seats)} seats found, {len(notes)} skipped).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
