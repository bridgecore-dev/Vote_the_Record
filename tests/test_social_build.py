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


def story(**kw):
    p = post(kind="story", approval="review", publish_after="2026-11-16T08:00:00-06:00",
             slides=[{"kicker": "Countdown", "countdown": {"date": "2027-01-12", "event": "the Legislature convenes"}}])
    p.update(kw)
    return p


def test_a_countdown_counts_from_the_day_the_post_goes_out():
    s = B.expanded(story())[0]
    assert s["big"] == "57" and s["head"] == "days until the Legislature convenes"
    assert errs(story()) == []


def test_a_countdown_needs_a_publish_date_and_a_future_event():
    assert any("publish_after" in e for e in errs(story(publish_after=None)))
    past = [{"countdown": {"date": "2026-11-01", "event": "x"}}]
    assert any("on or after" in e for e in errs(story(slides=past)))


QUIZ = {"head": "When does the session start?",
        "quiz": {"choices": ["Nov. 9", "Jan. 12"], "answer": 1, "explain": "It starts Jan. 12."}}


def test_a_quiz_becomes_a_question_and_its_answer():
    q, a = B.expanded(story(slides=[QUIZ]))
    assert q["rows"] == [["A", "Nov. 9"], ["B", "Jan. 12"]] and q["hint"] == "Tap for the answer →"
    assert a["head"] == "B. Jan. 12" and a["text"] == "It starts Jan. 12."
    assert B.expanded(story(kind="carousel", slides=[QUIZ]))[0]["hint"] == "Swipe for the answer →"


def test_a_quiz_needs_review_a_valid_answer_and_room_for_it():
    assert any("approval review" in e for e in errs(story(slides=[QUIZ], approval="auto")))
    bad = {**QUIZ, "quiz": {**QUIZ["quiz"], "answer": 5}}
    assert any("quiz is" in e for e in errs(story(slides=[bad])))
    assert any("story or carousel" in e for e in errs(story(kind="image", slides=[QUIZ])))


def contrast(a: str, b: str) -> float:
    def lum(h):
        r, g, b_ = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b_)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_every_palette_is_readable_in_both_modes():
    for name, modes in B.PALETTES.items():
        for mode, c in modes.items():
            where = f"{name}/{mode}"
            assert contrast(c["ink"], c["bg"]) >= 7, where          # body text
            assert contrast(c["muted"], c["surface"]) >= 4.5, where  # small grey text
            assert contrast(c["on_accent"], c["accent"]) >= 4.5, where  # text on the accent band
            assert contrast(c["accent"], c["surface"]) >= 3, where   # big accent numbers and words


def test_no_party_colored_palette():
    assert not {"red", "blue"} & set(B.PALETTES)


def test_design_choices_are_checked():
    good = post(kind="reel", approval="review", design={"palette": "plum", "mode": "dark", "font": "sans",
                                                       "position": "bottom", "motion": "captions", "sfx": True})
    assert B.check_design(good, ("captions", "classic")) == []
    bad = post(design={"palette": "red", "mode": "neon", "sfx": "yes", "motion": "captions"}, trial="auto")
    e = " ".join(B.check_design(bad, ("captions",)))
    assert "palette" in e and "mode" in e and "sfx" in e and "for Reels" in e and "trial is for Reels" in e


def test_a_design_sets_its_colors_and_position():
    css = B.design_css({"palette": "amber", "mode": "dark", "position": "bottom"})
    assert B.PALETTES["amber"]["dark"]["accent"] in css and "--text-align: flex-end" in css
    assert B.design_of({}) == B.DEFAULT_DESIGN


def test_an_ai_image_is_disclosed_and_reviewed():
    p = post(slides=[{"head": "A", "ai_image": True}, {"head": "B"}])
    e = " ".join(errs(p))
    assert "AI-generated illustration" in e and "approval review" in e
    ok = dict(p, approval="review", caption="Our explainer.\n\nAI-generated illustration.")
    assert errs(ok) == []
    marked = post(kind="reel", approval="review", slides=[{"head": "A", "image": {"ai": True}}, {"head": "B"}])
    assert B.uses_ai_image(marked)


def portrait(name, bioguide, **kw):
    return {"head": name, "image": {"subject": "member", "name": name, "bioguide": bioguide, "license": "Public domain",
                                    "url": "https://media.example.org/k.jpg", "title": "Official portrait", "retrieved": "2026-11-19", **kw}}


def test_member_portraits_name_the_member_and_go_in_alphabetical_order():
    ok = post(slides=[portrait("Jane Adams", "A000001"), portrait("Sam Baker", "B000002")])
    assert B.check_portraits(ok) == []
    out_of_order = post(slides=[portrait("Sam Baker", "B000002"), portrait("Jane Adams", "A000001")])
    assert any("alphabetical" in e for e in B.check_portraits(out_of_order))
    bad = post(slides=[portrait("Jane Adams", "12", license="CC BY", ai=True), {"head": "x"}])
    e = " ".join(B.check_portraits(bad))
    assert "bioguide" in e and "public domain" in e and "never AI" in e
