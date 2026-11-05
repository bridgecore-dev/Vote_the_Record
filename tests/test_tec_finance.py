"""Tests for the Texas Ethics Commission campaign finance collector (fictional data)."""

import csv
import io
import sys
import zipfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "collectors"))
sys.path.insert(0, str(REPO / "src" / "collectors" / "states" / "tx"))
import tec_finance as tf  # noqa: E402
from yamlio import to_yaml  # noqa: E402

E = "2026-11-03"
HOUSE = f"tx/districts/state-house-5/elections/{E}/state-representative"
COURT = f"tx/localities/test-county/elections/{E}/district-court-7"
BALLOT = f"data/states/tx/localities/test-county/elections/{E}/ballot.yaml"
FILER_COLS = ["filerIdent", "filerTypeCd", "filerName", "filerNameLast", "filerNameFirst", "filerNameShort",
              "ctaSeekOfficeCd", "filerHoldOfficeCd", "ctaSeekOfficeDistrict", "filerHoldOfficeDistrict",
              "contestSeekOfficeCd", "contestSeekOfficeDistrict"]
COVER_COLS = ["filerIdent", "filerTypeCd", "reportInfoIdent", "formTypeCd", "reportTypeCd1", "periodStartDt",
              "periodEndDt", "filedDt", "receivedDt", "totalContribAmount", "totalExpendAmount",
              "contribsMaintainedAmount", "loanBalanceAmount"]


def filer(fid, last, first, short="", code="STATEREP", district="5", ftype="COH"):
    return {"filerIdent": fid, "filerTypeCd": ftype, "filerName": f"{last}, {first}", "filerNameLast": last,
            "filerNameFirst": first, "filerNameShort": short, "ctaSeekOfficeCd": code, "filerHoldOfficeCd": "",
            "ctaSeekOfficeDistrict": district, "filerHoldOfficeDistrict": "", "contestSeekOfficeCd": "",
            "contestSeekOfficeDistrict": ""}


def cover(fid, rid, end, filed, contrib="100.00", ftype="COH"):
    return {"filerIdent": fid, "filerTypeCd": ftype, "reportInfoIdent": rid, "formTypeCd": ftype,
            "reportTypeCd1": "SEMIJUL", "periodStartDt": "20260101", "periodEndDt": end, "filedDt": filed,
            "receivedDt": filed, "totalContribAmount": contrib, "totalExpendAmount": "50.00",
            "contribsMaintainedAmount": "75.00", "loanBalanceAmount": ""}


def make_zip(path, filers, covers):
    with zipfile.ZipFile(path, "w") as z:
        for name, cols, rows in (("filers.csv", FILER_COLS, filers), ("cover.csv", COVER_COLS, covers)):
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
            z.writestr(name, buf.getvalue())


def cand_doc(slug, name, funding=None):
    return {"id": slug, "name": name, "incumbent": None, "ballot_party": None, "records": [],
            "funding": funding or [], "endorsements": [],
            "running_on": {"policy_proposals": [], "attack_messaging": [], "contested_claims": []},
            "mappings": [], "promise_tracker": []}


def make_repo(root):
    files = {
        "site/site.yaml": {"campaign_finance": {"tx": {"state_office_types": ["district-judge"]}}},
        "offices/state-representative/powers.yaml": {"office_type": "state-representative", "level": "state", "powers": []},
        "offices/district-judge/powers.yaml": {"office_type": "district-judge", "level": "county", "powers": []},
        BALLOT: {"name": "Test ballot", "election_date": E, "contests": [HOUSE, COURT]},
        f"data/states/{HOUSE}/race.yaml": {"name": "State Rep 5", "office_type": "state-representative",
                                           "election_date": E, "detail": "basic", "sources": []},
        f"data/states/{COURT}/race.yaml": {"name": "7th District Court", "office_type": "district-judge",
                                           "election_date": E, "detail": "basic", "sources": []},
    }
    cands = {f"data/states/{HOUSE}/candidates/jane-doe.yaml": cand_doc("jane-doe", "JANE DOE"),
             f"data/states/{HOUSE}/candidates/bob-smith.yaml": cand_doc("bob-smith", "BOB SMITH"),
             f"data/states/{HOUSE}/candidates/al-new.yaml": cand_doc("al-new", "AL NEWCOMER"),
             f"data/states/{COURT}/candidates/okey-judge.yaml": cand_doc("okey-judge", 'OKEY "OK" JUDGE')}
    for rel, doc in {**files, **cands}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(to_yaml(doc), encoding="utf-8")


FILERS = [filer("1", "Doe", "Jane"),
          filer("2", "Smith", "Robert", short="Bob"),
          filer("3", "Smith", "Bob", district="6", code="STATESEN"),        # a different Bob Smith, other office
          filer("4", "ERROR", "IN"),                                         # placeholder row TEC sometimes has
          {**filer("4", "Judge", "Iheanyi", short="Okey", code="JUDGEDIST", district="7", ftype="JCOH")}]
