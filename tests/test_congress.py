"""U.S. Congress roll calls: the collector reads the official XML as printed, the validator checks the counts, and the
/congress/ pages list every member alphabetically with the record's own party and state (CLAUDE.md rules 2, 3a, 5)."""

import csv
import io
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "collectors" / "federal"))
sys.path.insert(0, str(REPO / "src" / "collectors"))
sys.path.insert(0, str(REPO / "src" / "build"))
sys.path.insert(0, str(REPO / "src" / "validate"))
import rollcall  # noqa: E402
from build import build  # noqa: E402
from validate import validate  # noqa: E402

HOUSE_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rollcall-vote><vote-metadata>
<congress>119</congress><session>2nd</session><chamber>U.S. House of Representatives</chamber>
<rollcall-num>7</rollcall-num><legis-num>H R 12</legis-num><vote-question>On Passage</vote-question>
<vote-type>YEA-AND-NAY</vote-type><vote-result>Passed</vote-result><action-date>3-Feb-2026</action-date>
<vote-desc>A test bill</vote-desc>
<vote-totals>
<totals-by-party><party>Republican</party><yea-total>1</yea-total><nay-total>0</nay-total><present-total>0</present-total><not-voting-total>1</not-voting-total></totals-by-party>
<totals-by-party><party>Democratic</party><yea-total>1</yea-total><nay-total>1</nay-total><present-total>0</present-total><not-voting-total>0</not-voting-total></totals-by-party>
<totals-by-vote><total-stub>Totals</total-stub><yea-total>2</yea-total><nay-total>1</nay-total><present-total>0</present-total><not-voting-total>1</not-voting-total></totals-by-vote>
</vote-totals></vote-metadata>
<vote-data>
<recorded-vote><legislator name-id="Z000002" party="R" state="TX" role="legislator">Zeta</legislator><vote>Yea</vote></recorded-vote>
<recorded-vote><legislator name-id="A000001" party="D" state="NC" role="legislator">Alpha</legislator><vote>Nay</vote></recorded-vote>
<recorded-vote><legislator name-id="M000003" party="D" state="CA" role="legislator">Mid</legislator><vote>Yea</vote></recorded-vote>
<recorded-vote><legislator name-id="B000004" party="R" state="OH" role="legislator">Beta</legislator><vote>Not Voting</vote></recorded-vote>
</vote-data></rollcall-vote>"""

SENATE_XML = b"""<?xml version="1.0" encoding="UTF-8"?><roll_call_vote>
<congress>119</congress><session>1</session><vote_number>9</vote_number>
<vote_date>July 1, 2025,  11:56 AM</vote_date><vote_question_text>On Passage of the Bill H.R. 1</vote_question_text>
<vote_document_text>A test bill.</vote_document_text><vote_result_text>Bill Passed (1-1, Vice President voted Yea)</vote_result_text>
<majority_requirement>1/2</majority_requirement>
<document><document_name>H.R. 1</document_name></document>
<count><yeas>1</yeas><nays>1</nays><present/><absent/></count>
<tie_breaker><by_whom>Vice President of the United States</by_whom><tie_breaker_vote>Yea</tie_breaker_vote></tie_breaker>
<members>
<member><member_full>Ess (R-AK)</member_full><party>R</party><state>AK</state><vote_cast>Yea</vote_cast><lis_member_id>S001</lis_member_id></member>
<member><member_full>Ay (D-MD)</member_full><party>D</party><state>MD</state><vote_cast>Nay</vote_cast><lis_member_id>S002</lis_member_id></member>
</members></roll_call_vote>"""


def test_house_xml_is_read_as_printed():
    roll = rollcall.parse_house(HOUSE_XML)
    assert (roll["congress"], roll["session"], roll["chamber"], roll["roll"]) == (119, 2, "house", 7)
    assert roll["date"] == "2026-02-03" and roll["measure"] == "H R 12" and roll["result"] == "Passed"
    assert roll["totals"] == {"yea": 2, "nay": 1, "present": 0, "not_voting": 1}
    assert [p["party"] for p in roll["party_totals"]] == ["Republican", "Democratic"]
    assert {m["name"]: (m["party"], m["state"], m["vote"]) for m in roll["members"]}["Alpha"] == ("D", "NC", "Nay")


def test_senate_xml_counts_every_member_and_keeps_the_tie_breaker():
    roll = rollcall.parse_senate(SENATE_XML)
    assert (roll["chamber"], roll["roll"], roll["date"]) == ("senate", 9, "2025-07-01")
    assert roll["totals"] == {"yea": 1, "nay": 1, "present": 0, "not_voting": 0}
    assert roll["party_totals"] is None  # the Senate's record prints none
    assert roll["tie_breaker"] == {"by": "Vice President of the United States", "vote": "Yea"}


def roll_doc():
    roll = rollcall.parse_house(HOUSE_XML)
    rollcall.fill_names(roll, {"A000001": "Ann Alpha", "Z000002": "Zed Zeta", "M000003": "Mia Mid", "B000004": "Bo Beta"})
    for m in roll["members"]:
        m["portrait"] = None
    doc, rel = rollcall.build_doc(roll, "https://clerk.house.gov/evs/2026/roll007.xml", HOUSE_XML,
                                  "2026-02-04", "2026-02-04")
    return doc, rel


def make_repo(tmp_path, files):
    root = tmp_path / "repo"
    for base in ("data", "offices", "corrections"):
        (root / base).mkdir(parents=True, exist_ok=True)
    for rel, doc in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return root


def test_collector_writes_an_unverified_official_record_at_its_path():
    doc, rel = roll_doc()
    assert rel == "data/federal/congress/119/2/house/roll-7.yaml"
    fact = doc["fact"]
    assert fact["id"] == "us/congress/119/2/house/roll-7#R-001"
    assert (fact["label"], fact["source_kind"], fact["verification"], fact["verified_on"], fact["reviewer"]) == (
        "official_record", "document", "unverified", None, None)
    assert len(fact["sha256"]) == 64
    assert [m["name"] for m in doc["members"]] == ["Alpha", "Beta", "Mid", "Zeta"]


def test_validator_accepts_a_roll_call_and_checks_its_counts(tmp_path):
    doc, rel = roll_doc()
    assert validate(make_repo(tmp_path / "ok", {rel: doc}), schemas_dir=REPO / "schemas").errors == []
    bad = dict(doc, totals=dict(doc["totals"], yea=3))
    errors = validate(make_repo(tmp_path / "bad", {rel: bad}), schemas_dir=REPO / "schemas").errors
    assert any("don't add up to totals" in e for e in errors)
    moved = validate(make_repo(tmp_path / "moved", {rel.replace("roll-7", "roll-8"): doc}),
                     schemas_dir=REPO / "schemas").errors
    assert any("belongs at" in e for e in moved)


def test_validator_rejects_a_verified_roll_call_without_reviewer(tmp_path):
    doc, rel = roll_doc()
    doc["fact"]["verification"] = "verified"
    errors = validate(make_repo(tmp_path, {rel: doc}), schemas_dir=REPO / "schemas").errors
    assert errors


def test_congress_pages_list_every_member_alphabetically_with_csv(tmp_path):
    doc, rel = roll_doc()
    out = tmp_path / "dist"
    assert build(make_repo(tmp_path, {rel: doc}), out, today="2026-02-05") == 0
    index = (out / "congress" / "index.html").read_text(encoding="utf-8")
    page = (out / "congress" / "119-2" / "house" / "7" / "index.html").read_text(encoding="utf-8")
    assert "A test bill" in index and 'id="compare-data"' in index and "(202) 224-3121" in index
    yea = page.split('id="g-yea"')[1].split("</section>")[0]
    assert yea.index("Mia Mid") < yea.index("Zed Zeta")  # alphabetical within the group, never by party
    assert "R · TX" in yea and "Unverified" in page and "clerk.house.gov/evs/2026/roll007.xml" in page
    assert "Totals by party, as the House Clerk prints them" in page
    assert ' style="' not in page and "<script>" not in page
    rows = list(csv.DictReader(io.StringIO((out / "congress" / "119-2" / "house" / "7" /
                                            "roll-119-2-house-7.csv").read_text(encoding="utf-8"))))
    assert [r["name_as_printed"] for r in rows] == ["Alpha", "Beta", "Mid", "Zeta"]
    assert {r["verification"] for r in rows} == {"unverified"}
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "Latest roll call" in home and 'href="/congress/"' in home


def test_no_congress_link_without_roll_calls(tmp_path):
    out = tmp_path / "dist"
    assert build(make_repo(tmp_path, {}), out, today="2026-02-05") == 0
    assert 'href="/congress/"' not in (out / "index.html").read_text(encoding="utf-8")
    assert not (out / "congress").exists()


def test_every_page_has_the_night_toggle_script(tmp_path):
    out = tmp_path / "dist"
    assert build(make_repo(tmp_path, {}), out, today="2026-02-05") == 0
    page = (out / "about" / "index.html").read_text(encoding="utf-8")
    assert 'id="theme-toggle"' in page and "/static/js/theme.js" in page
