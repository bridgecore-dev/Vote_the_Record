"""Seats and people (docs/OFFICIALS.md): the validator's links, the roster collector, and the pages."""

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "validate"))
sys.path.insert(0, str(REPO / "src" / "tools"))
sys.path.insert(0, str(REPO / "src" / "build"))
from validate import validate  # noqa: E402
import officials_from_rosters as rosters  # noqa: E402
import seed_officials  # noqa: E402
from build import build  # noqa: E402

SEAT_REL = "data/states/tx/localities/test-county/offices/county-judge.yaml"
SEAT_ID = "tx/localities/test-county/offices/county-judge"
FACT = {"id": f"{SEAT_ID}#S-01", "headline": "Who holds this office now",
        "statement": "The official list (Test County: Elected Officials) shows Pat Doe as County Judge.",
        "label": "official_record", "claim_status": "documented", "contradicted_by": [],
        "source_url": "https://example.gov/officials", "source_title": "Test County: Elected Officials",
        "source_kind": "page", "sha256": None, "archive_url": None, "event_date": "2026-11-05",
        "retrieved": "2026-11-05", "verification": "unverified", "verified_on": None, "reviewer": None}
SEAT = {"name": "County Judge", "office_type": "test-office",
        "holders": [{"person": "pat-doe", "start": None, "end": None, "basis": f"{SEAT_ID}#S-01"}],
        "sources": [FACT]}
PERSON = {"id": "pat-doe", "name": "Pat Doe", "candidacies": [], "records": [], "statements": [],
          "funding": [], "promise_tracker": []}


def repo(tmp_path, seat=None, person=None, extra=None):
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    files = {"offices/test-office/powers.yaml": {"office_type": "test-office", "powers": []},
             SEAT_REL: seat or SEAT, "data/states/tx/people/pat-doe.yaml": person or PERSON, **(extra or {})}
    for rel, doc in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return validate(tmp_path).errors


def test_a_seat_and_its_holder_pass(tmp_path):
    assert repo(tmp_path) == []


def test_a_holder_needs_a_person_file_and_a_basis_in_the_seat_sources(tmp_path):
    seat = copy.deepcopy(SEAT)
    seat["holders"] = [{"person": "nobody-here", "start": None, "end": None, "basis": f"{SEAT_ID}#S-09"}]
    errors = " ".join(repo(tmp_path, seat=seat))
    assert "no person file" in errors and "basis" in errors


def test_a_seat_office_type_must_exist(tmp_path):
    errors = " ".join(repo(tmp_path, seat={**SEAT, "office_type": "dog-catcher"}))
    assert "has no offices/<type>/powers.yaml" in errors


def test_person_facts_use_the_person_path_and_the_file_name_is_the_id(tmp_path):
    record = {**FACT, "id": "tx/people/someone-else#R-01", "record_type": "vote"}
    errors = " ".join(repo(tmp_path, person={**PERSON, "id": "pat-q-doe", "records": [record]}))
    assert "must start with 'tx/people/pat-doe#'" in errors and "id must be 'pat-doe'" in errors


def test_a_candidacy_must_be_a_candidate_file(tmp_path):
    errors = " ".join(repo(tmp_path, person={**PERSON, "candidacies": [
        "tx/localities/test-county/elections/2026-11-03/test-race/candidates/pat-doe"]}))
    assert "is not a candidate file" in errors


def test_a_vacant_seat_has_no_current_holder(tmp_path):
    errors = " ".join(repo(tmp_path, seat={**SEAT, "vacant": True}))
    assert "vacant" in errors


@pytest.fixture
def roster_repo(tmp_path, monkeypatch):
    data = tmp_path / "data" / "states"
    (tmp_path / "offices" / "county-judge").mkdir(parents=True)
    (tmp_path / "offices" / "county-judge" / "powers.yaml").write_text("office_type: county-judge\n", encoding="utf-8")
    monkeypatch.setattr(rosters, "REPO", tmp_path)
    monkeypatch.setattr(rosters, "DATA", data)
    saved = tmp_path / "rosters"
    saved.mkdir()
    (saved / "page.txt").write_text("Elected Officials\nCounty Judge   Pat  Doe\nSheriff Lee Roe\n", encoding="utf-8")
    return tmp_path, saved


def roster(saved, *seats):
    path = saved / "x.roster.json"
    path.write_text(json.dumps({
        "source_url": "https://example.gov/officials", "source_title": "Test County: Elected Officials",
        "source_kind": "page", "retrieved": "2026-11-05", "saved": "page.txt", "original": None,
        "archive_url": None, "seats": list(seats)}), encoding="utf-8")
    return path


def seat(rel, name, holders, quote):
    return {"seat": rel, "name": name, "office_type": "county-judge", "holders": holders, "quote": quote}


