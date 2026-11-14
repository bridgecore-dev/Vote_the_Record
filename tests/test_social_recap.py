"""The weekly/monthly Congress recap drafter (src/social/recap.py), against saved official feeds: no network.

Fixtures in tests/fixtures/social/recap/ are trimmed copies of the official feeds retrieved 2026-11-13 (GovInfo
plaw RSS and MODS, the House Clerk's 2026 roll-call list and roll XML with member lists removed but party
totals kept, the Senate's 119th Congress 2nd session vote menu, api.congress.gov CRS summaries). Made up for
tests: mods-119-136.xml (an earlier law that stops the walk) and the 137/138 items in plaw.xml.
"""
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/social"))
import recap  # noqa: E402

build = recap.build  # src/social/build.py (the post builder)

FIX = Path(__file__).resolve().parent / "fixtures/social/recap"
TODAY = dt.date(2026, 11, 13)
FILES = {
    recap.PLAW_RSS: "plaw.xml",
    recap.MODS.format(c=119, n=136): "mods-119-136.xml",
    recap.MODS.format(c=119, n=137): "mods-119-137.xml",
    recap.MODS.format(c=119, n=138): "mods-119-138.xml",
    recap.HOUSE_INDEX.format(y=2026): "house-index-2026.html",
    recap.HOUSE_PAGE.format(y=2026, page="ROLL_300.asp"): "house-roll-300.html",
    **{recap.HOUSE_ROLL.format(y=2026, n=n): f"house-roll-2026-{n}.xml" for n in (315, 316, 321, 323)},
    recap.SENATE_MENU.format(c=119, s=2): "senate-menu-119-2.xml",
    recap.SUMMARIES.format(c=119, t="hr", n=8205): "summaries-119-hr-8205.json",
    recap.SUMMARIES.format(c=119, t="hr", n=9612): "summaries-119-hr-9612.json",
}


class Fake:
    """Serves the fixtures; anything else is a 404, and a URL in `refuse` answers 403. Records every request."""

    def __init__(self, refuse=()):
        self.calls, self.refuse = [], set(refuse)

    def get(self, url, headers=None):
        self.calls.append((url, dict(headers or {})))
        if any(url.startswith(r) for r in self.refuse):
            raise recap.Blocked(url, 403)
        if url in FILES:
            return (FIX / FILES[url]).read_bytes()
        raise recap.Blocked(url, 404)


def draft(week, **kw):
    s, e, k = recap.period(week)
    return recap.draft(kw.pop("fetch", None) or Fake(), s, e, k, today=TODAY, taken=kw.pop("taken", {}), **kw)


def filled(post):
    """The post as a person would finish it: each blank filled to the room the drafter kept for it."""
    post = json.loads(json.dumps(post))
    for b in post["bills"]:
        b["does"] = b["does"] or "d" * recap.BUDGET["does"]
        b["affects"] = b["affects"] or "a" * recap.BUDGET["affects"]
        b["summary"]["text"] = b["summary"]["text"] or "s" * 300
    return post


def test_period_and_dates():
    assert recap.period("2026-11-12") == (dt.date(2026, 11, 9), dt.date(2026, 11, 15), "week")
    assert recap.period(month="2026-12") == (dt.date(2026, 12, 1), dt.date(2026, 12, 31), "month")
    assert recap.span(dt.date(2026, 11, 9), dt.date(2026, 11, 15)) == "Nov. 9-15, 2026"
    assert recap.span(dt.date(2026, 11, 30), dt.date(2026, 12, 6)) == "Nov. 30-Dec. 6, 2026"
    assert recap.span(dt.date(2026, 12, 28), dt.date(2027, 1, 3)) == "Dec. 28, 2026-Jan. 3, 2027"
    assert recap.span(dt.date(2026, 9, 1), dt.date(2026, 9, 30)) == "September 2026"
    assert recap.congress_of(2026) == (119, 2) and recap.congress_of(2027) == (120, 1)


