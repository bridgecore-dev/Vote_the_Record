"""Each test builds a tiny repo in a temp dir and checks one validator rule."""

import copy
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "validate"))
from validate import validate  # noqa: E402

RACE_DIR = "data/states/tx/localities/test-county/elections/2026-11-03/test-race"
CAND_REL = f"{RACE_DIR}/candidates/pat-doe.yaml"
PREFIX = "tx/localities/test-county/elections/2026-11-03/test-race/candidates/pat-doe"

FACT = {
    "id": f"{PREFIX}#F-001",
    "record_type": "vote",
    "same_event": "tx/localities/test-county/actions/2025-01-01#item-1",
    "statement": "Voted yes on Item 1.",
    "label": "official_record",
    "claim_status": "documented",
    "source_url": "https://example.gov/minutes.pdf",
    "source_title": "Minutes",
    "source_kind": "document",
    "sha256": None,
    "archive_url": None,
    "event_date": "2025-01-01",
    "retrieved": None,
    "verification": "unverified",
    "verified_on": None,
    "reviewer": None,
}

VERIFIED = {
    "sha256": "a" * 64,
    "archive_url": "https://web.archive.org/web/2025/https://example.gov/minutes.pdf",
    "retrieved": "2025-02-01",
    "verification": "verified",
    "verified_on": "2025-02-02",
    "reviewer": "owner",
}

SURVEY = {
    "office_type": "test-office",
    "tags": ["use-reserves", "cut-spending"],
    "scenarios": [{
        "id": "S-01",
        "powers": ["P-BUDGET"],
        "scenario": "Revenue falls short mid-year.",
        "options": [
            {"id": "A", "text": "Cut spending.", "tags": ["cut-spending"]},
            {"id": "B", "text": "Use reserves.", "tags": ["use-reserves"]},
        ],
    }],
}


def candidate(**overrides):
    doc = {
        "id": "pat-doe", "name": "Pat Doe", "incumbent": True, "ballot_party": None,
        "records": [copy.deepcopy(FACT)], "funding": [], "endorsements": [],
        "running_on": {"policy_proposals": [], "attack_messaging": [], "contested_claims": []},
        "mappings": [], "promise_tracker": [],
    }
    doc.update(overrides)
    return doc


def build(tmp_path, cand=None, survey=None):
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    files = {
        "offices/test-office/powers.yaml": {"office_type": "test-office", "powers": [
            {"id": "P-BUDGET", "description": "Adopts the budget.", "source_url": "https://example.gov/law"}]},
        "offices/test-office/survey.yaml": survey or SURVEY,
        f"{RACE_DIR}/race.yaml": {"name": "Test Race", "office_type": "test-office", "election_date": "2026-11-03"},
        CAND_REL: cand or candidate(),
    }
    for rel, doc in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return validate(tmp_path).errors


def with_fact(**changes):
    fact = {**FACT, **changes}
    return candidate(records=[fact])


def test_valid_unverified_passes(tmp_path):
    assert build(tmp_path) == []


def test_valid_verified_passes(tmp_path):
    assert build(tmp_path, with_fact(**VERIFIED)) == []


def test_verified_without_archive_url_fails(tmp_path):
    errors = build(tmp_path, with_fact(**{**VERIFIED, "archive_url": None}))
    assert any("archive_url" in e for e in errors)


def test_verified_on_before_retrieved_fails(tmp_path):
    errors = build(tmp_path, with_fact(**{**VERIFIED, "verified_on": "2025-01-15"}))
    assert any("earlier than retrieved" in e for e in errors)


def test_verified_document_without_sha256_fails(tmp_path):
    errors = build(tmp_path, with_fact(**{**VERIFIED, "sha256": None}))
    assert any("sha256" in e for e in errors)


def test_page_source_with_sha256_fails(tmp_path):
    errors = build(tmp_path, with_fact(source_kind="page", sha256="b" * 64))
    assert any("sha256" in e for e in errors)


def test_verified_page_without_sha256_passes(tmp_path):
    assert build(tmp_path, with_fact(**{**VERIFIED, "source_kind": "page", "sha256": None})) == []