def test_the_roster_collector_writes_only_names_found_in_the_saved_page(roster_repo):
    root, saved = roster_repo
    path = roster(saved,
                  seat("tx/localities/test-county/offices/county-judge", "County Judge", ["Pat Doe"], "County Judge Pat Doe"),
                  seat("tx/localities/test-county/offices/county-clerk", "County Clerk", ["Made Up"], "County Clerk Made Up"))
    writes, notes = rosters.plan([path])
    written = {t.relative_to(root).as_posix() for t, _ in writes}
    assert written == {"data/states/tx/localities/test-county/offices/county-judge.yaml",
                       "data/states/tx/people/pat-doe.yaml"}
    assert any("REFUSE tx/localities/test-county/offices/county-clerk" in n for n in notes)
    doc = yaml.safe_load(next(text for t, text in writes if "offices" in t.as_posix()))
    fact = doc["sources"][0]
    assert fact["verification"] == "unverified" and fact["reviewer"] is None and "County Judge Pat Doe" in fact["statement"]


def test_the_roster_collector_never_changes_a_seat_and_keeps_two_people_apart(roster_repo):
    root, saved = roster_repo
    path = roster(saved, seat("tx/localities/test-county/offices/county-judge", "County Judge", ["Pat Doe"], "Pat Doe"))
    for target, text in rosters.plan([path])[0]:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    other = roster(saved, seat("tx/localities/test-county/offices/county-judge", "County Judge", ["Lee Roe"], "Lee Roe"),
                   seat("tx/localities/other-county/offices/county-judge", "County Judge", ["Pat Doe"], "Pat Doe"))
    writes, notes = rosters.plan([other])
    assert any(n.startswith("DIFFERS tx/localities/test-county/offices/county-judge") for n in notes)
    assert "data/states/tx/people/pat-doe-other-county.yaml" in {t.relative_to(root).as_posix() for t, _ in writes}


def test_names_from_ballots_become_readable_and_ids_fold_accents():
    assert seed_officials.proper("RAFAEL D. PEÑA III") == "Rafael D. Peña III"
    assert seed_officials.slugify("Rafael D. Peña III") == "rafael-d-pena-iii"
    assert seed_officials.proper("Mixed Case Kept") == "Mixed Case Kept"


def test_demo_build_has_officials_and_profile_pages(tmp_path):
    out = tmp_path / "dist"
    assert build(REPO / "tests/fixtures/demo", out, demo=True, today="2026-11-05") == 0
    profile = (out / "people/tx/avery-example/index.html").read_text(encoding="utf-8")
    assert "Voted for the Demo County road bond order" in profile  # the person's own record
    assert "County Commissioner, Precinct 9" in profile and "Unverified" in profile
    place = (out / "officials/tx/demo-county/index.html").read_text(encoding="utf-8")
    assert 'href="/people/tx/avery-example/"' in place
    race = (out / "races/tx/demo-county/2026-11-03/commissioner-precinct-9/index.html").read_text(encoding="utf-8")
    assert 'href="/people/tx/avery-example/"' in race


def test_records_go_to_the_seat_holder_only_when_the_source_names_them(tmp_path, monkeypatch):
    import records_from_sources as records
    data = tmp_path / "data" / "states"
    for rel, doc in {SEAT_REL.removeprefix("data/states/"): SEAT, "tx/people/pat-doe.yaml": PERSON}.items():
        (data / rel).parent.mkdir(parents=True, exist_ok=True)
        (data / rel).write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr(records, "DATA", data)
    (tmp_path / "notice.txt").write_text("FOR the proposal: Judge Pat Doe, Commissioner Lee Roe\n", encoding="utf-8")
    rec = {"seat": SEAT_ID, "record_type": "vote", "headline": "Voted for proposing a tax rate",
           "statement": "Voted FOR the proposal.", "quote": "FOR the proposal: Judge Pat Doe"}
    src = {"source_url": "https://example.gov/notice", "source_title": "Notice", "source_kind": "page",
           "retrieved": "2026-11-05", "event_date": "2025-08-26", "saved": "notice.txt", "original": None,
           "records": [dict(rec, printed_name="Judge Pat Doe"), dict(rec, printed_name="Commissioner Lee Roe")]}
    path = tmp_path / "x.records.json"
    path.write_text(json.dumps(src), encoding="utf-8")
    people, notes = records.plan([path], "2026-11-05")
    (doc,) = people.values()
    assert [r["id"] for r in doc["records"]] == ["tx/people/pat-doe#R-01"]
    assert doc["records"][0]["verification"] == "unverified"
    assert any("the seat's holder is 'Pat Doe'" in n for n in notes)
