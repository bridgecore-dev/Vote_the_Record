"""src/collectors/results.py on a copy of the fictional demo data: matching, writing, and never overwriting."""

import hashlib
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
    # Wren Writein is a declared write-in candidate: their line is their count, and only the rest is other votes
    governor = read(root, "tx/statewide/elections/2026-11-03/governor")
    assert governor["counts"]["wren-writein"] == 12 and governor["other_votes"] == 3


def test_hart_report_one_value_per_line_drops_page_tops_and_takes_the_total():
    top = "Demo County, Texas\nGeneral Election\n11/3/2026\nPage {n}\nUnofficial Results\nRun Time\n9:15 PM\n"
    heads = "Choice\nParty\nEarly Voting\nElection Day\nTotal\n"
    pages = [top.format(n=1) + "GOVERNOR - Vote for none or one\n" + heads
             + "DANA DEMO\nREP\n800\n60.00%\n400\n57.00%\n1,200\n59.00%\n"
             + "Pat Write (W)\n5\n0.10%\n10\n0.20%\n15\n0.20%\n"
             + "Cast Votes:\n805\n100.00%\n410\n100.00%\n1,215\n100.00%\nUndervotes:\n3\n4\n7\nOvervotes:\n0\n0\n0\n"
             + "CITY OF DEMO CITY,\nPROPOSITION A\n" + heads + "FOR\n10\n50.00%\n2\n50.00%\n12\n50.00%\n",
             top.format(n=12) + heads + "AGAINST\n9\n50.00%\n3\n50.00%\n12\n50.00%\nCast Votes:\n19\n100.00%\n"]
    assert results.parse_hart_columns(pages) == [
        ("GOVERNOR", "DANA DEMO", 1200), ("GOVERNOR", "Pat Write (W)", 15),
        ("CITY OF DEMO CITY, PROPOSITION A", "FOR", 12), ("CITY OF DEMO CITY, PROPOSITION A", "AGAINST", 12)]


def test_precinct_csv_uses_the_county_totals_row_and_zip_reads_the_csv_inside(tmp_path):
    import zipfile
    text = ("COUNTY NUMBER,PRECINCT CODE,PRECINCT NAME,REGISTERED VOTERS TOTAL,BALLOTS CAST TOTAL,MAYOR,MAYOR,MAYOR\n"
            ",,,,,NON,NON,NON\n,,,VOTERS,BALLOTS CAST,KAI KIDDING,LEE LOREM,OVER VOTES\n"
            "DEMO,1,Precinct 1,100,50,20,30,0\nDEMO,2,Precinct 2,100,40,25,15,1\nDEMO,ZZZ,COUNTY TOTALS,200,90,45,45,1\n")
    assert results.parse_precinct_csv(text) == [("MAYOR", "KAI KIDDING", 45), ("MAYOR", "LEE LOREM", 45),
                                                ("MAYOR", "OVER VOTES", 1)]
    z = tmp_path / "summary.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("summary.csv", CSV)
    assert ("GOVERNOR", "DANA DEMO", 1200) in results.read_rows(z)


def test_a_name_printed_with_an_unreadable_accent_still_matches():
    assert results.name_keys("Jubilee Pe�a") & results.name_keys("Jubilee Peña")


def test_a_district_number_must_match_whole_and_contest_totals_are_not_a_choice():
    mud2 = {"kind": "measure", "name": "Blue Meadow MUD No. 2, Proposition A", "choices": ["For", "Against"]}
    assert results.measure_fits(mud2, "Proposition A - Blue Meadow MUD No. 2")
    assert not results.measure_fits(mud2, "Proposition A - Blue Meadow MUD No. 4")
    assert not results.measure_fits(mud2, "Proposition A - Blue Meadow MUD No. 12")
    assert results.match_choices(mud2, ["For", "Against", "Overvotes", "Contest Totals"])[2] == []


