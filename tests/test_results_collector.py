"""src/collectors/results.py on a copy of the fictional demo data: matching, writing, and never overwriting."""

import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "collectors"))
sys.path.insert(0, str(REPO / "src" / "validate"))
import results  # noqa: E402
from validate import validate  # noqa: E402

BALLOT = "data/states/tx/localities/demo-county/elections/2026-11-03/ballot.yaml"
CSV = """Contest Name,Choice Name,Party,Total Votes
GOVERNOR,DANA DEMO,REP,"1,200"
GOVERNOR,EVAN EXAMPLE,DEM,900
GOVERNOR,Write-in,,15
GOVERNOR,Over Votes,,3
JUSTICE OF THE PEACE PCT 1,INDY IMAGINARY,REP,700
CITY OF DEMO CITY PROPOSITION A,FOR,,12500
CITY OF DEMO CITY PROPOSITION A,AGAINST,,9900
CITY COUNCIL PLACE 1,KAI KIDDING,,400
CITY COUNCIL PLACE 1,LEE LOREM,,410
SOMETHING ELSE,NOBODY HERE,,5
"""


@pytest.fixture
def demo(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(REPO / "tests/fixtures/demo", root)
    shutil.copytree(REPO / "schemas", root / "schemas")
    for f in root.rglob("result.yaml"):
        f.unlink()
    export = tmp_path / "export.csv"
    export.write_text(CSV, encoding="utf-8")
    return root, export


def run(root, export, *extra):
    return results.main(["--root", str(root), "--ballot", BALLOT, "--file", str(export), "--source-url",
                         "https://example.org/results", "--source-title", "DEMO County: Election Night Results",
                         "--as-of", "2026-11-04", "--retrieved", "2026-11-04", *extra])


def read(root, path):
    return yaml.safe_load((root / "data/states" / path / "result.yaml").read_text(encoding="utf-8"))


def test_dry_run_writes_nothing(demo, capsys):
    root, export = demo
    run(root, export)
    assert not list(root.rglob("result.yaml"))
    out = capsys.readouterr().out
    # a county's report doesn't write statewide races: their totals come from the Secretary of State
    assert "NO MATCH: SOMETHING ELSE" in out and "NO MATCH: GOVERNOR" in out and "3 contests matched" in out


def test_write_matches_by_candidates_and_passes_the_validator(demo):
    root, export = demo
    run(root, export, "--write")
    assert not (root / "data/states/tx/statewide/elections/2026-11-03/governor/result.yaml").exists()
    jp = read(root, "tx/localities/demo-county/elections/2026-11-03/jp-precinct-1")
    assert jp["covers"] == "whole" and jp["status"] == "unofficial"
    assert jp["sources"][0]["id"].endswith("/result#S-01") and jp["sources"][0]["verification"] == "unverified"
    prop = read(root, "tx/localities/demo-city/elections/2026-11-03/prop-a")
    assert prop["counts"] == {"FOR": 12500, "AGAINST": 9900} and "outcome" not in prop
    assert validate(root).errors == []


def test_never_replaces_official_with_unofficial_and_numbers_new_reports(demo):
    root, export = demo
    run(root, export, "--write", "--status", "official")
    run(root, export, "--write")  # an unofficial report later must not replace the canvass
    jp = read(root, "tx/localities/demo-county/elections/2026-11-03/jp-precinct-1")
    assert jp["status"] == "official" and jp["sources"][0]["id"].endswith("#S-01")
    run(root, export, "--write", "--status", "official", "--as-of", "2026-11-15")
    jp = read(root, "tx/localities/demo-county/elections/2026-11-03/jp-precinct-1")
    assert jp["as_of"] == "2026-11-15" and jp["sources"][0]["id"].endswith("#S-02")


HART = """Cumulative Report  -  Unofficial
Demo County, Texas  -  General Election  -  November 03, 2026
Page 1 of 2
Election Early Absentee Total Party Candidate
Justice of the Peace, Precinct 1, Vote For 1
 93,400 43.20% 42.70%  700,630 34.84% 46.49%  61,681  545,549 REP Indy Imaginary
 0 0.00% 0.00%  12 0.00% 0.00%  1  11 Someone Else (W)
 200,898  1,640,818 Cast Votes: 99.13% 99.04% 98.90% 98.61%  177,043  1,262,877
Under Votes:
Demo City, Council, Place 1,
Vote For 1
 5 1% 1%  40 1% 1%  10  25 Kai Kidding
"""


def test_hart_cumulative_report_text_takes_the_total_and_keeps_wrapped_titles():
    rows = results.parse_hart_text(HART)
    assert rows == [("Justice of the Peace, Precinct 1", "Indy Imaginary", 700630),
                    ("Justice of the Peace, Precinct 1", "Someone Else (W)", 12),
                    ("Demo City, Council, Place 1", "Kai Kidding", 40)]
    assert results.WRITE_IN.search("Someone Else (W)")


def test_statewide_report_counts_in_full_and_a_county_share_never_replaces_it(demo):
    root, export = demo
    common = ["--root", str(root), "--file", str(export), "--source-url", "https://example.org/sos",
              "--source-title", "DEMO Secretary of State: Election Night Results", "--as-of", "2026-11-04",
              "--retrieved", "2026-11-04", "--write"]
    results.main(["--statewide", "TX", "2026-11-03", *common])
    gov = read(root, "tx/statewide/elections/2026-11-03/governor")
    assert gov["counts"] == {"dana-demo": 1200, "evan-example": 900} and gov["other_votes"] == 15
    assert gov["covers"] == "whole" and "covers_note" not in gov
    run(root, export, "--write", "--as-of", "2026-11-05", "--whole")  # a county's later report is only its share
    assert read(root, "tx/statewide/elections/2026-11-03/governor")["covers"] == "whole"
    assert validate(root).errors == []


ESS = """Summary Results Report
General Joint
Unofficial Final
Demo County
Governor
Vote For 1
EV-In
Person
Dana Demo 1,200 900 300 0 012.0%
Evan Example 900 700 200 0 08.0%
Write-In Totals 15 10 5 0 00.1%
Write-In: Wren Writein 12 8 4 0 00.1%
Write-In: Uncertified 3 2 1 0 00.0%
Total Votes Cast 2,115 1,610 505 0 0100.00%
Overvotes 1 0 1 0 0
Undervotes 20 10 10 0 0
Summary Results Report
General Joint
Unofficial Final
Demo County
Justice of the Peace, Precinct
1
Vote For 1
Indy Imaginary 700 500 200 0 0100.00%
Undervotes 5 1 4 0 0
Summary Results Report
General Joint
Unofficial Final
Demo County
"""


def test_ess_summary_report_titles_wrap_and_write_in_totals_count_once(demo, tmp_path):
    rows = results.parse_hart_text(ESS)
    titles = list(dict.fromkeys(t for t, _, _ in rows))
    assert titles == ["Governor", "Justice of the Peace, Precinct 1"]
    root, _ = demo
    export = tmp_path / "ess.json"
    export.write_text(__import__("json").dumps(
        {"contests": [{"name": t, "choices": [{"name": c, "votes": v} for tt, c, v in rows if tt == t]} for t in titles]}),
        encoding="utf-8")
    results.main(["--statewide", "TX", "2026-11-03", "--root", str(root), "--file", str(export), "--source-url",
                  "https://example.org/sos", "--source-title", "DEMO", "--as-of", "2026-11-04", "--write"])
    assert read(root, "tx/statewide/elections/2026-11-03/governor")["other_votes"] == 15
