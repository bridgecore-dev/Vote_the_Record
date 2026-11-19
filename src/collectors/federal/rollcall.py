"""U.S. Congress roll-call votes from the official XML into data/federal/congress/<congress>/<session>/<chamber>/.

Usage:
  python src/collectors/federal/rollcall.py --house 2026:307 [--house 2025:145 ...]
                                           --senate 119:1:372 [--senate ...] [--write] [--no-portraits]

Without --write it only prints what it would write.

Sources (CLAUDE.md rule 1):
  - House: the Clerk's roll-call XML, https://clerk.house.gov/evs/<year>/roll<NNN>.xml
  - Senate: the Senate's LIS vote XML,
    https://www.senate.gov/legislative/LIS/roll_call_votes/vote<congress><session>/vote_<congress>_<session>_<NNNNN>.xml
Each file becomes one roll call: the question, measure, result and totals as printed, and every member with the
name, party, state and vote exactly as the record prints them (rule 5: party only as the roll call lists it, and
the House Clerk's totals by party). The roll call itself is one fact, an official_record document with the XML's
sha256, written unverified (rule 3a); archive_url stays null until `python src/tools/archive.py --write`.

Full names and portraits (shown with every member, the same for all): the public-domain unitedstates project
(congress-legislators for full names and to match a Senate LIS ID to a Bioguide ID; unitedstates/images for the
official portraits, 225x275, saved at 150x184 with Pillow). Portraits are saved under data/federal/congress/portraits/<bioguide>.jpg and served
from our own site, never hotlinked. A member with no portrait gets initials on the site.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src" / "collectors"))
from yamlio import to_yaml  # noqa: E402

OUT = REPO / "data" / "federal" / "congress"
PORTRAITS = OUT / "portraits"
UA = "VoteTheRecord/1.0 (nonpartisan civic records; +https://github.com/bridgecore-dev/Vote_the_Record)"
HOUSE_URL = "https://clerk.house.gov/evs/{year}/roll{num:03d}.xml"
SENATE_URL = ("https://www.senate.gov/legislative/LIS/roll_call_votes/vote{congress}{session}/"
              "vote_{congress}_{session}_{num:05d}.xml")
DIRECTORY = "https://unitedstates.github.io/congress-legislators/legislators-{which}.json"
PORTRAIT_URL = "https://unitedstates.github.io/images/congress/225x275/{bioguide}.jpg"
ORDINAL = {"1st": 1, "2nd": 2, "3rd": 3}
# How the vote reads on our pages; the record's own word is always kept in "vote".
YES, NO = {"Yea", "Aye", "Guilty"}, {"Nay", "No", "Not Guilty"}


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def text(node, path: str) -> str:
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def num(node, path: str) -> int:
    value = text(node, path)
    return int(value) if value.isdigit() else 0


def group(vote: str) -> str:
    """yes / no / present / not_voting, from the vote as printed."""
    if vote in YES:
        return "yes"
    if vote in NO:
        return "no"
    return "present" if vote == "Present" else "not_voting"


def parse_house(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    meta = root.find("vote-metadata")
    date = dt.datetime.strptime(text(meta, "action-date"), "%d-%b-%Y").date().isoformat()
    party_totals = [
        {"party": text(t, "party"), "yea": num(t, "yea-total"), "nay": num(t, "nay-total"),
         "present": num(t, "present-total"), "not_voting": num(t, "not-voting-total")}
        for t in meta.findall("vote-totals/totals-by-party")
    ]
    totals = meta.find("vote-totals/totals-by-vote")
    members = []
    for rv in root.findall("vote-data/recorded-vote"):
        leg = rv.find("legislator")
        members.append({"name": (leg.text or "").strip(), "bioguide": leg.get("name-id") or None, "lis": None,
                        "party": leg.get("party"), "state": leg.get("state"), "vote": text(rv, "vote")})
    return {
        "congress": int(text(meta, "congress")), "session": ORDINAL[text(meta, "session")], "chamber": "house",
        "roll": int(text(meta, "rollcall-num")), "date": date, "question": text(meta, "vote-question"),
        "measure": text(meta, "legis-num") or None, "title": text(meta, "vote-desc") or None,
        "result": text(meta, "vote-result"), "required": text(meta, "vote-type") or None,
        "totals": {"yea": num(totals, "yea-total"), "nay": num(totals, "nay-total"),
                   "present": num(totals, "present-total"), "not_voting": num(totals, "not-voting-total")},
        "party_totals": party_totals or None, "tie_breaker": None, "members": members,
    }


def parse_senate(xml: bytes) -> dict:
    root = ET.fromstring(xml)
    when = re.sub(r"\s+", " ", text(root, "vote_date"))
    date = dt.datetime.strptime(when.split(",")[0] + "," + when.split(",")[1], "%B %d, %Y").date().isoformat()
    members = [{"name": text(m, "member_full"), "bioguide": None, "lis": text(m, "lis_member_id") or None,
                "party": text(m, "party"), "state": text(m, "state"), "vote": text(m, "vote_cast")}
               for m in root.findall("members/member")]
    counts = {"yea": 0, "nay": 0, "present": 0, "not_voting": 0}
    for m in members:  # the XML's <count> leaves Present and Absent blank when zero; count every member instead
        counts[{"yes": "yea", "no": "nay"}.get(group(m["vote"]), group(m["vote"]))] += 1
    if (counts["yea"], counts["nay"]) != (num(root, "count/yeas"), num(root, "count/nays")):
        raise SystemExit(f"Senate vote {text(root, 'vote_number')}: member votes don't add up to the printed count")
    tie = None
    if text(root, "tie_breaker/by_whom"):
        tie = {"by": text(root, "tie_breaker/by_whom"), "vote": text(root, "tie_breaker/tie_breaker_vote")}
    return {
        "congress": int(text(root, "congress")), "session": int(text(root, "session")), "chamber": "senate",
        "roll": int(text(root, "vote_number")), "date": date, "question": text(root, "vote_question_text"),
        "measure": text(root, "document/document_name") or None,
        "title": text(root, "vote_document_text") or text(root, "vote_title") or None,
        "result": text(root, "vote_result_text") or text(root, "vote_result"),
        "required": text(root, "majority_requirement") or None,
        "totals": counts, "party_totals": None, "tie_breaker": tie, "members": members,
    }


def directory(which: str = "current") -> dict:
    """Bioguide ID -> full name, and "lis:<Senate LIS ID>" -> Bioguide ID, from the unitedstates project."""
    people = {}
    for p in json.loads(fetch(DIRECTORY.format(which=which))):
        ids, name = p.get("id") or {}, p.get("name") or {}
        if ids.get("bioguide"):
            people[ids["bioguide"]] = name.get("official_full") or " ".join(
                x for x in (name.get("first"), name.get("last")) if x)
            if ids.get("lis"):
                people["lis:" + ids["lis"]] = ids["bioguide"]
    return people


def fill_names(roll: dict, people: dict) -> list[str]:
    """Add bioguide (Senate) and full_name to each member; returns members it couldn't match."""
    missing = []
    for m in roll["members"]:
        if not m["bioguide"] and m["lis"]:
            m["bioguide"] = people.get("lis:" + m["lis"])
        m["full_name"] = people.get(m["bioguide"]) if m["bioguide"] else None
        if not m["full_name"]:
            missing.append(m["name"])
    return missing


