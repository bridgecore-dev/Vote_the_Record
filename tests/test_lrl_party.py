"""Party as the Texas Legislative Reference Library lists it (CLAUDE.md rule 5): the collector (against pages saved
from the Library, trimmed to a few rows; no network), the validator's rules, the vote totals, and the pages."""

import copy
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "lrl"
sys.path.insert(0, str(REPO / "src" / "collectors" / "states" / "tx"))
sys.path.insert(0, str(REPO / "src" / "validate"))
sys.path.insert(0, str(REPO / "src" / "build"))
import lrl_party as P  # noqa: E402
from officials import party_totals  # noqa: E402
from validate import validate  # noqa: E402

PREFIX = "tx/statewide/legislature/89r/members"
PAGES = {P.SEARCH.format(leg=89, code="H"): "search-89-H.html", P.SEARCH.format(leg=89, code="S"): "search-89-S.html",
         P.DETAIL.format(id="5737"): "member-5737.html"}


def get(url):
    return (FIXTURES / PAGES[url]).read_text(encoding="utf-8")


def fact(local, **extra):
    return {"id": f"{PREFIX}#{local}", "headline": "x", "statement": "x", "label": "official_record",
            "claim_status": "documented", "contradicted_by": [], "source_url": "https://capitol.texas.gov/x",
            "source_title": "x", "source_kind": "page", "sha256": None, "archive_url": None,
            "event_date": "2026-11-06", "retrieved": "2026-11-06", "verification": "unverified",
            "verified_on": None, "reviewer": None, **extra}


def member(chamber, district, name, printed, n, person=None):
    return {"chamber": chamber, "district": district, "name": name, "printed": printed, "person": person,
            "fact": fact(f"S-{n:03d}")}


def members_doc():
    return {"session": "89R", "members": [
        member("house", 6, "Daniel Alders", ["Alders"], 1),
        member("house", 9, "Trent Ashby", ["Ashby"], 2),
        member("house", 31, "Ryan Guillen", ["Guillen"], 3),
        member("house", 131, "Alma Allen", ["Allen"], 4),
        member("house", 150, "Someone Else", ["Else"], 5),
        member("senate", 1, "Bryan Hughes", ["Hughes"], 6),
        member("senate", 9, "Kelly Hancock", ["Hancock"], 7),
    ], "senate_roll": None}


def test_the_search_page_reads_each_term_line():
    rows = {r["name"]: r for r in P.parse_search(get(P.SEARCH.format(leg=89, code="H")))}
    assert rows["Alma Allen"]["district"] == 131 and rows["Alma Allen"]["terms"][0]["party"] == "D"
    guillen = rows["Ryan Guillen"]["terms"]  # a member listed with a different party in earlier terms
    assert [(t["legs"], t["party"]) for t in guillen] == [((88, 89), "R"), ((78, 87), "D")]
    assert P.covering(rows["Ryan Guillen"], "H", 89, 2025) == [0]
    assert P.covering(rows["Trent Ashby"], "H", 89, 2025) == [1]  # the first line is a later Senate term


def test_a_page_without_the_results_table_stops_the_run():
    with pytest.raises(P.Stop):
        P.parse_search("<html>Please verify you are a human</html>")
    with pytest.raises(P.Stop):
        P.parse_detail("<html>Access denied</html>")


def test_members_match_by_chamber_and_district_and_unmatched_go_to_notes():
    doc, log = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    party = {(m["chamber"], m["district"]): m["party"] for m in doc["members"]}
    assert party == {("house", 6): "R", ("house", 9): "R", ("house", 31): "R", ("house", 131): "D",
                     ("house", 150): None, ("senate", 1): "R", ("senate", 9): "R"}
    # Hancock, not his 2026 successor: only one row's term covers 2025
    assert list(doc["members"][0]) == ["chamber", "district", "name", "printed", "person", "party", "fact"]
    ids = [f["id"] for f in doc["party_source"]]
    assert ids == [f"{PREFIX}#P-H", f"{PREFIX}#P-H-009", f"{PREFIX}#P-S"]  # Ashby's own page is cited too
    assert all(f["verification"] == "unverified" and f["reviewer"] is None and f["sha256"] is None
               and f["source_kind"] == "page" for f in doc["party_source"])
    assert doc["party_source"][0]["source_url"] == "https://lrl.texas.gov/legeLeaders/members/membersearch.cfm?leg=89&chamber=H"
    assert "House District 150 (Someone Else): 0 Library rows" in doc["notes"]
    assert any(line.startswith("UNMATCHED House District 150") for line in log)


def test_a_name_that_disagrees_leaves_the_party_empty():
    doc = members_doc()
    doc["members"][0]["name"] = "Pat Nobody"
    out, _ = P.plan(doc, PREFIX, get, "2026-11-14")
    assert out["members"][0]["party"] is None and "names Daniel Alders" in out["notes"]


def test_a_verified_party_source_is_never_replaced():
    old, _ = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    old["party_source"][0].update(verification="verified", verified_on="2026-11-20", reviewer="owner",
                                  archive_url="https://web.archive.org/web/2026/x")
    new = copy.deepcopy(old)
    assert P.keep_reviewed(old, new) is None and new["party_source"] == old["party_source"]
    new["members"][0]["party"] = "D"
    assert "verified" in P.keep_reviewed(old, new)


