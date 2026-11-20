"""Tests for the upkeep tools in src/tools/ (fictional data)."""

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "tools"))
sys.path.insert(0, str(REPO / "src" / "validate"))
import archive  # noqa: E402
import check_rosters  # noqa: E402
import new_election  # noqa: E402
import status  # noqa: E402
from common import write_doc  # noqa: E402
from validate import validate  # noqa: E402

E = "2027-05-01"
BALLOT = f"tx/localities/test-county/elections/{E}"
RACE = f"tx/localities/test-city/elections/{E}/council-district-3"
SB = {"issuer": "Test County Clerk", "url": "https://example.gov/sample.pdf", "title": "Sample Ballot, May 1, 2027",
      "sha256": "b" * 64, "archive_url": None, "retrieved": "2027-03-01"}
SPEC = {"ballot": BALLOT, "name": "Test County ballot", "election_date": E, "election_label": "May 1, 2027 election",
        "lookup_url": "https://example.gov/lookup", "sample_ballot": SB,
        "write_in_list": {"url": "https://example.gov/writein.pdf", "title": "Test County Clerk write-in list",
                          "sha256": "c" * 64, "archive_url": None, "retrieved": "2027-03-01"},
        "contests": [
            {"race": RACE, "name": "Test City Council, District 3", "office_type": "city-council-member", "page": 2,
             "candidates": [{"name": "JANE DOE", "party": None}, {"name": 'ROBERT "BOB" ROE', "party": None}],
             "write_ins": ["Pat Poe"]},
            {"measure": f"tx/localities/test-city/elections/{E}/proposition-a", "name": "Test City, Proposition A",
             "kind": "charter-amendment", "page": 3, "ballot_text": "Shall the charter be amended?",
             "choices": ["FOR", "AGAINST"]}]}


def scaffold(root):
    (root / "offices/city-council-member").mkdir(parents=True)
    (root / "offices/city-council-member/powers.yaml").write_text(yaml.safe_dump(
        {"office_type": "city-council-member", "level": "local", "powers": []}), encoding="utf-8")
    for path, header, doc in new_election.plan(root, SPEC):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_doc(path, header, doc)


def test_new_election_writes_a_valid_ballot_and_never_overwrites(tmp_path):
    scaffold(tmp_path)
    assert validate(tmp_path, schemas_dir=REPO / "schemas").errors == []
    ballot = yaml.safe_load((tmp_path / f"data/states/{BALLOT}/ballot.yaml").read_text(encoding="utf-8"))
    assert ballot["contests"][0] == RACE and ballot["complete"] is False
    race = yaml.safe_load((tmp_path / f"data/states/{RACE}/race.yaml").read_text(encoding="utf-8"))
    assert race["detail"] == "basic" and [s["id"][-4:] for s in race["sources"]] == ["S-01", "S-02"]
    assert "JANE DOE; ROBERT \"BOB\" ROE" in race["sources"][0]["statement"]
    bob = yaml.safe_load((tmp_path / f"data/states/{RACE}/candidates/robert-bob-roe.yaml").read_text(encoding="utf-8"))
    assert bob["incumbent"] is None and bob["name"] == 'ROBERT "BOB" ROE'
    pat = yaml.safe_load((tmp_path / f"data/states/{RACE}/candidates/pat-poe.yaml").read_text(encoding="utf-8"))
    assert pat["write_in"] is True
    assert new_election.plan(tmp_path, SPEC) == []  # a second run changes nothing


