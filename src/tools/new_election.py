"""Start a new election's ballot from the official sample ballot.

Usage:
  python src/tools/new_election.py --spec <spec.yaml> [--write]

The spec is a short YAML file transcribed from the sample ballot (see docs/MAINTAINING.md for
a full example):

  ballot: tx/localities/travis-county/elections/2027-05-01   # where ballot.yaml goes
  name: Travis County ballot
  election_date: "2027-05-01"
  election_label: May 1, 2027 joint election                  # used in fact statements
  lookup_url: https://...                                    # the county's "what's on my ballot"
  sample_ballot: {issuer: Travis County Clerk, url: ..., title: ..., sha256: ..., archive_url: ..., retrieved: ...,
                  notes: ...}   # notes optional, copied onto every fact
  write_in_list: {url: ..., title: ..., sha256: ..., archive_url: ..., retrieved: ...}   # optional
  localities: {city-of-austin: city, austin-isd: school-district}   # optional: writes locality.yaml if missing
  contests:                                                  # in ballot order
    - existing: tx/statewide/elections/2027-05-01/governor   # already in the data: just list it
    - race: tx/localities/city-of-austin/elections/2027-05-01/council-district-3
      name: Austin City Council, District 3
      office_type: city-council-member
      page: 2
      seats: 1                                               # optional
      area: travis-county/commissioner-2                     # optional: the voting area, if not the folder's
      candidates: [{name: JANE DOE, party: null}]            # exactly as printed
      write_ins: [Pat Roe]                                   # optional, from the write-in list
    - measure: tx/localities/city-of-austin/elections/2027-05-01/proposition-a
      name: City of Austin, Proposition A
      kind: charter-amendment
      page: 3
      ballot_text: "..."                                     # exactly as printed
      choices: [FOR, AGAINST]

Every race starts as a basic page (detail: basic) with every candidate's incumbent status
unknown. Nothing is overwritten: existing race or candidate files are skipped, and contests
are appended to an existing ballot.yaml. Without --write it only lists what it would create.
Everything is written unverified (CLAUDE.md rule 3a).
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, read_doc, write_doc  # noqa: E402

EMPTY = {"records": [], "funding": [], "endorsements": [],
         "running_on": {"policy_proposals": [], "attack_messaging": [], "contested_claims": []},
         "mappings": [], "promise_tracker": []}


def slug(name: str) -> str:
    """'Yvonne Muñoz' -> 'yvonne-munoz'; quotes and nickname marks dropped."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"[\"']", "", plain.lower())).strip("-")


def fact(fid: str, headline: str, statement: str, src: dict, title: str, date: str) -> dict:
    f = {"id": fid, "headline": headline, "statement": statement, "label": "official_record",
         "claim_status": "documented", "contradicted_by": [], "source_url": src["url"], "source_title": title,
         "source_kind": "document", "sha256": src.get("sha256"), "archive_url": src.get("archive_url"),
         "event_date": date, "retrieved": str(src["retrieved"]), "verification": "unverified",
         "verified_on": None, "reviewer": None}
    if src.get("notes"):  # e.g. how the text was read from a ballot with no text layer
        f["notes"] = src["notes"]
    return f


