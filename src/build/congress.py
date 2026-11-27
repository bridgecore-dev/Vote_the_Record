"""U.S. Congress roll calls (data/federal/congress/) arranged for the /congress/ pages.

Every member is listed exactly as the roll call prints them: name, party, state, vote (CLAUDE.md rule 5).
Groups follow the record's own order of answers (Yea, Nay, Present, Not Voting); members within a group are
alphabetical by the name the record prints, the same rule for everyone. Party never sorts, groups, or colors.
"""

from __future__ import annotations

import csv
import io
import re

from model import STATE_NAMES

CHAMBERS = {"house": "U.S. House of Representatives", "senate": "U.S. Senate"}
GROUPS = [("yea", "Yea"), ("nay", "Nay"), ("present", "Present"), ("not_voting", "Not Voting")]
GROUP_OF = {"Yea": "yea", "Aye": "yea", "Guilty": "yea", "Nay": "nay", "No": "nay", "Not Guilty": "nay",
            "Present": "present", "Not Voting": "not_voting"}
SESSION_WORD = {1: "1st", 2: "2nd", 3: "3rd"}


def initials(name: str) -> str:
    """'Alma S. Adams' -> 'AA'; shown only when a member has no official portrait."""
    words = [w for w in name.replace('"', " ").replace(",", " ").split()
             if w[:1].isalpha() and w.rstrip(".").lower() not in ("jr", "sr", "ii", "iii", "iv")]
    words = words or [name]
    return (words[0][0] + (words[-1][0] if len(words) > 1 else "")).upper()


def sort_key(m: dict) -> tuple:
    return (m["name"].casefold(), (m.get("full_name") or "").casefold(), m["state"])


def member_key(m: dict) -> str:
    return m.get("bioguide") or f"{m['name']}|{m['state']}"


def member_url(key: str) -> str:
    """/congress/members/<bioguide>/; a member the directory couldn't match gets a slug of name and state."""
    return f"/congress/members/{re.sub(r'[^a-z0-9]+', '-', key.casefold()).strip('-')}/"


def assemble_vote(rel: str, doc: dict) -> dict:
    slug = f"{doc['congress']}-{doc['session']}/{doc['chamber']}/{doc['roll']}"
    url = f"/congress/{slug}/"
    members = []
    for m in sorted(doc["members"], key=sort_key):
        display = m.get("full_name") or m["name"]
        members.append({
            **m, "key": member_key(m), "display": display, "initials": initials(display),
            "group": GROUP_OF[m["vote"]],
            "portrait_src": f"/congress/portraits/{m['portrait']}" if m.get("portrait") else None,
            "profile_url": None, "member_url": member_url(member_key(m)),
        })
    groups = [{"key": key, "label": label, "count": doc["totals"][key],
               "members": [m for m in members if m["group"] == key]} for key, label in GROUPS]
    states = sorted({m["state"] for m in members})
    return {
        **doc, "rel": rel, "url": url, "slug": slug, "csv_name": f"roll-{doc['congress']}-{doc['session']}-"
        f"{doc['chamber']}-{doc['roll']}.csv", "chamber_name": CHAMBERS[doc["chamber"]],
        "session_word": SESSION_WORD.get(doc["session"], str(doc["session"])),
        "heading": doc.get("title") or doc["question"], "members": members, "groups": groups,
        "states": states, "id": f"{doc['congress']}-{doc['session']}-{doc['chamber']}-{doc['roll']}",
        "label": f"{CHAMBERS[doc['chamber']]} roll call {doc['roll']}"
                 + (f" · {doc['measure']}" if doc.get("measure") else ""),
    }


