"""src/social/results_posts.py: story drafts of official results, on fixture data only (never data/)."""
import shutil
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/social"))
import results_posts as rp  # noqa: E402

build = rp.social_build()

FIX = Path(__file__).resolve().parent / "fixtures/social/results"
HIGHLIGHTS = FIX / "highlights.yaml"
E = "2026-11-03"
JUDGE = f"data/states/tx/localities/fixture-county/elections/{E}/county-judge/result.yaml"


def draft(root=FIX, county="fixture-county", use_results=True, highlights=HIGHLIGHTS):
    return rp.drafts(root, county, E, "2026-11-13", highlights, use_results=use_results)


def posts_by_name(**kw):
    return dict(draft(**kw)[0])


def test_county_contests_and_city_contests_go_to_separate_posts():
    posts, notes = draft()
    assert [n for n, _ in posts] == ["2026-11-13-results-fixture-county.yaml",
                                     "2026-11-13-results-fixture-county-cities-districts.yaml"]
    county, cities = posts[0][1], posts[1][1]
    assert [s["head"] for s in county["slides"]] == ["Fixture County Judge"]
    assert [s["head"] for s in cities["slides"]] == ["City of Sample, Proposition A"]
    assert cities["highlight"] == "Results"
    assert cities["record_ids"] == ["tx/localities/city-of-sample/elections/2026-11-03/proposition-a/result/official"]
    assert county["record_ids"] == ["tx/localities/fixture-county/elections/2026-11-03/county-judge/result/official"]
    # unofficial, no result, statewide, and another county's share of a city contest are all left out
    assert ("skipped 1 unofficial, 1 with no result yet, 1 statewide/district contests, "
            "1 counted in another county") in notes[0]


def test_candidates_alphabetical_exact_counts_no_party():
    post = posts_by_name()["2026-11-13-results-fixture-county.yaml"]
    res = post["slides"][0]["results"]
    assert res["candidates"] == [{"name": "Bo Young", "votes": 390}, {"name": "Cy Moss (write-in)", "votes": 10},
                                 {"name": "Zoe Adams", "votes": 600}]
    assert res["status"] == "official" and res["as_of"] == "Nov. 12, 2026"
    assert "Party" not in rp.dump(post)


def test_measure_choices_alphabetical_too():
    post = posts_by_name()["2026-11-13-results-fixture-county-cities-districts.yaml"]
    assert [c["name"] for c in post["slides"][0]["results"]["candidates"]] == ["AGAINST", "FOR"]


def test_outcome_as_recorded_same_words_and_notes():
    p = posts_by_name()
    judge = p["2026-11-13-results-fixture-county.yaml"]["slides"][0]
    prop = p["2026-11-13-results-fixture-county-cities-districts.yaml"]["slides"][0]
    assert prop["text"].startswith("Canvass outcome: Passed.")
    assert "Votes counted in Fixture County only." in prop["text"]
    assert judge["text"].startswith("Canvass outcome: Elected: Zoe Adams.")
    assert "Other write-in votes: 5" in judge["text"]
    assert judge["by"] == prop["by"] == rp.PERCENT_NOTE  # the percentages are computed, and every slide says so


def test_no_outcome_says_not_recorded(tmp_path):
    root = tmp_path / "r"
    shutil.copytree(FIX, root)
    doc = yaml.safe_load((root / JUDGE).read_text(encoding="utf-8"))
    del doc["outcome"]
    (root / JUDGE).write_text(yaml.safe_dump(doc), encoding="utf-8")
    post = posts_by_name(root=root)["2026-11-13-results-fixture-county.yaml"]
    assert post["slides"][0]["text"].startswith("Canvass outcome: not recorded yet.")


def test_sources_approval_and_highlight_fallback():
    posts, notes = draft()
    post = posts[0][1]
    assert post["sources"] == [{"title": "Fixture County Official Canvass",
                                "url": "https://www.fixturecounty.tx.gov/canvass.pdf", "retrieved": "2026-11-12"}]
    assert post["approval"] == "auto"
    assert post["highlight"] == "Local" and any("'Fixture Co.' Highlight could be added" in n for n in notes)