COVERS = [cover("1", "100", "20260630", "20260715"),
          cover("1", "101", "20261231", "20260716"),                         # future period: ignored
          cover("1", "102", "20260630", "20260801", contrib="120.00"),       # later correction: wins
          cover("2", "200", "20260630", "20260715"),
          cover("3", "300", "20260630", "20260715"),
          cover("4", "400", "20260630", "20260715", ftype="JCOH")]


PDF_TOTALS = "%PDF-1.4 fake report: 100.00 120.00 50.00 75.00 999.00"


def fake_get(url, body=PDF_TOTALS):
    return body.encode() if "/2026/pdfs/" in url else None  # only the filing year's folder exists


def run(root, zip_path, today="2026-09-30", getter=fake_get, relink=False):
    config = yaml.safe_load((root / "site/site.yaml").read_text(encoding="utf-8"))
    tec = tf.TecData(zip_path, "2025-01-01", today)
    return tf.plan(root, root / BALLOT, tec, "a" * 64, today, "2025-01-01", config, getter=getter, relink=relink)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def plain_text_pdfs(monkeypatch):
    monkeypatch.setattr(tf, "pdf_text", lambda data, max_pages=None: data.decode())


def apply(changes):
    for path, (header, doc) in changes:
        tf.write_doc(path, header, doc)


def load(root, rel):
    return yaml.safe_load((root / rel).read_text(encoding="utf-8"))


def test_matches_adds_facts_and_flags_missing(tmp_path):
    make_repo(tmp_path)
    make_zip(tmp_path / "tec.zip", FILERS, COVERS)
    changes, log = run(tmp_path, tmp_path / "tec.zip")
    apply(changes)
    jane = load(tmp_path, f"data/states/{HOUSE}/candidates/jane-doe.yaml")["funding"]
    assert [f["id"] for f in jane] == [f"{HOUSE}/candidates/jane-doe#M-01"]
    assert "report 102" in jane[0]["source_title"] and "$120.00" in jane[0]["statement"]
    assert jane[0]["source_url"] == "https://prd.tecprd.ethicsefile.com/public/cf/2026/pdfs/ScrubbedReport_102.PDF"
    assert jane[0]["sha256"] == __import__("hashlib").sha256(PDF_TOTALS.encode()).hexdigest()
    assert jane[0]["verification"] == "unverified" and jane[0]["reviewer"] is None
    assert "The report data lists no amount for outstanding loans." in jane[0]["statement"]
    bob = load(tmp_path, f"data/states/{HOUSE}/candidates/bob-smith.yaml")["funding"]
    assert "report 200" in bob[0]["source_title"]  # Robert "Bob", office and district both match
    okey = load(tmp_path, f"data/states/{COURT}/candidates/okey-judge.yaml")["funding"]
    assert "report 400" in okey[0]["source_title"]  # found despite the placeholder first row
    race = load(tmp_path, f"data/states/{HOUSE}/race.yaml")["sources"]
    assert race[-1]["id"].endswith("#S-FUND") and "AL NEWCOMER" in race[-1]["statement"]


def test_rerun_is_a_no_op_and_a_new_report_adds_m02_without_touching_m01(tmp_path):
    make_repo(tmp_path)
    make_zip(tmp_path / "tec.zip", FILERS, COVERS)
    apply(run(tmp_path, tmp_path / "tec.zip")[0])
    assert run(tmp_path, tmp_path / "tec.zip")[0] == []

    rel = f"data/states/{HOUSE}/candidates/jane-doe.yaml"
    doc = load(tmp_path, rel)
    doc["funding"][0].update(verification="verified", verified_on="2026-10-01", reviewer="owner",
                             archive_url="https://web.archive.org/x")
    (tmp_path / rel).write_text(to_yaml(doc), encoding="utf-8")
    before = doc["funding"][0]

    make_zip(tmp_path / "tec2.zip", FILERS, COVERS + [cover("1", "103", "20260924", "20261005", contrib="999.00")])
    changes, log = run(tmp_path, tmp_path / "tec2.zip", today="2026-10-06")
    apply(changes)
    funding = load(tmp_path, rel)["funding"]
    assert funding[0] == before  # the verified fact is untouched
    assert funding[1]["id"].endswith("#M-02") and "report 103" in funding[1]["source_title"]


def test_nickname_without_matching_district_is_not_accepted(tmp_path):
    tec_zip = tmp_path / "tec.zip"
    make_zip(tec_zip, [filer("2", "Smith", "Robert", short="Bob", district="9")], [cover("2", "200", "20260630", "20260715")])
    tec = tf.TecData(tec_zip, "2025-01-01", "2026-09-30")
    assert tec.match("BOB SMITH", "state-representative", "5")["status"] == "not-found"
    assert tec.match("BOB SMITH", "state-representative", "9")["status"] == "report"