def portrait(bioguide: str | None, write: bool) -> str | None:
    if not bioguide:
        return None
    path = PORTRAITS / f"{bioguide}.jpg"
    if path.is_file():
        return path.name
    if not write:
        return path.name + " (to download)"
    try:
        data = fetch(PORTRAIT_URL.format(bioguide=bioguide))
    except Exception:  # noqa: BLE001 - no official portrait published yet: initials instead
        return None
    PORTRAITS.mkdir(parents=True, exist_ok=True)
    path.write_bytes(shrink(data))
    return path.name


PORTRAIT_SIZE = (150, 184)  # every portrait the same size (225x275 scaled down)


def shrink(data: bytes) -> bytes:
    """The same small JPEG for every member, so the pages stay light: Pillow if installed, else ffmpeg."""
    import io

    try:
        from PIL import Image  # requirements-dev.txt
    except ImportError:
        return _shrink_ffmpeg(data)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = img.resize(PORTRAIT_SIZE, Image.LANCZOS) if img.size != PORTRAIT_SIZE else img
    out = io.BytesIO()
    img.save(out, "JPEG", quality=78, optimize=True, progressive=True)
    return out.getvalue()


def _shrink_ffmpeg(data: bytes) -> bytes:
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        raise SystemExit("Shrinking portraits needs Pillow (pip install -r requirements-dev.txt) or ffmpeg")
    w, h = PORTRAIT_SIZE
    done = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-vf", f"scale={w}:{h}:flags=lanczos",
                           "-q:v", "5", "-f", "image2", "-c:v", "mjpeg", "pipe:1"], input=data, capture_output=True,
                          check=True)
    return done.stdout