def to_csv(vote: dict) -> str:
    """The roll call as a CSV: one row per member, as the record prints them, plus the source."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["chamber", "congress", "session", "roll", "date", "question", "measure", "result",
                "name_as_printed", "full_name", "bioguide", "party", "state", "vote", "source_url",
                "verification"])
    for m in vote["members"]:
        w.writerow([vote["chamber_name"], vote["congress"], vote["session"], vote["roll"], vote["date"],
                    vote["question"], vote.get("measure") or "", vote["result"], m["name"], m.get("full_name") or "",
                    m.get("bioguide") or "", m["party"], m["state"], m["vote"], vote["fact"]["source_url"],
                    vote["fact"]["verification"]])
    return out.getvalue()


def assemble_congress(docs: dict) -> dict:
    votes = [assemble_vote(rel, doc) for rel, (kind, doc) in docs.items() if kind == "congress_roll"]
    votes.sort(key=lambda v: (v["date"], v["chamber"], v["roll"]), reverse=True)  # newest first
    people: dict[str, dict] = {}
    for v in votes:
        for m in v["members"]:
            people.setdefault(m["key"], {"n": m["display"], "s": m["state"], "p": m["party"],
                                         "c": v["chamber"], "i": m["portrait_src"]})
    compare = {
        "members": dict(sorted(people.items(), key=lambda kv: (kv[1]["n"].split()[-1].casefold(), kv[1]["n"]))),
        "votes": [{"id": v["id"], "label": v["label"], "heading": v["heading"], "date": v["date"],
                   "url": v["url"], "chamber": v["chamber"],
                   "v": {m["key"]: m["vote"] for m in v["members"]}} for v in votes],
    }
    portraits = sorted({m["portrait"] for v in votes for m in v["members"] if m.get("portrait")})
    return {"votes": votes, "compare": compare, "portraits": portraits,
            "states": sorted({m["state"] for v in votes for m in v["members"]})}


SEAT_CHAMBER = {"us-senator": "senate", "us-representative": "house"}
SUFFIXES = ("jr", "sr", "ii", "iii", "iv")


def _name_parts(name: str) -> tuple[str, str]:
    """(first initial, surname), lowercase; 'Ted Cruz' -> ('t', 'cruz'), 'A. Gene Calvert Jr.' -> ('a', 'calvert')."""
    words = [w.strip(".,").casefold() for w in name.replace('"', " ").split()]
    words = [w for w in words if w and w not in SUFFIXES]
    return (words[0][:1], words[-1]) if words else ("", "")


def link_people(congress: dict, people: list[dict]) -> None:
    """Link a roll-call member to the profile of the person holding that state's seat in that chamber now.

    Links only when exactly one current seat holder in the member's state and chamber has the same surname and
    first initial as the roll call's full name; otherwise the member stays unlinked. Each linked person gets
    `congress_votes`, newest first."""
    holders: dict[tuple[str, str], list[dict]] = {}
    for person in people:
        for seat in person["seats"]:
            chamber = SEAT_CHAMBER.get(seat["office_type"])
            if chamber:
                holders.setdefault((person["state"].upper(), chamber), []).append(person)
    for person in people:
        person.setdefault("congress_votes", [])
    for vote in congress["votes"]:  # newest first already
        for m in vote["members"]:
            want = _name_parts(m.get("full_name") or "")
            if not want[1]:
                continue
            found = {id(p): p for p in holders.get((m["state"], vote["chamber"]), [])
                     if _name_parts(p["name"]) == want}
            if len(found) == 1:
                person = next(iter(found.values()))
                m["profile_url"] = person["url"]
                person["congress_votes"].append({"vote": vote, "cast": m["vote"]})


def assemble_members(congress: dict) -> None:
    """Add one page per member and per state, from the roll calls on this site (run after link_people).

    A member's name, party, and state come from their newest roll call here. Lists are alphabetical by the name the
    record prints, and a state's list is everyone who appears in these roll calls, not a roster of who serves now."""
    members: dict[str, dict] = {}
    for vote in congress["votes"]:  # newest first
        for m in vote["members"]:
            row = members.setdefault(m["key"], {
                "key": m["key"], "url": m["member_url"], "name": m["name"], "full_name": m.get("full_name"),
                "display": m["display"], "initials": m["initials"], "party": m["party"], "state": m["state"],
                "state_name": STATE_NAMES.get(m["state"], m["state"]),
                "state_url": f"/congress/states/{m['state'].lower()}/",
                "portrait_src": m["portrait_src"], "profile_url": None, "chambers": [], "votes": []})
            row["profile_url"] = row["profile_url"] or m["profile_url"]
            row["portrait_src"] = row["portrait_src"] or m["portrait_src"]
            if vote["chamber"] not in row["chambers"]:
                row["chambers"].append(vote["chamber"])
            row["votes"].append({"vote": vote, "cast": m["vote"], "party": m["party"], "state": m["state"]})
    ordered = sorted(members.values(), key=sort_key)
    for row in ordered:
        row["chamber_names"] = [CHAMBERS[c] for c in row["chambers"]]
    states = []
    for code in sorted({v_m["state"] for v in congress["votes"] for v_m in v["members"]},
                       key=lambda c: STATE_NAMES.get(c, c).casefold()):
        seen = {c: [x for x in ordered if any(r["state"] == code and r["vote"]["chamber"] == c for r in x["votes"])]
                for c in CHAMBERS}
        rolls = []
        for vote in congress["votes"]:
            voted = [m for m in vote["members"] if m["state"] == code]
            if voted:
                rolls.append({"vote": vote, "counts": [{"label": label, "n": sum(m["group"] == key for m in voted)}
                                                       for key, label in GROUPS]})
        states.append({"code": code, "name": STATE_NAMES.get(code, code), "url": f"/congress/states/{code.lower()}/",
                       "senate": seen["senate"], "house": seen["house"], "rolls": rolls})
    congress["member_pages"], congress["state_pages"] = ordered, states