def test_county_highlight_and_review_for_non_gov(tmp_path):
    root = tmp_path / "r"
    shutil.copytree(FIX, root)
    (root / JUDGE).write_text((root / JUDGE).read_text(encoding="utf-8").replace(
        "www.fixturecounty.tx.gov", "results.example.com"), encoding="utf-8")
    hl = tmp_path / "h.yaml"
    hl.write_text("places:\n- {name: Fixture Co., icon: pin}\n", encoding="utf-8")
    posts, notes = draft(root, highlights=hl)
    assert posts[0][1]["highlight"] == "Fixture Co."
    assert posts[0][1]["approval"] == "review"
    assert posts[1][1]["highlight"] == "Local" and any("No 'Results' Highlight" in n for n in notes)


def make_county(tmp_path, n):
    """A county with n official two-candidate contests of its own."""
    base = tmp_path / f"data/states/tx/localities/big-county/elections/{E}"
    paths = []
    for i in range(1, n + 1):
        d = base / f"seat-{i}"
        (d / "candidates").mkdir(parents=True)
        (d / "race.yaml").write_text(f"name: Seat {i}\nelection_date: '{E}'\n", encoding="utf-8")
        for cid, name in (("a", "Ann Able"), ("b", "Ben Baker")):
            (d / f"candidates/{cid}.yaml").write_text(f"id: {cid}\nname: {name}\n", encoding="utf-8")
        (d / "result.yaml").write_text(yaml.safe_dump({
            "election_date": E, "status": "official", "as_of": "2026-11-12", "covers": "whole",
            "counts": {"b": 10 + i, "a": 20}, "outcome": {"result": "elected", "candidates": ["a"]},
            "sources": [{"source_url": "https://x.tx.us/c.pdf", "source_title": "Canvass", "retrieved": "2026-11-12"}]}),
            encoding="utf-8")
        paths.append(f"tx/localities/big-county/elections/{E}/seat-{i}")
    (base / "ballot.yaml").write_text(yaml.safe_dump({"name": "Big County ballot", "contests": paths}), encoding="utf-8")
    return tmp_path


def test_splits_at_ten_slides(tmp_path):
    root = make_county(tmp_path, 12)
    posts, _ = draft(root, "big-county")
    assert [n for n, _ in posts] == ["2026-11-13-results-big-county-1.yaml", "2026-11-13-results-big-county-2.yaml"]
    assert [len(p["slides"]) for _, p in posts] == [10, 2]
    assert "(part 1 of 2)" in posts[0][1]["caption"]
    assert len({r for _, p in posts for r in p["record_ids"]}) == 12


@pytest.mark.parametrize("use_results", [True, False])
def test_drafts_pass_build_check(tmp_path, use_results):
    if use_results and "results" not in build.SLIDE_KEYS:
        pytest.skip("build.py here has no `results` slide field yet")
    root = make_county(tmp_path, 12)
    for _, post in draft(use_results=use_results)[0] + draft(root, "big-county", use_results=use_results)[0]:
        post = yaml.safe_load(rp.dump(post))  # as the file reads back
        if not use_results:
            assert all("rows" in s and "results" not in s for s in post["slides"])
        assert build.check(post) == []


def test_rows_fallback_percent():
    p = posts_by_name(use_results=False)
    judge = p["2026-11-13-results-fixture-county.yaml"]["slides"][0]
    prop = p["2026-11-13-results-fixture-county-cities-districts.yaml"]["slides"][0]
    assert judge["rows"][2] == ["Zoe Adams", "600 votes · 60.0%"]  # of the 1,000 votes for the names listed
    assert prop["rows"] == [["AGAINST", "100 votes · 25.0%"], ["FOR", "300 votes · 75.0%"]]


def test_write_and_list(tmp_path, capsys):
    out = tmp_path / "posts"
    args = ["--root", str(FIX), "--highlights", str(HIGHLIGHTS), "--date", "2026-11-13"]
    assert rp.main(["--county", "fixture-county", "--write", "--out", str(out)] + args) == 0
    assert sorted(p.name for p in out.iterdir()) == ["2026-11-13-results-fixture-county-cities-districts.yaml",
                                                     "2026-11-13-results-fixture-county.yaml"]
    rp.main(["--county", "fixture-county", "--write", "--out", str(out)] + args)
    assert "SKIP" in capsys.readouterr().err  # never overwrites
    rp.main(["--list"] + args)
    assert "fixture-county: 2 official, 1 unofficial, of 5 local contests" in capsys.readouterr().out