def test_unverified_with_reviewer_fails(tmp_path):
    errors = build(tmp_path, with_fact(reviewer="someone"))
    assert any("reviewer" in e for e in errors)


def test_missing_source_url_fails(tmp_path):
    fact = {k: v for k, v in FACT.items() if k != "source_url"}
    errors = build(tmp_path, candidate(records=[fact]))
    assert any("source_url" in e for e in errors)


def test_id_not_matching_path_fails(tmp_path):
    errors = build(tmp_path, with_fact(id="tx/elsewhere/pat-doe#F-001"))
    assert any("path-based" in e for e in errors)


def test_contradicted_requires_evidence(tmp_path):
    errors = build(tmp_path, with_fact(claim_status="contradicted"))
    assert any("contradicted_by" in e for e in errors)


def test_contradicted_by_must_exist(tmp_path):
    errors = build(tmp_path, with_fact(claim_status="contradicted", contradicted_by=[f"{PREFIX}#F-999"]))
    assert any("does not exist" in e for e in errors)


def mapping(**changes):
    return {"record": FACT["id"], "question": "test-office/S-01", "options": ["B"],
            "rationale": "Voted to draw on reserves.", "verification": "unverified",
            "verified_on": None, "reviewer": None, **changes}


def test_mapping_with_multiple_options_passes(tmp_path):
    assert build(tmp_path, candidate(mappings=[mapping(options=["A", "B"])])) == []


def test_mapping_unknown_option_fails(tmp_path):
    errors = build(tmp_path, candidate(mappings=[mapping(options=["Z"])]))
    assert any("option 'Z'" in e for e in errors)


def test_mapping_without_rationale_fails(tmp_path):
    errors = build(tmp_path, candidate(mappings=[mapping(rationale="")]))
    assert any("rationale" in e or "short" in e for e in errors)


def test_promise_kept_requires_evidence(tmp_path):
    errors = build(tmp_path, candidate(promise_tracker=[{"promise": FACT["id"], "status": "kept", "evidence": []}]))
    assert any("promise_tracker" in e for e in errors)


def test_party_key_outside_ballot_party_fails(tmp_path):
    cand = candidate()
    cand["records"][0]["party"] = "X"
    errors = build(tmp_path, cand)
    assert any("ballot_party" in e for e in errors)


def test_party_in_scenario_text_fails(tmp_path):
    survey = copy.deepcopy(SURVEY)
    survey["scenarios"][0]["scenario"] = "Republicans propose cuts."
    errors = build(tmp_path, survey=survey)
    assert any("mentions a party" in e for e in errors)


def test_scenario_power_must_exist(tmp_path):
    survey = copy.deepcopy(SURVEY)
    survey["scenarios"][0]["powers"] = ["P-NOPE"]
    errors = build(tmp_path, survey=survey)
    assert any("P-NOPE" in e for e in errors)


def test_option_tag_must_be_listed(tmp_path):
    survey = copy.deepcopy(SURVEY)
    survey["scenarios"][0]["options"][0]["tags"] = ["unlisted"]
    errors = build(tmp_path, survey=survey)
    assert any("unlisted" in e for e in errors)


def test_candidate_summary_facts_are_validated(tmp_path):
    good = {**FACT, "id": f"{PREFIX}#B-001"}
    good.pop("record_type")
    assert build(tmp_path, candidate(summary=[good])) == []


def test_candidate_summary_fact_without_source_fails(tmp_path):
    bad = {k: v for k, v in FACT.items() if k not in ("record_type", "source_url")}
    bad["id"] = f"{PREFIX}#B-001"
    errors = build(tmp_path, candidate(summary=[bad]))
    assert any("source_url" in e for e in errors)


def write(tmp_path, rel, doc):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")


def test_why_it_matters_requires_a_source(tmp_path):
    build(tmp_path)
    write(tmp_path, f"{RACE_DIR}/race.yaml", {
        "name": "Test Race", "office_type": "test-office", "election_date": "2026-11-03",
        "why_it_matters": [{"text": "It sets the budget."}]})
    errors = validate(tmp_path).errors
    assert any("source_url" in e for e in errors)