def test_a_contest_crossing_county_lines_shows_the_county_share_with_more_votes(demo):
    root, export = demo
    target = root / "data/states/tx/localities/demo-city/elections/2026-11-03/prop-a/result.yaml"
    for other, kept in ((100, False), (50000, True)):
        target.write_text(yaml.safe_dump({"election_date": "2026-11-03", "status": "unofficial", "as_of": "2026-11-04",
                                          "covers": "partial", "covers_note": "Votes counted in Other County only.",
                                          "counts": {"FOR": other, "AGAINST": 0}, "sources": []}), encoding="utf-8")
        run(root, export, "--write")
        assert (read(root, "tx/localities/demo-city/elections/2026-11-03/prop-a")["counts"]["FOR"] == other) == kept


def test_a_clarity_summary_zip_in_windows_1252_reads(tmp_path):
    import zipfile
    z = tmp_path / "summary.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("summary.csv", CSV.replace("DANA DEMO", "DANA DEMO PE\xd1A").encode("cp1252"))
    assert ("GOVERNOR", "DANA DEMO PE\xd1A", 1200) in results.read_rows(z)


def test_seats_printed_under_one_title_split_by_their_candidates():
    def race(path, *names):
        return {"path": path, "kind": "race", "name": path, "election_date": "2026-11-03",
                "candidates": [{"id": n.lower().replace(" ", "-"), "name": n, "write_in": False} for n in names]}
    contests = [race("cad-1", "Martin John", "Sam Chatriwala"), race("cad-2", "Dayna Druke", "Muzaffar Vohra"),
                race("cad-3", "Ameer Malik"), race("judge", "Asha Reddi")]
    t = "CAD BOARD OF DIRECTORS (Vote For 1)"
    rows = [(t, "MARTIN JOHN", 5), (t, "SAM CHATRIWALA", 4), (t, "MUZAFFAR VOHRA", 3), (t, "DAYNA DRUKE", 6),
            (t, "AMEER MALIK", 9), ("JUDGE", "REP ASHA REDDI", 1)]
    matched, problems = results.plan(contests, rows, {})
    got = {m["contest"]["path"]: m["counts"] for m in matched}
    assert got["cad-1"] == {"martin-john": 5, "sam-chatriwala": 4} and got["cad-3"] == {"ameer-malik": 9}
    assert got["judge"] == {"asha-reddi": 1}  # a party code printed before the name
    assert all(m["title"] in (t, "JUDGE") for m in matched)


def test_a_shared_name_does_not_match_a_race_with_other_names_listed():
    court = {"path": "ccc-1", "kind": "race", "name": "ccc-1", "election_date": "2026-11-03",
             "candidates": [{"id": "david-cook", "name": "David Cook", "write_in": False}]}
    assert results.match_choices(court, ["David Cook", "Amy Martinez-Salas"]) is None
    assert results.match_choices(court, ["David Cook", "Write-in Totals"]) is not None


def test_an_incumbent_mark_and_quoted_nickname_still_match():
    assert results.name_keys('MARIA LUISA "LULU" FLORES (I)') & results.name_keys("Maria Luisa \u201cLulu\u201d Flores")


def test_a_typed_up_scan_takes_its_hash_from_the_downloaded_report(demo, tmp_path):
    root, export = demo
    scan = tmp_path / "report.pdf"
    scan.write_bytes(b"%PDF-1.4 scanned results report")
    run(root, export, "--source-kind", "document", "--source-file", str(scan), "--write")
    fact = read(root, "tx/localities/demo-county/elections/2026-11-03/jp-precinct-1")["sources"][0]
    assert fact["sha256"] == hashlib.sha256(scan.read_bytes()).hexdigest()