def chamber_name(chamber: str) -> str:
    return "U.S. House of Representatives" if chamber == "house" else "U.S. Senate"


def build_doc(roll: dict, url: str, xml: bytes, today: str, names_retrieved: str) -> dict:
    rel = f"data/federal/congress/{roll['congress']}/{roll['session']}/{roll['chamber']}/roll-{roll['roll']}.yaml"
    prefix = "us/" + rel.removeprefix("data/federal/").removesuffix(".yaml")
    t = roll["totals"]
    label = roll["measure"] + ", " if roll["measure"] else ""
    statement = (f"{chamber_name(roll['chamber'])} roll call {roll['roll']} ({roll['congress']}th Congress, session "
                 f"{roll['session']}), {roll['date']}: {roll['question']}, {label}{roll['result']}. "
                 f"{t['yea']} Yea, {t['nay']} Nay, {t['present']} Present, {t['not_voting']} Not Voting.")
    source_title = (f"Office of the Clerk, U.S. House of Representatives: Roll Call {roll['roll']} ({roll['date'][:4]})"
                    if roll["chamber"] == "house" else
                    f"U.S. Senate: Roll Call Vote {roll['congress']}th Congress - {roll['session']} Session, "
                    f"Vote Number {roll['roll']}")
    members = sorted(roll["members"], key=lambda m: ((m.get("full_name") or m["name"]).split()[-1].lower(),
                                                     (m.get("full_name") or m["name"]).lower()))
    return {
        "congress": roll["congress"], "session": roll["session"], "chamber": roll["chamber"], "roll": roll["roll"],
        "date": roll["date"], "question": roll["question"], "measure": roll["measure"], "title": roll["title"],
        "result": roll["result"], "required": roll["required"], "totals": roll["totals"],
        "party_totals": roll["party_totals"], "tie_breaker": roll["tie_breaker"],
        "fact": {
            "id": f"{prefix}#R-001", "record_type": "vote", "statement": statement, "label": "official_record",
            "claim_status": "documented", "source_url": url, "source_title": source_title,
            "source_kind": "document", "sha256": hashlib.sha256(xml).hexdigest(), "archive_url": None,
            "event_date": roll["date"], "retrieved": today, "verification": "unverified",
            "verified_on": None, "reviewer": None,
        },
        "directory": {"url": DIRECTORY.format(which="current"), "title": "unitedstates/congress-legislators "
                      "(public domain): full names and ID matches; portraits from unitedstates/images",
                      "retrieved": names_retrieved},
        "members": [{k: m.get(k) for k in ("name", "full_name", "bioguide", "party", "state", "vote", "portrait")}
                    for m in members],
    }, rel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--house", action="append", default=[], help="YEAR:ROLL, e.g. 2026:307")
    parser.add_argument("--senate", action="append", default=[], help="CONGRESS:SESSION:VOTE, e.g. 119:1:372")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--no-portraits", action="store_true", help="don't download missing portraits")
    args = parser.parse_args(argv)
    today = dt.date.today().isoformat()
    jobs = []
    for spec in args.house:
        year, roll = spec.split(":")
        jobs.append((HOUSE_URL.format(year=int(year), num=int(roll)), parse_house))
    for spec in args.senate:
        congress, session, vote = spec.split(":")
        jobs.append((SENATE_URL.format(congress=int(congress), session=int(session), num=int(vote)), parse_senate))
    if not jobs:
        parser.error("name at least one --house or --senate roll call")
    people, historical = directory(), False
    for url, parse in jobs:
        xml = fetch(url)
        roll = parse(xml)
        missing = fill_names(roll, people)
        if missing and not historical:  # a member who has left Congress
            people.update(directory("historical"))
            historical = True
            missing = fill_names(roll, people)
        for m in roll["members"]:
            m["portrait"] = None if args.no_portraits and not (
                m["bioguide"] and (PORTRAITS / f"{m['bioguide']}.jpg").is_file()) else portrait(m["bioguide"], args.write)
        doc, rel = build_doc(roll, url, xml, today, today)
        t = doc["totals"]
        print(f"{rel}: {doc['question']} {doc['measure'] or ''} {doc['result']} "
              f"({t['yea']}-{t['nay']}, {t['present']} present, {t['not_voting']} not voting); "
              f"{len(doc['members'])} members" + (f"; no full name for {missing}" if missing else ""))
        if args.write:
            path = REPO / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(to_yaml(doc), encoding="utf-8", newline="\n")
    if not args.write:
        print("Preview only. Add --write to save.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