def test_why_it_matters_allowed_on_office_locality_and_race(tmp_path):
    reason = [{"text": "It sets the budget.", "source_url": "https://example.gov/law"}]
    build(tmp_path)
    write(tmp_path, "offices/test-office/powers.yaml", {"office_type": "test-office", "why_it_matters": reason,
          "powers": [{"id": "P-BUDGET", "description": "Adopts the budget.", "source_url": "https://example.gov/law"}]})
    write(tmp_path, "data/states/tx/localities/test-county/locality.yaml", {
        "locality": "test-county", "type": "county", "state": "TX", "sources": [], "why_it_matters": reason})
    write(tmp_path, f"{RACE_DIR}/race.yaml", {"name": "Test Race", "office_type": "test-office",
          "election_date": "2026-11-03", "why_it_matters": reason})
    assert validate(tmp_path).errors == []


def test_voter_essentials_need_path_based_ids(tmp_path):
    build(tmp_path)
    write(tmp_path, "data/states/tx/voter-essentials/2026-11-03.yaml", {
        "state": "TX", "election_date": "2026-11-03", "items": [{
            "id": "tx/elsewhere#reg", "name": "Register", "date": "2026-10-05",
            "source_url": "https://example.gov", "source_title": "Dates", "source_kind": "page",
            "sha256": None, "archive_url": None, "retrieved": "2026-09-28",
            "verification": "unverified", "verified_on": None, "reviewer": None}]})
    errors = validate(tmp_path).errors
    assert any("path-based" in e for e in errors)


def test_scenario_may_cite_a_state_override_power(tmp_path):
    # data/ is read before offices/, so the office's own powers.yaml must not erase override powers.
    survey = copy.deepcopy(SURVEY)
    survey["scenarios"][0]["powers"] = ["P-STATE-ONLY"]
    build(tmp_path, survey=survey)
    write(tmp_path, "data/states/tx/office-overrides/test-office.yaml", {
        "office_type": "test-office", "state": "TX", "powers": [{
            "id": "P-STATE-ONLY", "description": "A Texas-only power.", "statute": "Test Code § 1",
            "source_url": "https://example.gov/law"}]})
    assert validate(tmp_path).errors == []


def test_organization_statement_allowed_on_endorsements_not_records(tmp_path):
    endorsement = {**FACT, "id": f"{PREFIX}#E-001", "label": "organization_statement"}
    endorsement.pop("record_type")
    assert build(tmp_path / "ok", candidate(endorsements=[endorsement])) == []
    errors = build(tmp_path / "bad", with_fact(label="organization_statement"))
    assert any("label" in e or "organization_statement" in e for e in errors)


def test_plain_summary_must_cite_existing_powers(tmp_path):
    build(tmp_path)
    write(tmp_path, "offices/test-office/powers.yaml", {"office_type": "test-office",
          "powers": [{"id": "P-BUDGET", "description": "Adopts the budget.", "source_url": "https://example.gov/law"}],
          "plain_summary": {"intro": {"text": "Runs the budget.", "powers": ["P-BUDGET"]},
                            "points": [{"text": "Something else.", "powers": ["P-MISSING"]}]}})
    errors = validate(tmp_path).errors
    assert any("P-MISSING" in e for e in errors)
    assert not any("P-BUDGET" in e for e in errors)


def test_candidate_photo_needs_file_and_rights(tmp_path):
    photo = {"file": "pat-doe.jpg", "source_url": "https://example.org/photo", "license": "CC BY 4.0",
             "credit": "Example Photographer", "retrieved": "2026-09-30"}
    errors = build(tmp_path / "a", candidate(photo=photo))
    assert any("not found" in e for e in errors)
    (tmp_path / "b" / CAND_REL).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "b" / CAND_REL).parent.joinpath("pat-doe.jpg").write_bytes(b"\xff\xd8\xff")
    assert build(tmp_path / "b", candidate(photo=photo)) == []
    no_license = {k: v for k, v in photo.items() if k != "license"}
    assert any("license" in e for e in build(tmp_path / "c", candidate(photo=no_license)))


