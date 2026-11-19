"""Real-browser check of the demo build: CSP, survey results, focus, themes, phone width.

Needs Node.js and Chrome, Chromium, or Edge. Skipped locally when either is missing;
set REQUIRE_BROWSER=1 (as CI does) to make a missing browser a failure.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "build"))
from build import build  # noqa: E402

CANDIDATE_BROWSERS = [
    os.environ.get("BROWSER_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_browser():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "msedge"):
        if shutil.which(name):
            return shutil.which(name)
    return next((p for p in CANDIDATE_BROWSERS if p and Path(p).is_file()), None)


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    node, browser = shutil.which("node"), find_browser()
    if not node or not browser:
        message = f"Browser check needs Node and Chrome/Edge (node={node}, browser={browser})."
        if os.environ.get("REQUIRE_BROWSER") == "1":
            pytest.fail(message)
        pytest.skip(message)
    out = tmp_path_factory.mktemp("demo") / "dist"
    assert build(REPO / "tests" / "fixtures" / "demo", out, demo=True, today="2026-09-29") == 0
    result = subprocess.run([node, str(REPO / "tests" / "browser" / "survey_check.mjs"), str(out), browser],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_no_csp_violations_console_errors_or_offsite_requests(report):
    assert report["problems"] == {"cspViolations": [], "consoleErrors": [], "offsiteRequests": []}


def test_percentage_matches_methodology_for_avery(report):
    # S-01 alignment 3.0/3.4 at importance 3; S-03 and S-04 aligned at importance 2; S-02 unverified.
    expected = round(100 * (3 * (3.0 / 3.4) + 2 + 2) / 7)
    assert report["survey"]["meters"][0] == expected
    assert "Based on 3 of 4 questions." in report["survey"]["text"]["Avery Example"]


def test_beta_breakdown_and_no_record_states(report):
    survey = report["survey"]
    assert "Not enough record to compare." in survey["text"]["Blake Sample"]
    assert survey["betaBreakdownOpen"] == [False, True, None]
    assert survey["text"]["Casey Placeholder"].endswith("We found no record for this candidate in the sources we reviewed, so they can't be compared.")
    assert survey["unverifiedBadgesInResults"] >= 2  # unverified record and unverified link


def test_results_alphabetical_and_focus_moves_to_results(report):
    assert report["survey"]["order"] == ["Avery Example", "Blake Sample", "Casey Placeholder"]
    assert report["survey"]["focusedId"] == "results-heading"


def test_dark_mode_reduced_motion_and_phone_width(report):
    assert report["dark"] == "rgb(25, 24, 22)"  # --bg in dark mode (site.css)
    assert report["light"] == "rgb(237, 234, 227)"  # --bg in light mode
    assert report["scrollBehaviorReduced"] == "auto"
    assert report["phoneOverflow"] == 0


def test_results_grid_compares_answers_with_each_record(report):
    survey = report["survey"]
    assert survey["gridHead"] == ["Question", "Avery Example", "Blake Sample", "Casey Placeholder"]
    rows = {r[0]: r[1:] for r in survey["gridRows"]}
    assert rows["Budget gap"] == ["◐ Mixed", "✗ Different", "— No record"]      # Avery 3.0 of 3.4 agrees
    assert rows["Storm coming"] == ["· Record not counted", "— No record", "— No record"]  # unverified record
    assert rows["Disaster repairs"] == ["✓ Same as you", "· Record not counted", "— No record"]  # Blake's is disputed
    assert rows["Road money"] == ["✓ Same as you", "— No record", "— No record"]
    assert list(rows) == ["Budget gap", "Storm coming", "Disaster repairs", "Road money"]  # only answered questions


ALL_DEMO_RACES = ["Demo City Council, Place 1", "Demo City, Proposition A", "Demo County Commissioner, Precinct 9",
                  "Justice of the Peace, Precinct 1", "Justice of the Peace, Precinct 2", "Governor",
                  "U.S. Representative, District 1", "U.S. Representative, District 2"]


def test_ballot_starts_with_every_race_and_shows_the_zip_form(report):
    initial = report["ballot"]["initial"]
    assert initial["formShown"] is True
    assert initial["races"] == ALL_DEMO_RACES
    assert initial["split"] == []


def test_zip_filters_races_and_flags_split_districts(report):
    z = report["ballot"]["zip22222"]
    assert z["races"] == ["Justice of the Peace, Precinct 2", "Governor",
                          "U.S. Representative, District 1", "U.S. Representative, District 2"]
    assert z["split"] == ["U.S. Representative, District 1", "U.S. Representative, District 2"]
    assert z["groups"] == ["Your county", "Your state", "National"]  # empty "Closest to home" hidden
    assert z["status"] == ("ZIP code 22222: 4 races on your ballot, closest to home first. "
                           "2 races depend on your exact address. Add your precinct number to be sure.")
    assert z["hash"] == "#zip=22222"
    assert report["ballot"]["reset"]["races"] == ALL_DEMO_RACES


def test_home_zip_box_opens_the_ballot_filtered(report):
    z = report["ballot"]["fromHome11111"]
    assert z["hash"] == "#zip=11111"
    assert z["races"] == ["Demo City Council, Place 1", "Demo City, Proposition A", "Demo County Commissioner, Precinct 9",
                          "Justice of the Peace, Precinct 1", "Governor", "U.S. Representative, District 1"]
    assert "don't have ballot information for ZIP code 99999" in report["ballot"]["homeUnknown"]


def test_ballot_fits_a_phone_screen(report):
    assert report["phoneOverflowBallot"] <= 0


def test_precinct_number_gives_exact_races_and_wins_over_zip(report):
    p = report["ballot"]["precinct102"]
    assert p["races"] == ["Justice of the Peace, Precinct 2", "Governor", "U.S. Representative, District 2"]
    assert p["split"] == []
    assert p["status"] == "Precinct 102: 3 races on your ballot, closest to home first."
    assert p["hash"] == "#precinct=102"


def test_location_from_home_finds_the_precinct_and_drops_coordinates_from_the_url(report):
    p = report["ballot"]["fromLocation"]
    assert p["hash"] == "#precinct=102"  # the map location is not kept in the URL
    assert p["precinctBox"] == "102"
    assert p["races"] == ["Justice of the Peace, Precinct 2", "Governor", "U.S. Representative, District 2"]
    assert p["status"].startswith("Found your address. Precinct 102: 3 races")