def test_a_spelled_out_district_type_fits_and_the_fuller_name_wins():
    mud7 = {"kind": "measure", "name": "Blue Meadow MUD No. 7, Proposition A", "choices": ["For", "Against"]}
    assert results.measure_fits(mud7, "Blue Meadow Municipal Utility District No. 7 Proposition A (Vote For 1)")
    city = {"path": "tx/localities/city-of-dallas/elections/2026-11-03/proposition-a", "kind": "measure",
            "name": "City of Dallas, Proposition A", "choices": ["For", "Against"]}
    lake = {"path": "tx/localities/city-of-lake-dallas/elections/2026-11-03/proposition-a", "kind": "measure",
            "name": "City of Lake Dallas, Proposition A", "choices": ["For", "Against"]}
    county = {"path": "tx/localities/dallas-county/elections/2026-11-03/proposition-a", "kind": "measure",
              "name": "Dallas County, Proposition A", "choices": ["For", "Against"]}
    rows = [(t, c, 1) for t in ("City of Lake Dallas Proposition A", "DALLAS COUNTY PROPOSITION A")
            for c in ("For", "Against")]
    matched, problems = results.plan([city, lake, county], rows, {})
    assert {m["contest"]["path"].split("/")[2] for m in matched} == {"city-of-lake-dallas", "dallas-county"}
    assert problems == []


def test_a_party_code_printed_after_the_name_still_matches():
    assert results.name_keys("Juan Zamora DEM") & results.name_keys("Juan Zamora")


def test_two_places_races_printed_under_one_title_each_keep_their_own_names():
    def race(path, *names):
        return {"path": path, "kind": "race", "name": path, "election_date": "2026-11-03",
                "candidates": [{"id": n.lower().replace(" ", "-"), "name": n, "write_in": False} for n in names]}
    contests = [race("tx/localities/city-of-a/elections/2026-11-03/mayor", "Bob Yancy", "Elizabeth Cunha"),
                race("tx/localities/city-of-b/elections/2026-11-03/mayor", "Rafael D. Pe\u00f1a III", "Bobby Gutierrez")]
    contests[1]["candidates"][0]["id"] = "rafael-d-pena-iii"
    rows = [("MAYOR", "BOB YANCY", 5), ("MAYOR", "ELIZABETH CUNHA", 4), ("MAYOR", "RAFAEL D. PE?A III", 3),
            ("MAYOR", "BOBBY GUTIERREZ", 6)]
    matched, problems = results.plan(contests, rows, {})
    got = {m["contest"]["path"].split("/")[2]: m["counts"] for m in matched}
    assert got == {"city-of-a": {"bob-yancy": 5, "elizabeth-cunha": 4},
                   "city-of-b": {"rafael-d-pena-iii": 3, "bobby-gutierrez": 6}} and problems == []


def test_a_title_that_comes_back_or_whose_choices_start_over_is_another_contest():
    rows = [("Proposition A", "For", 10), ("Proposition A", "Against", 5), ("Mayor", "Pat", 7),
            ("Proposition A", "For", 3), ("Proposition A", "Against", 4), ("Proposition A", "For", 1)]
    assert [r[0] for r in results.mark_repeats(rows)] == \
        ["Proposition A"] * 2 + ["Mayor"] + ["Proposition A [2]"] * 2 + ["Proposition A [3]"]


def test_a_declared_write_in_candidate_gets_their_own_count_and_the_rest_stay_other_votes():
    race = {"path": "tx/localities/demo-county/elections/2026-11-03/county-clerk", "kind": "race", "name": "County Clerk",
            "election_date": "2026-11-03",
            "candidates": [{"id": "pat-listed", "name": "Pat Listed", "write_in": False},
                           {"id": "terry-writein", "name": "Terry Writein", "write_in": True}]}
    # ES&S: a "Write-In Totals" line sums the named and the uncertified write-ins under it
    rows = [("County Clerk", "Pat Listed", 500), ("County Clerk", "Write-In Totals", 120),
            ("County Clerk", "Write-in: Terry Writein", 100), ("County Clerk", "Write-in Not Certified", 20)]
    (m,), problems = results.plan([race], rows, {})
    assert m["counts"] == {"pat-listed": 500, "terry-writein": 100} and m["other_votes"] == 20 and problems == []
    # the Secretary of State and Hart print each write-in as "NAME (W)" with no total line
    rows = [("COUNTY CLERK", "PAT LISTED", 500), ("COUNTY CLERK", "TERRY WRITEIN (W)", 100),
            ("COUNTY CLERK", "SOMEONE ELSE (W)", 7)]
    (m,), problems = results.plan([race], rows, {})
    assert m["counts"] == {"pat-listed": 500, "terry-writein": 100} and m["other_votes"] == 7
