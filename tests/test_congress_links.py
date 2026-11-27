"""Roll-call members link to a profile only on a unique same-state, same-chamber seat-holder match."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "build"))
from congress import GROUPS, assemble_members, assemble_vote, link_people  # noqa: E402


def person(slug, name, office_type):
    return {"url": f"/people/tx/{slug}/", "name": name, "state": "tx",
            "seats": [{"office_type": office_type}]}


def vote(chamber, members):
    return {"chamber": chamber, "members": members}


def test_links_unique_match_and_skips_others():
    cruz = person("ted-cruz", "Ted Cruz", "us-senator")
    rep = person("christian-dashaun-menefee", "Christian Dashaun Menefee", "us-representative")
    a = person("al-green", "Al Green", "us-representative")
    b = person("a-green", "Ann Green", "us-representative")  # same initial + surname as a: ambiguous
    sen = vote("senate", [{"full_name": "Ted Cruz", "state": "TX", "vote": "Nay"},
                          {"full_name": "Rafael Cruz", "state": "TX", "vote": "Yea"},    # other first initial
                          {"full_name": "Ted Cruz", "state": "FL", "vote": "Yea"}])      # other state
    house = vote("house", [{"full_name": "Christian D. Menefee", "state": "TX", "vote": "Yea"},
                           {"full_name": "Al Green", "state": "TX", "vote": "Yea"},
                           {"full_name": "Ted Cruz", "state": "TX", "vote": "Yea"}])     # senator, not a House seat
    link_people({"votes": [sen, house]}, [cruz, rep, a, b])
    assert sen["members"][0]["profile_url"] == "/people/tx/ted-cruz/"
    assert "profile_url" not in sen["members"][1] and "profile_url" not in sen["members"][2]
    assert house["members"][0]["profile_url"] == "/people/tx/christian-dashaun-menefee/"
    assert "profile_url" not in house["members"][1]
    assert "profile_url" not in house["members"][2]
    assert [cv["cast"] for cv in cruz["congress_votes"]] == ["Nay"]
    assert a["congress_votes"] == [] and b["congress_votes"] == []


def roll(chamber, number, date, members):
    return assemble_vote(f"data/federal/congress/119/2/{chamber}/roll-{number}.yaml", {
        "congress": 119, "session": 2, "chamber": chamber, "roll": number, "date": date, "question": "On Passage",
        "measure": None, "title": "A bill", "result": "Passed", "required": None,
        "totals": {key: sum(m["vote"] == label for m in members) for key, label in GROUPS}, "party_totals": None,
        "tie_breaker": None, "fact": {"source_url": "https://example.gov/x", "verification": "unverified"},
        "directory": {}, "members": members})


def test_member_and_state_pages():
    def m(name, bio, party, state, cast):
        return {"name": name, "full_name": name, "bioguide": bio, "party": party, "state": state, "vote": cast,
                "portrait": None}
    new = roll("house", 2, "2026-02-01", [m("Zed Young", "Z000001", "I", "TX", "Yea"),
                                          m("Amy Ames", "A000001", "D", "TX", "Nay"),
                                          m("Bo Bell", "B000001", "R", "OK", "Yea")])
    old = roll("house", 1, "2026-01-01", [m("Zed Young", "Z000001", "R", "TX", "Nay")])
    congress = {"votes": [new, old]}
    assemble_members(congress)
    assert [x["display"] for x in congress["member_pages"]] == ["Amy Ames", "Bo Bell", "Zed Young"]
    zed = congress["member_pages"][2]
    assert zed["url"] == "/congress/members/z000001/" and zed["party"] == "I"  # newest roll call's listing
    assert [(v["cast"], v["party"]) for v in zed["votes"]] == [("Yea", "I"), ("Nay", "R")]
    tx = next(s for s in congress["state_pages"] if s["code"] == "TX")
    assert tx["name"] == "Texas" and [x["display"] for x in tx["house"]] == ["Amy Ames", "Zed Young"]
    assert {c["label"]: c["n"] for c in tx["rolls"][0]["counts"]}["Yea"] == 1