def test_promise_tracker_is_for_incumbents_only(tmp_path):
    entry = {"promise": FACT["id"], "status": "pending", "evidence": []}
    errors = build(tmp_path, candidate(incumbent=False, promise_tracker=[entry]))
    assert any("only incumbents" in e for e in errors)


def test_did_the_opposite_needs_evidence(tmp_path):
    entry = {"promise": FACT["id"], "status": "opposite", "evidence": []}
    errors = build(tmp_path, candidate(incumbent=True, promise_tracker=[entry]))
    assert any("promise_tracker" in e for e in errors)


# --- Ballots and ZIP codes -------------------------------------------------

BALLOT_DIR = "data/states/tx/localities/test-county/elections/2026-11-03"
SRC = {
    "statement": "Sample ballot.", "label": "official_record", "claim_status": "documented",
    "contradicted_by": [], "source_url": "https://example.gov/ballot.pdf", "source_title": "Sample ballot",
    "source_kind": "document", "sha256": "0" * 64, "archive_url": None, "event_date": "2026-11-03",
    "retrieved": "2026-09-01", "verification": "unverified", "verified_on": None, "reviewer": None,
}


def ballot_repo(tmp_path, contests, zips=None, level="county", extra=None):
    shutil.copytree(REPO / "schemas", tmp_path / "schemas")
    files = {
        "offices/test-office/powers.yaml": {"office_type": "test-office", "powers": [], **({"level": level} if level else {})},
        "data/states/tx/districts/us-house-9/elections/2026-11-03/us-rep/race.yaml":
            {"name": "U.S. Representative, District 9", "office_type": "test-office", "election_date": "2026-11-03"},
        f"{BALLOT_DIR}/jp-1/race.yaml": {"name": "JP 1", "office_type": "test-office",
                                          "election_date": "2026-11-03", "area": "test-county/jp-1"},
        f"{BALLOT_DIR}/ballot.yaml": {"name": "Test County ballot", "election_date": "2026-11-03",
                                      "sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/ballot#S-01")],
                                      "contests": contests},
    }
    if zips is not None:
        files[f"{BALLOT_DIR}/zips.yaml"] = {"sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/zips#S-01")],
                                            "zips": zips}
    files.update(extra or {})
    for rel, doc in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    for base in ("data", "offices", "corrections"):
        (tmp_path / base).mkdir(exist_ok=True)
    return validate(tmp_path).errors


US9 = "tx/districts/us-house-9/elections/2026-11-03/us-rep"
JP1 = "tx/localities/test-county/elections/2026-11-03/jp-1"


def test_valid_ballot_with_district_race_and_split_zip(tmp_path):
    assert ballot_repo(tmp_path, [US9, JP1], {"77001": [["us-house-9", "test-county/jp-1"]]}) == []


def test_ballot_contest_must_exist(tmp_path):
    errors = ballot_repo(tmp_path, [US9, "tx/statewide/elections/2026-11-03/governor"], {"77001": ["us-house-9", "test-county/jp-1"]})
    assert any("has no race.yaml" in e for e in errors)


def test_ballot_contests_need_a_level(tmp_path):
    errors = ballot_repo(tmp_path, [US9], level=None)
    assert any("no level" in e for e in errors)


def test_zip_areas_must_belong_to_ballot_races(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9", "test-county/jp-1", "us-house-99"]})
    assert any("'us-house-99' is not the area of any contest" in e for e in errors)


def test_implied_areas_are_left_out_and_every_area_is_reachable(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["tx", "us-house-9"]})
    assert any("'tx' is implied" in e for e in errors)
    assert any("area 'test-county/jp-1' is on the ballot but no ZIP code reaches it" in e for e in errors)


def test_zip_lists_an_area_once(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9", ["us-house-9", "test-county/jp-1"]]})
    assert any("listed more than once" in e for e in errors)


def test_zips_need_a_ballot(tmp_path):
    errors = ballot_repo(tmp_path, [US9], extra={
        "data/states/tx/localities/other-county/elections/2026-11-03/zips.yaml":
            {"sources": [dict(SRC, id="tx/localities/other-county/elections/2026-11-03/zips#S-01")], "zips": {}}})
    assert any("needs a ballot.yaml" in e for e in errors)


def test_ballot_source_ids_are_path_based(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9", "test-county/jp-1"]}, extra={
        f"{BALLOT_DIR}/ballot.yaml": {"name": "B", "election_date": "2026-11-03",
                                      "sources": [dict(SRC, id="tx/elsewhere#S-01")], "contests": [US9, JP1]}})
    assert any("must start with" in e for e in errors)