# --- validator -------------------------------------------------------------------------------------------------

def validate_members(tmp_path, doc):
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    path = tmp_path / "data/states/tx/statewide/legislature/89r/members.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return validate(tmp_path).errors


def test_party_with_its_source_passes(tmp_path):
    doc, _ = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    assert validate_members(tmp_path, doc) == []


def test_party_needs_party_source(tmp_path):
    doc, _ = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    del doc["party_source"]
    assert any("party_source" in e for e in validate_members(tmp_path, doc))


def test_party_must_be_a_code_the_library_prints(tmp_path):
    doc, _ = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    doc["members"][0]["party"] = "Republican"
    assert validate_members(tmp_path, doc)


def test_party_keys_elsewhere_in_the_members_file_still_fail(tmp_path):
    doc, _ = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    doc["members"][0]["fact"]["party"] = "R"
    assert any("rule 5" in e or "Additional properties" in e for e in validate_members(tmp_path, doc))


# --- vote totals -----------------------------------------------------------------------------------------------

PARTY = {"A": "D", "B": "D", "C": "R", "E": "R", "F": "R"}


def vote(yeas, nays, every_member=False, tally=None):
    return {"tally": tally or {"yeas": len(yeas), "nays": len(nays)},
            "names": {"yeas": yeas, "nays": nays} if yeas or nays else None, "every_member": every_member}


def test_totals_by_party_in_code_order():
    assert party_totals(vote(["C", "A", "E"], ["B"]), PARTY, []) == "D 1-1, R 2-0"


def test_no_totals_unless_every_member_counted_has_a_party():
    assert party_totals(vote(["A", "Mr. Speaker"], []), PARTY, []) is None
    assert party_totals(vote(["A"], [], tally={"yeas": 2, "nays": 0}), PARTY, []) is None  # names don't add up
    assert party_totals(vote([], [], tally={"yeas": 3, "nays": 0}), PARTY, []) is None  # tally only


def test_an_every_member_senate_vote_counts_the_roll_less_the_nays():
    v = {"tally": {"yeas": 4, "nays": 1}, "names": {"nays": ["B"]}, "every_member": True}
    assert party_totals(v, PARTY, ["A", "B", "C", "E", "F"]) == "D 1-1, R 3-0"


# --- pages -----------------------------------------------------------------------------------------------------

def test_profile_shows_the_party_chip_and_votes_show_totals(tmp_path):
    from build import build
    root = tmp_path / "demo"
    shutil.copytree(REPO / "tests/fixtures/demo", root)
    shutil.copytree(REPO / "schemas", root / "schemas")
    leg = root / "data/states/tx/statewide/legislature/89r"
    leg.joinpath("bills").mkdir(parents=True)
    doc, _ = P.plan({"session": "89R", "members": [
        member("house", 6, "Daniel Alders", ["Alders"], 1, person="avery-example"),
        member("house", 131, "Alma Allen", ["Allen"], 2)], "senate_roll": None}, PREFIX, get, "2026-11-14")
    doc["members"][0]["name"] = "Daniel Alders"
    (leg / "members.yaml").write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    bill = {"session": "89R", "bill": "HB 1", "caption": "Relating to a test.", "became_law": "signed",
            "law": {**fact("L-01"), "id": "tx/statewide/legislature/89r/bills/hb-1#L-01"},
            "votes": [{"chamber": "house", "action": "passed", "date": "2025-04-16", "record": 1,
                       "tally": {"yeas": 1, "nays": 1}, "names": {"yeas": ["Alders"], "nays": ["Allen"]},
                       "every_member": False, "fact": {**fact("V-01"), "id": "tx/statewide/legislature/89r/bills/hb-1#V-01"}}]}
    (leg / "bills/hb-1.yaml").write_text(yaml.safe_dump(bill, sort_keys=False), encoding="utf-8")
    out = tmp_path / "dist"
    assert build(root, out, demo=True, today="2026-11-05") == 0
    profile = (out / "people/tx/avery-example/index.html").read_text(encoding="utf-8")
    assert '<span class="chip chip-muted">Party (Texas Legislative Reference Library): R</span>' in profile
    assert "Passed the House 1-1 (D 0-1, R 1-0)" in profile
    assert "Legislative Reference Library&#39;s member list" in profile or "Legislative Reference Library's member list" in profile
    assert "style=" not in profile.split("<main", 1)[-1]


def test_a_legislature_rerun_keeps_party_and_its_source():
    import legislature as L
    old, _ = P.plan(members_doc(), PREFIX, get, "2026-11-14")
    new = members_doc()
    L.keep_party(old, new)
    assert [m["party"] for m in new["members"]] == [m["party"] for m in old["members"]]
    assert new["party_source"] == old["party_source"] and new["notes"] == old["notes"]


def test_a_legislature_rerun_keeps_members_added_by_hand():
    import legislature as L
    old = members_doc()
    new = members_doc()
    new["members"] = [m for m in new["members"] if m["district"] != 131]
    L.keep_added(old, new)
    assert [m["district"] for m in new["members"]] == [m["district"] for m in old["members"]]
