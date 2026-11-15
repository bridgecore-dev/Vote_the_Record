"""src/social/build.py: the post-file rules, without rendering anything (no Chrome, no ffmpeg)."""
import copy
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src/social"))
# loaded by path: "build" is also the site builder's name
_spec = importlib.util.spec_from_file_location("social_build", ROOT / "src/social/build.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

SRC = {"title": "Office of the Clerk, Roll Call 316", "url": "https://clerk.house.gov/Votes/2026316", "retrieved": "2026-11-14"}
BILL = {"id": "H.R. 9999", "does": "Would let people deduct transit costs.", "affects": "Transit riders who pay income tax.",
        "where": "Passed the House Nov. 12.", "summary": {"text": "This bill allows a deduction for transit.",
                                                        "title": "CRS summary", "url": "https://www.congress.gov/bill/x",
                                                        "retrieved": "2026-11-14"}}


def post(**kw) -> dict:
    p = {"kind": "carousel", "level": "congress", "approval": "auto", "sources": [SRC],
         "slides": [{"head": "The House voted on Nov. 12."}, {"head": "Here is the tally.", "vote": {"yea": 251, "nay": 180}}],
         "caption": "The House voted.", "hashtags": ["congress"]}
    p.update(kw)
    return p


def errs(p, **kw) -> list[str]:
    return B.check(p, **kw)


def test_a_plain_post_passes():
    assert errs(post()) == []


def test_every_post_cites_a_source():
    assert any("no sources" in e for e in errs(post(sources=[])))


def test_auto_approval_needs_government_sources():
    news = {"title": "News", "url": "https://news.example.com/story", "retrieved": "2026-11-14"}
    assert any(".gov" in e for e in errs(post(sources=[news])))
    assert errs(post(sources=[news], approval="review")) == []


def test_a_named_bill_must_be_explained():
    p = post(slides=[{"head": "The House passed H.R. 9999."}, {"head": "Next"}], approval="review")
    assert any("without saying what it does" in e for e in errs(p))
    assert errs(dict(p, bills=[BILL])) == []


def test_explaining_a_bill_needs_review_and_more_than_one_image():
    p = post(slides=[{"head": "The House passed H.R. 9999."}, {"head": "Next"}], bills=[BILL])
    assert any("judgment call" in e for e in errs(p))
    assert any("single image" in e for e in errs(dict(p, kind="image", approval="review", slides=p["slides"][:1])))


def test_us_is_not_read_as_a_senate_bill():
    assert B.mentioned_bills(post(caption="The U.S. 2026 budget.")) == {}


def test_editorial_rules_wait_for_the_pull_request():
    # a merged post still builds after a rule is added; --check on the pull request applies it
    p = post(slides=[{"head": "The House passed H.R. 9999."}, {"head": "Next"}])
    assert errs(p, editorial=False) == []
    assert errs(p)


def test_the_explainer_follows_the_first_mention_and_carousels_say_swipe():
    p = post(slides=[{"head": "The House passed H.R. 9999."}, {"head": "Next"}], bills=[BILL], approval="review")
    slides = B.expanded(p)
    assert slides[1]["chip"] == "H.R. 9999" and slides[0].get("swipe")
    assert not B.expanded(dict(p, kind="reel"))[0].get("swipe")  # a Reel plays itself


def test_a_recap_caption_gets_one_line_per_bill():
    bills = [dict(BILL, id=f"H.R. 999{n}") for n in range(3)]
    caption = B.full_caption(post(bills=bills))
    assert "on the slides" in caption and caption.count("Who it affects") == 0


RESULTS = {"contest": "County Judge", "status": "official", "as_of": "Nov. 13, 2026",
           "candidates": [{"name": "Alex Example", "votes": 9}, {"name": "Jordan Sample", "votes": 5}]}


def results_errs(**kw):
    return errs(post(slides=[{"head": "Results", "results": dict(copy.deepcopy(RESULTS), **kw)}, {"head": "Next"}]))


def test_results_are_alphabetical_unless_a_measure():
    flipped = list(reversed(RESULTS["candidates"]))
    assert any("alphabetically" in e for e in results_errs(candidates=flipped))
    assert results_errs(candidates=flipped, measure=True) == []


def test_party_is_the_ballots_listing_for_everyone_or_no_one():
    some = [dict(RESULTS["candidates"][0], party="REP"), RESULTS["candidates"][1]]
    assert any("every candidate a party" in e for e in results_errs(candidates=some))
    everyone = [dict(RESULTS["candidates"][0], party="REP"), dict(RESULTS["candidates"][1], party=None)]
    assert results_errs(candidates=everyone) == []


def test_an_outcome_waits_for_the_canvass():
    assert any("canvass" in e for e in results_errs(status="unofficial", outcome="Elected: Alex Example"))
    assert results_errs(outcome="Elected: Alex Example") == []


def test_a_vote_needs_yea_and_nay_as_whole_numbers():
    bad = post(slides=[{"head": "Tally", "vote": {"yea": 3}}, {"head": "Next"}])
    assert any("yea and nay" in e for e in errs(bad))


def test_found_via_is_a_news_link_or_an_idea_id_never_the_owners_words():
    review = dict(approval="review")
    assert errs(post(found_via="owner idea m_abc123", **review)) == []
    assert errs(post(found_via="https://news.example.com/story", **review)) == []
    assert any("never the owner's note" in e for e in errs(post(found_via="saw this reel, thoughts?", **review)))
    assert any("social media link" in e for e in errs(post(found_via="https://www.instagram.com/reel/x/", **review)))
    assert any("review" in e for e in errs(post(found_via="owner idea m_abc123")))


def test_reel_fields_belong_to_reels():
    p = post(slides=[{"head": "Hi", "say": "Hello."}, {"head": "Next"}])
    assert any("are for Reels" in e for e in errs(p))


def test_the_caption_fits_instagram():
    assert any("2,200" in e for e in errs(post(caption="x" * 2200)))


def test_record_ids_never_repeat(tmp_path):
    first = tmp_path / "2026-11-14-a.yaml"
    second = tmp_path / "2026-11-15-b.yaml"
    import yaml
    for f in (first, second):
        f.write_text(yaml.safe_dump(post(record_ids=["us/119/2/house/roll/316"])), encoding="utf-8")
    assert any("already in 2026-11-14-a.yaml" in e for e in B.duplicates(second, yaml.safe_load(second.read_text())))


def test_a_band_slide_with_nothing_for_the_band_uses_the_card():
    page = B.slide_html(post(), {"head": "Five Texas bills would repeal a tax break."}, 1, 3, "band", (1080, 1350))
    assert 'class="layout-card"' in page and 'class="lead"' not in page
    page = B.slide_html(post(), {"kicker": "Filing", "big": "1,457", "head": "Bills filed."}, 1, 3, "band", (1080, 1350))
    assert 'class="layout-band"' in page