def test_precincts_reach_every_area_and_everywhere_is_checked(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], extra={
        f"{BALLOT_DIR}/precincts.yaml": {
            "sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/precincts#S-01")],
            "everywhere": ["tx", "us-house-9"],
            "precincts": {"12": ["us-house-9"], "13": []}}})
    assert any("everywhere: 'tx' is already implied" in e for e in errors)
    assert any("12: 'us-house-9' is implied for every precinct" in e for e in errors)
    assert any("area 'test-county/jp-1' is on the ballot but no precinct reaches it" in e for e in errors)


def test_precinct_numbers_are_digits_without_leading_zeros(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], extra={
        f"{BALLOT_DIR}/precincts.yaml": {
            "sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/precincts#S-01")],
            "precincts": {"0012": ["us-house-9", "test-county/jp-1"]}}})
    assert any("precincts" in e and "0012" in e for e in errors)



def test_precinct_shapes_must_match_precincts_and_cite_their_source(tmp_path):
    import json as _json
    pct = {"sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/precincts#S-01")],
           "precincts": {"12": ["us-house-9", "test-county/jp-1"], "13": ["us-house-9"]}}
    errors = ballot_repo(tmp_path, [US9, JP1], extra={f"{BALLOT_DIR}/precincts.yaml": pct})
    assert errors == []
    shapes = tmp_path / BALLOT_DIR / "precinct-shapes.json"
    square = [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]
    shapes.write_text(_json.dumps({"source": "tx/elsewhere#S-01", "precincts": {"12": square, "99": square}}))
    from validate import validate as _validate
    errors = _validate(tmp_path).errors
    assert any("source must be the ID" in e for e in errors)
    assert any("precinct 13 has no outline" in e for e in errors)
    assert any("precinct 99 has an outline but is not in precincts.yaml" in e for e in errors)



MEASURE_SRC = dict(SRC, id="tx/localities/test-county/elections/2026-11-03/prop-a/measure#S-01")
MEASURE = {"name": "Test County, Proposition A", "election_date": "2026-11-03", "ballot_text": "Shall the county...",
           "choices": ["FOR", "AGAINST"], "sources": [MEASURE_SRC]}
PROP = "tx/localities/test-county/elections/2026-11-03/prop-a"


def test_ballot_can_list_a_measure(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1, PROP], {"77001": ["us-house-9", "test-county/jp-1"]},
                         extra={f"{BALLOT_DIR}/prop-a/measure.yaml": MEASURE})
    assert errors == []


def test_contest_folder_cannot_hold_both_a_race_and_a_measure(tmp_path):
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9", "test-county/jp-1"]},
                         extra={f"{BALLOT_DIR}/jp-1/measure.yaml": dict(MEASURE, sources=[
                             dict(SRC, id="tx/localities/test-county/elections/2026-11-03/jp-1/measure#S-01")])})
    assert any("either race.yaml or measure.yaml" in e for e in errors)


def test_measure_needs_choices_and_exact_wording(tmp_path):
    bad = {k: v for k, v in MEASURE.items() if k != "ballot_text"}
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9", "test-county/jp-1"]},
                         extra={f"{BALLOT_DIR}/prop-a/measure.yaml": dict(bad, choices=["FOR"])})
    assert any("ballot_text" in e for e in errors)
    assert any("choices" in e for e in errors)



def test_unmapped_areas_skip_reachability_but_must_be_real_and_unused(tmp_path):
    ballot = {"name": "Test County ballot", "election_date": "2026-11-03",
              "sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/ballot#S-01")],
              "contests": [US9, JP1], "unmapped": ["test-county/jp-1"]}
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9"]}, extra={f"{BALLOT_DIR}/ballot.yaml": ballot})
    assert errors == []  # jp-1 is unmapped, so no ZIP needs to reach it