def test_archive_fills_round_trip_and_hand_formatted_files(tmp_path):
    scaffold(tmp_path)
    race_file = tmp_path / f"data/states/{RACE}/race.yaml"
    fid = f"{RACE}/race#S-01"
    archive.set_archive_url(race_file, fid, "https://web.archive.org/web/20270301000000/https://example.gov/sample.pdf")
    doc = yaml.safe_load(race_file.read_text(encoding="utf-8"))
    assert doc["sources"][0]["archive_url"].startswith("https://web.archive.org/")
    assert doc["sources"][1]["archive_url"] is None  # only the named fact
    # A hand-formatted file: only the one line changes.
    text = race_file.read_text(encoding="utf-8").replace("detail: basic", "detail:   basic")
    race_file.write_text(text, encoding="utf-8")
    wid = f"{RACE}/race#S-02"
    archive.set_archive_url(race_file, wid, "https://web.archive.org/web/20270301000001/https://example.gov/writein.pdf")
    after = race_file.read_text(encoding="utf-8")
    assert "detail:   basic" in after
    assert yaml.safe_load(after)["sources"][1]["archive_url"].endswith("writein.pdf")


def test_archive_skips_bulk_downloads_and_generated_files(tmp_path):
    scaffold(tmp_path)
    urls = archive.pending(tmp_path)
    assert "https://example.gov/sample.pdf" in urls
    zips = tmp_path / f"data/states/{BALLOT}/zips.yaml"
    zips.write_text(yaml.safe_dump({"sources": [{"id": f"{BALLOT}/zips#S-01", "source_url": "https://gis.example/x",
                                                 "archive_url": None}]}), encoding="utf-8")
    assert "https://gis.example/x" not in archive.pending(tmp_path)


def test_archive_includes_federal_roll_calls(tmp_path):
    scaffold(tmp_path)
    roll = tmp_path / "data/federal/congress/119/2/senate/roll-1.yaml"
    roll.parent.mkdir(parents=True)
    roll.write_text(yaml.safe_dump({"fact": {"id": "us/congress/119/2/senate/roll-1#R-001",
                                             "source_url": "https://www.senate.gov/x.xml", "archive_url": None}}),
                    encoding="utf-8")
    assert "https://www.senate.gov/x.xml" in archive.pending(tmp_path)


def test_check_rosters_reports_only_changes(tmp_path):
    scaffold(tmp_path)
    race_file = tmp_path / f"data/states/{RACE}/race.yaml"
    race = yaml.safe_load(race_file.read_text(encoding="utf-8"))
    race["sources"].append({**race["sources"][0], "id": f"{RACE}/race#S-HOLDER", "source_url": "https://example.gov/council"})
    race_file.write_text(yaml.safe_dump(race), encoding="utf-8")
    for slug, value in (("jane-doe", True), ("robert-bob-roe", False), ("pat-poe", False)):
        f = tmp_path / f"data/states/{RACE}/candidates/{slug}.yaml"
        f.write_text(f.read_text(encoding="utf-8").replace("incumbent: null", f"incumbent: {str(value).lower()}"),
                     encoding="utf-8")
    page = b"<ul><li data-x='Doe, Jane'>Council District 3</li><li>Robert Roe, District 5</li></ul>"
    lines = check_rosters.check(tmp_path, None, fetcher=lambda url: page, known_set=set())
    assert lines == [f"CHECK {RACE}: ROBERT \"BOB\" ROE now appears on https://example.gov/council "
                     "(a new officeholder, or holding a different seat on the same list)"]
    assert check_rosters.check(tmp_path, None, fetcher=lambda url: page, known_set={(RACE, "robert-bob-roe")}) == []


def test_status_report_counts(tmp_path):
    scaffold(tmp_path)
    text = status.report(tmp_path, "2027-03-02", 30)
    assert "- Nobody's incumbent status known: 1" in text
    assert "- No money information for any candidate: 1" in text
    assert "- No archived copy yet: " in text


def test_snapshot_hash_accepts_gzip_replay(monkeypatch):
    import gzip
    import hashlib
    body = b"<roll_call_vote>...</roll_call_vote>"
    monkeypatch.setattr(archive, "fetch", lambda url: gzip.compress(body))
    got = archive.snapshot_sha256("https://web.archive.org/web/20261120130628/https://www.senate.gov/x.xml")
    assert hashlib.sha256(body).hexdigest() in got