def test_hand_formatted_files_are_skipped_not_rewritten(tmp_path):
    make_repo(tmp_path)
    rel = tmp_path / f"data/states/{HOUSE}/candidates/jane-doe.yaml"
    rel.write_text(rel.read_text(encoding="utf-8").replace("funding: []", "funding:   []"), encoding="utf-8")
    make_zip(tmp_path / "tec.zip", FILERS, COVERS)
    changes, log = run(tmp_path, tmp_path / "tec.zip")
    assert rel not in [p for p, _ in changes]
    assert any(line.startswith("SKIP") and "jane-doe.yaml" in line for line in log)


def test_no_fact_without_a_matching_pdf(tmp_path):
    make_repo(tmp_path)
    make_zip(tmp_path / "tec.zip", FILERS, COVERS)
    changes, log = run(tmp_path, tmp_path / "tec.zip", getter=lambda url: None)
    assert not any("jane-doe" in str(p) for p, _ in changes)
    assert any("no PDF found" in line for line in log)
    changes, log = run(tmp_path, tmp_path / "tec.zip", getter=lambda url: fake_get(url, "%PDF other numbers 1.00"))
    assert not any("jane-doe" in str(p) for p, _ in changes)
    assert any("totals not found in the PDF: 120.00" in line for line in log)


def test_relink_repoints_unverified_bulk_facts_only(tmp_path):
    make_repo(tmp_path)
    make_zip(tmp_path / "tec.zip", FILERS, COVERS)
    bulk = {"id": f"{HOUSE}/candidates/jane-doe#M-01", "headline": "h", "statement": "s", "label": "official_record",
            "claim_status": "documented", "contradicted_by": [], "source_url": tf.TEC_URL,
            "source_title": f"{tf.TEC_TITLE} (report 102)", "source_kind": "document", "sha256": "a" * 64,
            "archive_url": None, "event_date": "2026-06-30", "retrieved": "2026-09-30", "verification": "unverified",
            "verified_on": None, "reviewer": None, "notes": "old"}
    rel = tmp_path / f"data/states/{HOUSE}/candidates/jane-doe.yaml"
    rel.write_text(to_yaml(cand_doc("jane-doe", "JANE DOE", [bulk])), encoding="utf-8")
    bob = tmp_path / f"data/states/{HOUSE}/candidates/bob-smith.yaml"
    verified = dict(bulk, id=f"{HOUSE}/candidates/bob-smith#M-01", source_title=f"{tf.TEC_TITLE} (report 200)",
                    verification="verified", verified_on="2026-10-01", reviewer="owner", archive_url="https://web.archive.org/x")
    bob.write_text(to_yaml(cand_doc("bob-smith", "BOB SMITH", [verified])), encoding="utf-8")
    assert not any(line.startswith("RELINK") for line in run(tmp_path, tmp_path / "tec.zip")[1])  # only with relink
    changes, log = run(tmp_path, tmp_path / "tec.zip", relink=True)
    apply(changes)
    fact = load(tmp_path, rel.relative_to(tmp_path).as_posix())["funding"][0]
    assert fact["id"] == bulk["id"] and fact["statement"] == "s" and fact["verification"] == "unverified"
    assert fact["source_url"].endswith("ScrubbedReport_102.PDF") and "report 102" in fact["source_title"]
    assert load(tmp_path, bob.relative_to(tmp_path).as_posix())["funding"] == [verified]  # verified: untouched


def test_officials_without_a_2026_candidacy_get_their_latest_report(tmp_path):
    make_repo(tmp_path)
    make_zip(tmp_path / "tec.zip", FILERS, COVERS)
    seat = "data/states/tx/districts/state-house-5/offices/state-representative.yaml"
    people = {"jane-doe": {"name": "Jane Doe", "candidacies": []},
              "bob-smith": {"name": "Bob Smith", "candidacies": [f"tx/{HOUSE.removeprefix('tx/')}/candidates/bob-smith"]}}
    for rel, doc in {seat: {"name": "State Representative, District 5", "office_type": "state-representative",
                            "holders": [{"person": p, "start": None, "end": None, "basis": "x"} for p in people],
                            "sources": []},
                     **{f"data/states/tx/people/{p}.yaml": {"id": p, **d, "records": [], "statements": [],
                                                            "funding": [], "promise_tracker": [], "research": "pending"}
                        for p, d in people.items()}}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("# header\n" + yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    config = yaml.safe_load((tmp_path / "site/site.yaml").read_text(encoding="utf-8"))
    tec = tf.TecData(tmp_path / "tec.zip", "2025-01-01", "2026-09-30")
    changes, log = tf.plan_officials(tmp_path, tec, "2026-09-30", config, getter=fake_get)
    assert [p.stem for p, _ in changes] == ["jane-doe"]  # Bob Smith ran in 2026: his candidate file has it
    path, text = changes[0]
    assert text.startswith("# header\n")
    doc = yaml.safe_load(text)
    (fact,) = doc["funding"]
    assert fact["id"] == "tx/people/jane-doe#M-01" and "report 102" in fact["source_title"]
    assert fact["verification"] == "unverified" and "research" not in doc