def plan(root: Path, spec: dict) -> list[tuple[Path, str, dict]]:
    """(file, header comment, document) for every file to write."""
    date = str(spec["election_date"])
    sb = spec["sample_ballot"]
    issuer, label = sb["issuer"], spec["election_label"]
    owner = issuer + ("'" if issuer.endswith("s") else "'s")  # "Elections' sample ballot", "Clerk's sample ballot"
    out: list[tuple[Path, str, dict]] = []
    contests = []
    for c in spec["contests"]:
        if "existing" in c:  # a race or measure already in the data (e.g. statewide): only listed on this ballot
            folder = root / "data/states" / c["existing"]
            assert (folder / "race.yaml").exists() or (folder / "measure.yaml").exists(), c["existing"]
            contests.append(c["existing"])
            continue
        path = c.get("race") or c.get("measure")
        assert path.startswith(spec["ballot"].split("/")[0] + "/") and f"/elections/{date}/" in path, path
        contests.append(path)
        folder = root / "data/states" / path
        page_title = f"{issuer}: {sb['title']} (page {c['page']})"
        if "measure" in c:
            if (folder / "measure.yaml").exists():
                continue
            text = (f'The {owner} sample ballot for the {label} prints "{c["name"]}" on page {c["page"]} with the '
                    f'choices {" and ".join(c["choices"])} and this English wording: "{c["ballot_text"]}"')
            doc = {"name": c["name"], "election_date": date, "kind": c["kind"], "ballot_text": c["ballot_text"],
                   "choices": c["choices"], "explained": [],
                   "sources": [fact(f"{path}/measure#S-01", "Proposition as printed on the sample ballot", text, sb,
                                    page_title, date)]}
            out.append((folder / "measure.yaml",
                        "# Proposition: exact wording and choices as printed on the sample ballot. Unverified.\n", doc))
            continue
        cands = c.get("candidates") or []
        listed = "; ".join(f"{x['name']}" + (f" ({x['party']})" if x.get("party") else "") for x in cands)
        sources = [fact(f"{path}/race#S-01", "Candidates as printed on the sample ballot",
                        f'The {owner} sample ballot for the {label} lists "{c.get("ballot_title", c["name"])}" on '
                        f"page {c['page']}, with these candidates: {listed}.", sb, page_title, date)]
        if c.get("write_ins"):
            wl = spec["write_in_list"]
            sources.append(fact(f"{path}/race#S-02", "Declared write-in candidates on the official list",
                                f"The {wl['title']} lists these write-in candidates for {c.get('ballot_title', c['name'])}: "
                                f"{', '.join(c['write_ins'])}.", wl, wl["title"], date))
        if not (folder / "race.yaml").exists():
            race = {"name": c["name"], "office_type": c["office_type"], "election_date": date, "detail": "basic"}
            if c.get("area"):
                race["area"] = c["area"]  # e.g. travis-county/commissioner-2, when the folder doesn't say it
            if c.get("seats", 1) > 1:
                race["seats"] = c["seats"]
            if c.get("notes"):
                race["notes"] = c["notes"]
            race["sources"] = sources
            out.append((folder / "race.yaml",
                        "# Basic race page: the office and candidates as printed on the sample ballot. Unverified.\n", race))
        people = [(x["name"], x.get("party"), False) for x in cands] + [(n, None, True) for n in c.get("write_ins") or []]
        for name, party, write_in in people:
            target = folder / "candidates" / f"{slug(name)}.yaml"
            if target.exists():
                continue
            doc = {"id": slug(name), "name": name, "incumbent": None, "ballot_party": party}
            if write_in:
                doc["write_in"] = True
            doc.update(EMPTY)
            out.append((target, "# Basic race: name and ballot label as printed on the sample ballot. Unverified.\n", doc))

    # Localities new to this ballot get a locality.yaml (cities, school districts, special districts).
    state = spec["ballot"].split("/")[0]
    for slug_, kind in (spec.get("localities") or {}).items():
        target = root / "data/states" / state / "localities" / slug_ / "locality.yaml"
        if not target.exists():
            out.append((target, f"# {kind.replace('-', ' ').capitalize()} on the {spec['name']}.\n",
                        {"locality": slug_, "type": kind, "state": state.upper(), "sources": []}))

    ballot_file = root / "data/states" / spec["ballot"] / "ballot.yaml"
    if ballot_file.exists():
        header, ballot = read_doc(ballot_file)
        new = [c for c in contests if c not in ballot["contests"]]
        if new:
            ballot["contests"] = ballot["contests"] + new
            out.append((ballot_file, header, ballot))
    else:
        ballot = {"name": spec["name"], "election_date": date}
        if spec.get("lookup_url"):
            ballot["lookup_url"] = spec["lookup_url"]
        ballot["complete"] = False
        ballot["sources"] = [fact(f"{spec['ballot']}/ballot#S-01", "Every contest on the sample ballot",
                                  f"The {owner} sample ballot for the {label} lists every contest on this ballot.",
                                  sb, f"{issuer}: {sb['title']}", date)]
        ballot["contests"] = contests
        out.append((ballot_file, "# Contests on this ballot, in ballot order. complete: false until every contest is added.\n",
                    ballot))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    spec = yaml.safe_load(args.spec.read_text(encoding="utf-8"))
    files = plan(args.root, spec)
    for path, _h, _d in files:
        print(("WRITE " if args.write else "WOULD WRITE ") + path.relative_to(args.root).as_posix())
    if args.write:
        for path, header, doc in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_doc(path, header, doc)
        print(f"Wrote {len(files)} file(s). Next: python src/validate/validate.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
