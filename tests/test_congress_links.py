"""Roll-call members link to a profile only on a unique same-state, same-chamber seat-holder match."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "build"))
from congress import link_people  # noqa: E402


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