def test_unmapped_area_must_be_on_the_ballot_and_not_in_zips(tmp_path):
    ballot = {"name": "Test County ballot", "election_date": "2026-11-03",
              "sources": [dict(SRC, id="tx/localities/test-county/elections/2026-11-03/ballot#S-01")],
              "contests": [US9, JP1], "unmapped": ["test-county/jp-1", "nowhere"]}
    errors = ballot_repo(tmp_path, [US9, JP1], {"77001": ["us-house-9", "test-county/jp-1"]},
                         extra={f"{BALLOT_DIR}/ballot.yaml": ballot})
    assert any("unmapped: area 'nowhere'" in e for e in errors)
    assert any("'test-county/jp-1' is listed as unmapped" in e for e in errors)


def test_judicial_office_may_not_have_quiz_scenarios(tmp_path):
    assert build(tmp_path) == []
    powers = tmp_path / "offices/test-office/powers.yaml"
    doc = yaml.safe_load(powers.read_text(encoding="utf-8"))
    doc["judicial"] = True
    powers.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    errors = validate(tmp_path).errors
    assert any("judicial offices have no quiz" in e for e in errors)


def test_power_topics_must_come_from_the_topic_list(tmp_path):
    assert build(tmp_path) == []
    powers = tmp_path / "offices/test-office/powers.yaml"
    doc = yaml.safe_load(powers.read_text(encoding="utf-8"))
    doc["powers"][0]["topics"] = ["taxes-budget"]
    powers.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert validate(tmp_path).errors == []
    doc["powers"][0]["topics"] = ["hot-button-issue"]
    powers.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert validate(tmp_path).errors != []


# ---- Election results (result.yaml beside a race) ----
RESULT_REL = f"{RACE_DIR}/result.yaml"
RESULT_PREFIX = "tx/localities/test-county/elections/2026-11-03/test-race/result"


def result(**overrides):
    doc = {
        "election_date": "2026-11-03", "status": "unofficial", "as_of": "2026-11-04", "covers": "whole",
        "counts": {"pat-doe": 100},
        "sources": [{**FACT, "id": f"{RESULT_PREFIX}#S-01", "record_type": None, "same_event": None}],
    }
    for key in ("record_type", "same_event"):
        doc["sources"][0].pop(key)
    doc.update(overrides)
    return doc


def build_with_result(tmp_path, doc):
    build(tmp_path)
    path = tmp_path / RESULT_REL
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return validate(tmp_path).errors


def test_valid_result_passes(tmp_path):
    assert build_with_result(tmp_path, result()) == []


def test_official_result_with_outcome_passes(tmp_path):
    doc = result(status="official", outcome={"result": "elected", "candidates": ["pat-doe"]})
    assert build_with_result(tmp_path, doc) == []


def test_outcome_on_unofficial_result_fails(tmp_path):
    errors = build_with_result(tmp_path, result(outcome={"result": "elected", "candidates": ["pat-doe"]}))
    assert any("official" in e for e in errors)


def test_result_counting_an_unknown_candidate_fails(tmp_path):
    errors = build_with_result(tmp_path, result(counts={"pat-doe": 1, "someone-else": 2}))
    assert any("'someone-else' is not a candidate" in e for e in errors)


def test_result_missing_a_candidate_fails(tmp_path):
    errors = build_with_result(tmp_path, result(counts={}))
    assert errors


def test_partial_result_needs_a_note(tmp_path):
    errors = build_with_result(tmp_path, result(covers="partial"))
    assert any("covers_note" in e for e in errors)


def test_measure_outcome_on_a_race_fails(tmp_path):
    errors = build_with_result(tmp_path, result(status="official", outcome={"result": "passed"}))
    assert any("elected or runoff" in e for e in errors)


def test_result_id_must_match_its_path(tmp_path):
    doc = result()
    doc["sources"][0]["id"] = "tx/elsewhere/result#S-01"
    errors = build_with_result(tmp_path, doc)
    assert any("path-based" in e for e in errors)