def test_bill_ids_from_each_feed_and_resolutions_left_out():
    assert recap.bill("H R 9612", 119)["id"] == "H.R. 9612"
    assert recap.bill("H J RES 213", 119)["id"] == "H.J.Res. 213"
    assert recap.bill("H.J.Res. 140", 119)["type"] == "hjres"
    assert recap.bill("S. 4668", 119)["page"] == "https://www.congress.gov/bill/119th-congress/senate-bill/4668"
    for raw in ("H RES 1247", "H CON RES 93", "S.Res. 817", "PN1181-7", ""):
        assert recap.bill(raw, 119) is None
    for b in ("H.R. 9612", "H.J.Res. 213", "S. 12"):  # the builder recognizes each as a bill
        assert build.BILL.fullmatch(b)


def test_week_counts_and_final_passage_votes_without_a_key():
    post = draft("2026-11-09")
    first = post["slides"][0]
    assert first["rows"] == [["New public laws", "0"], ["House roll-call votes", "9"], ["Senate roll-call votes", "6"]]
    listed = [r[0] for s in post["slides"] for r in s.get("rows") or [] if s.get("kicker", "").startswith("Final")]
    # every final-passage vote on a bill, in date order; the Senate's motions on H.R. 2347 and the House's
    # rules, discharge and referral votes are counted but not listed
    assert listed[:3] == ["H.R. 3978", "H.R. 9612", "H.R. 1013"]
    assert "H.R. 2347" not in json.dumps(post)
    assert post["approval"] == "review" and post["kind"] == "carousel" and post["level"] == "congress"
    assert "place" not in post
    for b in post["bills"]:  # no key: nothing invented, the gaps are loud
        assert b["does"] == "" and b["affects"] == "" and b["summary"]["text"] == ""
    assert "CONGRESS_API_KEY is not set" in post["notes"]
    assert all(s["retrieved"] == "2026-11-13" for s in post["sources"])
    assert {s["url"] for s in post["sources"]} >= {recap.PLAW_RSS, recap.SENATE_MENU.format(c=119, s=2)}
    assert post["record_ids"][0] == "us/119/recap/week-2026-11-09"
    assert "us/119/2/house/roll/316" in post["record_ids"]
    errs = build.check(post)
    # only the bill text is missing (an explainer slide's head is the bill's `does`)
    assert errs and all(e.startswith("bill ") or "needs head" in e for e in errs), errs


def test_tallies_exactly_as_recorded_and_never_by_party():
    post = draft("2026-11-09")
    rows = dict(r for s in post["slides"] for r in s.get("rows") or [])
    assert rows["H.R. 9612"] == "House, Nov. 9: Passed, 394-3"  # roll 316 whole-chamber totals
    assert rows["H.R. 1013"] == "House, Nov. 11: Passed, 295-128"
    text = json.dumps(post)
    for word in ("Republican", "Democrat", "Independent", "party"):
        assert word not in text


def test_with_a_key_the_crs_summary_is_quoted_word_for_word():
    fake = Fake()
    post = draft("2026-11-09", fetch=fake, key="test-key")
    b = next(b for b in post["bills"] if b["id"] == "H.R. 9612")
    assert b["summary"]["text"].startswith("This bill allows for the construction of a uranium enrichment facility")
    assert "following analysis" not in b["summary"]["text"]  # the italic lead-in is not the summary
    assert len(b["summary"]["text"]) <= 300
    assert b["does"] == ""  # its first sentence is 141 characters: too long to use whole, so a person writes it
    assert b["affects"] == "" and "write `affects` and `does`" in post["notes"]
    assert b["summary"]["url"] == "https://www.congress.gov/bill/119th-congress/house-bill/9612"
    sent = [h for u, h in fake.calls if "api.congress.gov" in u]
    assert sent and all(h == {"X-Api-Key": "test-key"} for h in sent)  # a header, never in a URL
    assert all("test-key" not in u for u, _ in fake.calls) and "test-key" not in json.dumps(post)


def test_a_refused_api_is_asked_once_and_left_for_a_person():
    fake = Fake(refuse=["https://api.congress.gov"])
    post = draft("2026-11-09", fetch=fake, key="test-key")
    assert sum("api.congress.gov" in u for u, _ in fake.calls) == 1
    assert "refused the request (403); not retried" in post["notes"]


def test_a_refused_feed_stops_the_run():
    with pytest.raises(recap.Blocked):
        draft("2026-11-09", fetch=Fake(refuse=["https://www.senate.gov"]))


def test_laws_by_short_title_and_already_posted_records_left_out():
    post = draft("2026-11-02", key="k", taken={"us/119/public-law/137": "2026-11-13-new-federal-laws-137-138.yaml"})
    law = next(s for s in post["slides"] if s.get("kicker") == "Became law")
    assert [r[0] for r in law["rows"]] == ["H.R. 1744", "H.R. 8205"]
    assert law["rows"][1][1].startswith("Accelerating Access to Critical Therapies for ALS Reauthorization Act of 2026")
    assert "Public Law 119-138, approved Nov. 3" in post["caption"]
    assert "us/119/public-law/138" in post["record_ids"] and "us/119/public-law/137" not in post["record_ids"]
    assert "2026-11-13-new-federal-laws-137-138.yaml" in post["notes"]
    b = next(b for b in post["bills"] if b["id"] == "H.R. 8205")
    assert b["where"] == "Became Public Law 119-138 on Nov. 3, 2026."
    assert b["summary"]["text"].startswith("This act reauthorizes through FY2031")  # the newest CRS version


def test_newest_law_found_past_a_lagging_feed():
    fake = Fake()
    assert recap.newest(fake, 119, 136) == 138  # the feed only went up to 136
    assert len(fake.calls) < 20


def test_finished_draft_passes_the_builder_and_fits():
    for week, reel in (("2026-11-09", False), ("2026-11-02", False), ("2026-11-09", True)):
        post = filled(draft(week, key="k", reel=reel))
        assert build.check(post) == [], (week, reel)
        assert len(build.full_caption(post)) <= 2200
        lo, hi = (2, 20) if reel else (2, 10)
        assert lo <= len(build.expanded(post)) <= hi


def test_a_cut_list_says_so_on_a_slide_and_in_the_rule():
    post = draft("2026-11-09")
    shown = sum(len(s.get("rows") or []) for s in post["slides"] if s.get("kicker", "").startswith("Final"))
    if shown < 4:  # four final-passage votes that week
        assert post["slides"][-1]["kicker"] == "Not shown"
        assert f"{4 - shown} more final-passage vote" in post["slides"][-1]["head"]
    assert "in date order" in post["rule"] and "the list stops in that order" in post["rule"]


def test_does_is_the_first_sentence_verbatim_when_it_fits():
    class Short(Fake):
        def get(self, url, headers=None):
            if "api.congress.gov" in url:
                return json.dumps({"summaries": [{"actionDate": "2026-11-09", "text":
                    "<p><strong>Short Act</strong></p><p>This bill renames a post office. It takes effect at once.</p>"}]}).encode()
            return super().get(url, headers)

    post = draft("2026-11-09", fetch=Short(), key="k")
    b = post["bills"][0]
    assert b["does"] == "This bill renames a post office."
    assert b["summary"]["text"] == "This bill renames a post office. It takes effect at once."
    assert "write `affects` from the CRS summary" in post["notes"]


def test_reel_version():
    post = draft("2026-11-09", reel=True)
    assert post["kind"] == "reel" and all("seconds" in s for s in post["slides"])


def test_party_name_in_a_feed_stops_the_draft(monkeypatch):
    real = recap.assemble

    def with_party(*a, **k):
        post = real(*a, **k)
        post["caption"] += " Republican"
        return post

    monkeypatch.setattr(recap, "assemble", with_party)
    with pytest.raises(SystemExit, match="party name"):
        draft("2026-11-09")


def test_filename():
    assert recap.filename(dt.date(2026, 11, 9), "week", False, TODAY) == "2026-11-13-congress-week-2026-11-09.yaml"
    assert recap.filename(dt.date(2026, 11, 1), "month", True, TODAY) == "2026-11-13-congress-month-2026-11-reel.yaml"


def test_bills_carry_the_official_short_title_only():
    laws = {b["id"]: b for b in draft("2026-11-02", key="k")["bills"]}
    assert laws["H.R. 8205"]["title"] == "Accelerating Access to Critical Therapies for ALS Reauthorization Act of 2026"
    votes = {b["id"]: b.get("title") for b in draft("2026-11-09", key="k")["bills"]}
    assert votes.get("H.R. 9612") == "American Enrichment Deployment Act"  # the Clerk's vote description
    assert not recap.SHORT_TITLE.fullmatch("A bill to amend the Internal Revenue Code of 1986 Act")
    assert not recap.SHORT_TITLE.fullmatch("To amend title 38, United States Code, and for other purposes")
