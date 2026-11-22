"""The motion Reel renderer (src/social/reel.py): layouts, timing, the voice/doc/broll/image rules, and a tiny render.

No network and no voice model: speak() is replaced by a sine tone and fetch() by saved fixtures.
tests/fixtures/social/reel/hb167-introduced-excerpt.htm is an excerpt of HB 167 (90R) as introduced, from Texas
Legislature Online (capitol.texas.gov/tlodocs/90R/billtext/html/HB00167I.htm), retrieved 2026-11-14.
"""
import json
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/social"))
import reel  # noqa: E402

B = reel.B
FIX = Path(__file__).resolve().parent / "fixtures/social/reel"
BILL_URL = "https://capitol.texas.gov/tlodocs/90R/billtext/html/HB00167I.htm"
REPEAL = "The following provisions of the Tax Code are repealed: (1) Section 151.359; (2) Section 151.3595; and (3) Section 313.009."


def bill(**kw):
    return {"id": "HB 167", "does": "Would repeal data center sales tax breaks.", "affects": "Certified data centers.",
            "where": "Filed Nov. 9, 2026.", "summary": {"text": REPEAL, "title": "HB 167 as filed, Sec. 4", "url": BILL_URL,
                                                        "retrieved": "2026-11-14"}, **kw}


def post(slides, **kw):
    return {"kind": "reel", "level": "state", "place": "Texas", "approval": "review", "caption": "Text.\n\n" + reel.DISCLOSURE,
            "sources": [{"title": "TLO", "url": BILL_URL, "retrieved": "2026-11-14"}], "slides": slides, **kw}


# ---------------------------------------------------------------- layouts and acts

@pytest.mark.parametrize("slide, want", [
    ({"head": "Plain words."}, "plain"),
    ({"big": "5 bills", "head": "x", "rows": ["a"]}, "big"),
    ({"head": "x", "rows": [["HB 1", "Rep. A"], "b"]}, "rows"),
    ({"head": "x", "quote": {"label": "Caption", "text": "Relating to it."}}, "quote"),
    ({"head": "x", "vote": {"yea": 3, "nay": 1}}, "vote"),
    ({"results": {"contest": "Judge", "status": "official", "as_of": "Nov. 13", "candidates": [{"name": "A", "votes": 1}]}}, "results"),
    ({"head": "x", "doc": {"url": BILL_URL, "highlight": REPEAL}}, "doc"),
])
def test_layout_follows_fields(slide, want):
    assert reel.layout(slide) == want


def test_layout_knows_a_bill_explainer():
    p = post([{"head": "HB 167 was filed."}], bills=[bill()])
    slides = B.expanded(p)
    assert [reel.layout(s, p) for s in slides] == ["plain", "explainer"]


def test_acts_contrast_and_never_repeat_back_to_back():
    assert reel.act({"big": "5"}, "big", 0, None) == "teal"  # the hook
    before = reel.act({"chip": "The break today"}, "big", 1, "teal")
    after = reel.act({"chip": "If one passes"}, "big", 2, before)
    assert (before, after) == ("mint", "sand")
    assert reel.act({"head": "x", "doc": {}}, "doc", 3, "paper") == "ink"
    prev = "teal"
    for k in range(1, 9):
        now = reel.act({"head": "x"}, "plain", k, prev)
        assert now != prev
        prev = now


@pytest.mark.parametrize("big, want", [
    ("5 bills", {"pre": "", "num": 5.0, "dec": 0, "comma": False, "suf": " bills"}),
    ("6.25%", {"pre": "", "num": 6.25, "dec": 2, "comma": False, "suf": "%"}),
    ("$1,200 billion", {"pre": "$", "num": 1200.0, "dec": 0, "comma": True, "suf": " billion"}),
    ("Sept. 1, 2027", None), ("HB 167", None), ("251-180", None),
])
def test_count_up_keeps_the_numbers_format(big, want):
    assert reel.countable(big) == want


# ---------------------------------------------------------------- timing

def test_scene_length_is_the_voice_plus_tail_else_seconds_else_default():
    slides = [{"head": "a"}, {"head": "b", "seconds": 6}, {"head": "c"}]
    assert reel.durations(slides, [3.0, None, None]) == [round(reel.VOICE_AT + 3.0 + reel.TAIL, 3), 6.0, B.SECONDS]


def test_a_voiced_scene_stays_long_enough_for_what_builds_in():
    s = {"head": "x", "rows": ["a", "b", "c", "d", "e"]}
    assert reel.durations([s], [0.8], ["rows"]) == [round(reel.least(s, "rows"), 3)]


def test_caption_phrases_are_short():
    out = reel.phrases("Today, a certified data center doesn't pay the 6.25 percent state sales tax on items like electricity, servers, cooling and software.")
    assert all(len(p.split()) <= 6 for p in out)
    assert " ".join(out) == "Today, a certified data center doesn't pay the 6.25 percent state sales tax on items like electricity, servers, cooling and software."


# ---------------------------------------------------------------- check()

def test_check_passes_a_good_voiced_post():
    p = post([{"big": "5 bills", "head": "Five bills.", "say": "Five bills were filed."},
              {"chip": "In the bill", "head": "HB 167, Section 4.", "say": "HB 167, Section 4.",
               "doc": {"url": BILL_URL, "highlight": REPEAL, "title": "HB 167 as filed", "retrieved": "2026-11-14"}}],
             bills=[bill(say="HB 167 would repeal the breaks.")], voice={"name": "af_heart", "speed": 1.05})
    assert reel.check(p) == []


def test_check_rules_for_say():
    long = " ".join(["word"] * 46)
    p = post([{"head": "Five bills.", "say": "SB 9 would change it."}, {"head": "x", "say": long},
              {"head": "Filed in 2026.", "say": "Filed in 2025."}])
    errs = reel.check(p)
    assert any("names SB 9" in e for e in errs)
    assert any("46 words" in e for e in errs)
    assert any("number 2025" in e for e in errs)
    p["caption"] = "No disclosure here."
    assert any(reel.DISCLOSURE in e for e in reel.check(p))
    assert reel.check(post([{"head": "x"}], caption="No voice, no disclosure needed.")) == []


def test_check_rules_for_voice_and_quote_key():
    errs = reel.check(post([{"head": "x", "quote": {"text": "Relating to taxes.", "key": "relating to"}}],
                           voice={"name": "Heart!", "speed": 3}))
    assert any("quote key" in e for e in errs)
    assert any("voice name" in e for e in errs)
    assert any("voice speed" in e for e in errs)


def test_check_rules_for_doc_broll_image():
    errs = reel.check(post([
        {"head": "x", "doc": {"url": "https://example.com/bill.pdf", "highlight": "x", "title": "t", "retrieved": "2026-11-14"}},
        {"head": "x", "broll": {"url": "https://www.youtube.com/watch?v=1", "start": 0, "end": 30, "title": "t", "retrieved": "d"}},
        {"head": "x", "image": {"url": "https://tspb.texas.gov/capitol.jpg", "title": "t", "license": "CC BY",
                                "subject": "person", "retrieved": "d"}},
        {"head": "x", "rows": ["a"], "doc": {"url": BILL_URL, "highlight": "x", "title": "t", "retrieved": "d"}},
    ]))
    assert any("slide 1: doc url must be on an official site" in e for e in errs)
    assert any("slide 2: broll url: YouTube" in e for e in errs)
    assert any("slide 2: broll runs 2-20 seconds" in e for e in errs)
    assert any("slide 3: image license" in e for e in errs)
    assert any("slide 3: image subject" in e for e in errs)
    assert any("slide 4: a doc slide shows the document; move rows" in e for e in errs)
    assert reel.check_broll({"url": "https://house.texas.gov/video/a.mp4", "start": "0:10", "end": "0:16",
                             "title": "t", "retrieved": "d"}) == []


def test_extra_sources_lists_docs_and_media_once():
    p = post([{"head": "x", "doc": {"url": BILL_URL, "title": "Bill", "retrieved": "d", "highlight": "x"}},
              {"head": "y", "doc": {"url": BILL_URL, "title": "Bill", "retrieved": "d", "highlight": "y"}},
              {"head": "z", "broll": {"url": "https://h.gov/v.mp4", "page": "https://h.gov/hearing", "title": "Hearing", "retrieved": "d"}}])
    assert [s["url"] for s in reel.extra_sources(p)] == [BILL_URL, "https://h.gov/hearing"]


# ---------------------------------------------------------------- docs: exact words or nothing

def stub_fetch(monkeypatch, body: bytes, ctype: str):
    calls = []

    def fake(url, cache=None):
        calls.append(url)
        return body, ctype
    monkeypatch.setattr(reel, "fetch", fake)
    return calls


def test_html_highlight_not_found_raises(monkeypatch, tmp_path):
    stub_fetch(monkeypatch, (FIX / "hb167-introduced-excerpt.htm").read_bytes(), "text/html")
    d = {"url": BILL_URL, "highlight": "The following provisions of the Tax Code are hereby repealed:", "title": "t"}
    with pytest.raises(reel.HighlightNotFound):
        reel.prepare_doc(None, d, tmp_path, 1)  # stops before any browser work


def test_html_text_matches_across_table_cells():
    text = reel.html_text((FIX / "hb167-introduced-excerpt.htm").read_text(encoding="ascii"))
    assert REPEAL in text


def pdf_bytes(lines):
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    page = doc.new_page()
    for k, line in enumerate(lines):
        page.insert_text((30, 80 + 18 * k), f"{k + 1}", fontsize=10)  # a line number in the margin
        page.insert_text((120, 80 + 18 * k), line, fontsize=11)
    return doc.tobytes()


def test_pdf_highlight_found_across_lines_and_line_numbers(tmp_path):
    data = pdf_bytes(["SECTION 4. The following provisions of the Tax Code are", "repealed: (1) Section 151.359;"])
    info = reel.doc_pdf(data, "The following provisions of the Tax Code are repealed:", None, tmp_path / "doc.png")
    assert (tmp_path / "doc.png").exists() and info["page"] == 1 and info["hw"] > 0 and info["hh"] > 0


def test_pdf_highlight_not_found_raises(tmp_path):
    data = pdf_bytes(["SECTION 4. The following provisions of the Tax Code are", "repealed: (1) Section 151.359;"])
    with pytest.raises(reel.HighlightNotFound):
        reel.doc_pdf(data, "The following provisions are repealed:", None, tmp_path / "doc.png")
    with pytest.raises(reel.HighlightNotFound):
        reel.doc_pdf(data, "The following provisions of the Tax Code are repealed:", 2, tmp_path / "doc.png")


def test_fetch_stops_on_refusal_and_bot_check(monkeypatch):
    def refuse(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
    monkeypatch.setattr(reel.urllib.request, "urlopen", refuse)
    with pytest.raises(reel.DocError, match="HTTP 403"):
        reel.fetch(BILL_URL)

    class Page:
        headers = type("H", (), {"get_content_type": lambda self: "text/html"})()
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html><title>Just a moment...</title>Verify you are human</html>"
        def geturl(self): return BILL_URL
    monkeypatch.setattr(reel.urllib.request, "urlopen", lambda req, timeout=0: Page())
    with pytest.raises(reel.DocError, match="bot check"):
        reel.fetch(BILL_URL)
    with pytest.raises(reel.DocError, match="not an official host"):
        reel.fetch("https://example.com/a.pdf")


# ---------------------------------------------------------------- a tiny render

def sine(text, voice, speed):
    import numpy as np
    sr = 24000
    t = np.arange(int(sr * (0.25 + 0.05 * len(text.split())))) / sr
    return (0.2 * np.sin(2 * np.pi * 220 * t)).astype("float32"), sr


def probe(path: Path) -> dict:
    exe = shutil.which("ffprobe") or str(Path(B.FFMPEG).with_name("ffprobe.exe"))
    out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration:stream=codec_type,codec_name,width,height,pix_fmt",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_tiny_render(monkeypatch, tmp_path):
    pytest.importorskip("playwright")
    pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    if not (shutil.which("ffmpeg") or Path(B.FFMPEG).exists()):
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(reel, "speak", sine)
    stub_fetch(monkeypatch, (FIX / "hb167-introduced-excerpt.htm").read_bytes(), "text/html")
    p = post([{"big": "5 bills", "head": "Five bills.", "say": "Five bills were filed."},
              {"chip": "In the bill", "head": "HB 167, Section 4.",
               "doc": {"url": BILL_URL, "highlight": REPEAL, "title": "HB 167 as filed", "retrieved": "2026-11-14"}}],
             bills=[])
    try:
        files = reel.render(p, p["slides"], tmp_path, fps=5, preset="ultrafast", stills=[2])
    except Exception as e:  # no Chromium or Chrome to drive
        if "Executable doesn't exist" in str(e) or "chrome" in str(e).lower() and "not found" in str(e).lower():
            pytest.skip(f"no browser: {e}")
        raise
    assert files[0]["file"] == "reel.mp4" and files[0]["cover"] == "cover.png"
    assert 'Highlighted words: "The following provisions' in files[0]["alt_text"]
    info = probe(tmp_path / "reel.mp4")
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"], a["codec_name"]) == ("h264", 1080, 1920, "yuv420p", "aac")
    want = reel.TIMING["video_seconds"]  # voiced slide + the doc slide's default seconds
    assert want > B.SECONDS
    assert abs(float(info["format"]["duration"]) - want) < 0.5
    assert (tmp_path / "cover.png").exists() and (tmp_path / "still-02.png").exists()
    assert (tmp_path / "voice.txt").read_text(encoding="utf-8").strip() == "Slide 1: Five bills were filed."
    assert not list(tmp_path.glob("*.html")) and not (tmp_path / "doc-02.png").exists()  # working files are cleaned up


def test_reel_covers_at_most_two_bills():
    bill = {"id": "HB 1", "does": "x", "affects": "y", "status": "z"}
    post = {"kind": "reel", "publish_after": "2026-11-16T07:30:00-06:00", "slides": [{"head": "a"}],
            "bills": [dict(bill, id=f"HB {n}") for n in (1, 2, 3)]}
    assert any("at most 2 bills" in e for e in reel.check(post))
    post["bills"] = post["bills"][:2]
    assert not any("at most 2 bills" in e for e in reel.check(post))
    old = dict(post, publish_after="2026-11-14T07:30:00-06:00", bills=[dict(bill, id=f"HB {n}") for n in range(1, 6)])
    assert not any("at most 2 bills" in e for e in reel.check(old))  # posts scheduled before the rule keep their bills
    carousel = dict(post, kind="carousel", bills=old["bills"])
    assert not any("at most 2 bills" in e for e in reel.check(carousel))


@pytest.mark.parametrize("said, heard", [
    ("in place since 1982.", "in place since nineteen eighty-two."),
    ("the FLSA of 1938", "the FLSA of nineteen thirty-eight"),
    ("in 1900 and 1905", "in nineteen hundred and nineteen oh five"),
    ("Filed in 2005, again in 2026.", "Filed in two thousand five, again in twenty twenty-six."),
    ("deficits, 2026-2035", "deficits, twenty twenty-six to twenty thirty-five"),
    ("It costs $1.", "It costs one dollar."),
    ("under $500,000 in penalties", "under five hundred thousand dollars in penalties"),
    ("below $206 million.", "below two hundred six million dollars."),
    ("$1.50 each", "one dollar and fifty cents each"),
    ("$2.5 billion", "two point five billion dollars"),
    ("H.R. 1982 passed 19 to 16 after 40 hours", "House Bill 1982 passed 19 to 16 after 40 hours"),
    ("remove U.S. forces from the U.S. Senate", "remove US forces from the US Senate"),
    ("It stays in the U.S.", "It stays in the US."),
    ("the U.S., and Washington, D.C., too", "the US, and Washington, DC, too"),
    ("at 6 p.m. Friday", "at 6 PM Friday"),
    ("reported to the FEC and the VA", "reported to the F E C and the V A"),
    ("Sen. Smith on Nov. 19", "Sen. Smith on Nov. 19"),
    ("S. 3321 passed the Senate.", "Senate Bill 3321 passed the Senate."),
    ("H.R. 973 and S. 1135", "House Bill 973 and Senate Bill 1135"),
    ("H.Res. 1655 and SB 31/HB 101", "House Resolution 1655 and Senate Bill 31/House Bill 101"),
    ("SJR 16 and S.J.Res. 5", "Senate Joint Resolution 16 and Senate Joint Resolution 5"),
    ("Go to us.gov.", "Go to U S dot gov."),
    ("See Congress.gov, then clerk.house.gov/Votes.", "See Congress dot gov, then clerk dot house dot gov slash Votes."),
    ("File at ic3.gov or irs.gov", "File at I C 3 dot gov or I R S dot gov"),
    ("https://www.fec.gov/data.", "F E C dot gov slash data."),
    ("nyc.gov/workrules", "N Y C dot gov slash work rules"),
    ("The U.S. Senate at 6 p.m.", "The US Senate at 6 PM."),
])
def test_spoken_numbers(said, heard):
    assert reel.spoken(said) == heard


def test_senate_bill_number_stays_in_its_sentence():
    assert reel.sentences("S. 1135 passed. It goes on.") == ["S. 1135 passed.", "It goes on."]


def test_every_reel_has_a_voice():
    post = {"kind": "reel", "publish_after": "2026-11-16T07:30:00-06:00",
            "slides": [{"head": "a", "say": "a"}, {"head": "b"}], "bills": [{"id": "HB 1", "does": "x"}],
            "caption": "Hi.\n\nVoice: computer-generated (Kokoro text-to-speech)."}
    assert any("add say to slide 2, bill HB 1" in e for e in reel.check(post))
    old = dict(post, publish_after="2026-11-14T07:30:00-06:00")
    assert not any("every Reel has a voice" in e for e in reel.check(old))  # published before the rule


def test_one_bill_per_reel_except_twins():
    hb = {"id": "HB 774", "does": "Caps gifts."}
    sb = {"id": "SB 373", "does": "Same text as HB 774; caps gifts."}
    post = {"kind": "reel", "publish_after": "2026-11-18T07:30:00-06:00", "slides": [{"head": "a"}],
            "bills": [hb, {"id": "SB 9", "does": "Other."}]}
    assert any("a Reel covers one bill" in e for e in reel.check(post))
    assert not any("a Reel covers one bill" in e for e in reel.check(dict(post, bills=[hb, sb])))
    assert not any("a Reel covers one bill" in e for e in reel.check(dict(post, publish_after="2026-11-15T19:00:00-06:00")))


@pytest.mark.parametrize("say, ok", [
    ("The United States House passed it.", True), ("The U.S. Senate votes next.", True),
    ("The Texas House and the Texas Senate", True), ("A White House statement.", True),
    ("The House passed it.", False), ("Next: the Senate.", False), ("No chamber here.", True),
])
def test_reels_name_the_chamber(say, ok):
    post = {"kind": "reel", "publish_after": "2026-11-19T07:30:00-06:00", "slides": [{"head": say, "say": say}],
            "caption": "x\n\nVoice: computer-generated (Kokoro text-to-speech)."}
    assert (not any("with its name" in e for e in reel.check(post))) == ok


def test_abbreviations_do_not_end_a_sentence():
    assert reel.sentences("The U.S. House passed it on Nov. 11. Rep. Bost spoke. Next: the Senate.") == [
        "The U.S. House passed it on Nov. 11.", "Rep. Bost spoke.", "Next: the Senate."]


@pytest.mark.parametrize("say, ok", [
    ("House Resolution 1655 would impeach him.", True), ("H.Res. 1655 would impeach him.", False),
    ("H. Res. 1486 was tabled.", False), ("S.J.Res. 12 is next.", False), ("H.Con.Res. 3", False),
    ("Senate Joint Resolution 12 is next.", True), ("The resolution passed.", True), ("Restaurants reopen.", True),
])
def test_the_voice_says_resolution_in_full(say, ok):
    post = {"kind": "reel", "publish_after": "2026-11-19T07:30:00-06:00", "slides": [{"head": say, "say": say}],
            "caption": "x\n\nVoice: computer-generated (Kokoro text-to-speech)."}
    assert (not any("in full" in e for e in reel.check(post))) == ok


def test_a_resolution_name_is_not_a_chamber():
    post = {"kind": "reel", "publish_after": "2026-11-19T07:30:00-06:00",
            "slides": [{"head": "H.Con.Res. 93", "say": "House Concurrent Resolution 93 passed the United States House."}],
            "caption": "x\n\nVoice: computer-generated (Kokoro text-to-speech)."}
    assert reel.check(post) == []


# ---------------------------------------------------------------- captions motion (design trials, 2026-11-18)

def no_whisper(words, samples, sr, guess):
    return guess


def test_motions_are_named_for_build_and_unbuilt_ones_are_refused():
    assert reel.MOTIONS[0] == "captions" and "classic" in reel.MOTIONS and set(reel.BUILT) <= set(reel.MOTIONS)
    ok = post([{"head": "x", "say": "Five bills."}], design={"motion": "captions", "sfx": True})
    assert not [e for e in reel.check(ok) if "motion" in e]
    assert any("isn't built yet" in e for e in reel.check(post([{"head": "x"}], design={"motion": "diagram"})))


def test_ai_images_are_generated_decoration_with_a_guarded_prompt():
    cap = {"motion": "captions", "sfx": True}
    p = post([{"head": "x", "image": {"url": "https://example.gov/a.png", "ai": True}}])
    assert any("goes in ai_image" in e for e in reel.check(p))
    ok = post([{"head": "x", "say": "Hills.", "ai_image": {"prompt": "Soft rolling hills under a calm morning sky"}}], design=cap)
    assert not [e for e in reel.check(ok) if "ai_image" in e]
    assert any("needs design motion captions" in e for e in reel.check(
        post([{"head": "x", "ai_image": {"prompt": "Soft rolling hills under a calm morning sky"}}])))
    for bad, why in [("A crowd waving outside at dusk", "crowd"), ("Gentle light over the Capitol dome", "Capitol"),
                     ("Three fields and 4 barns at dawn", "no numbers"), ("A quiet harbor near Boston", "proper names"),
                     ("A newspaper on a kitchen table", "newspaper")]:
        assert any(why in e for e in reel.check_ai_image({"prompt": bad})), bad
    assert reel.check_ai_image({"file": "a.png"})


def test_ai_images_come_from_workers_ai_once_then_from_the_cache(monkeypatch, tmp_path):
    import base64 as b64
    calls = []

    class Resp:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self.body

    def fake(req, timeout=0):
        calls.append((req.full_url, json.loads(req.data), req.headers.get("Authorization")))
        return Resp(json.dumps({"success": True, "result": {"image": b64.b64encode(b"JPEGDATA").decode()}}).encode())
    monkeypatch.setattr(reel.urllib.request, "urlopen", fake)
    monkeypatch.setenv("CF_AI_TOKEN", "tok")
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct")
    p = post([{"head": "x"}], design={"palette": "amber", "mode": "dark", "motion": "captions"})
    prompt, seed = reel.ai_prompt({"prompt": "Calm wheat fields at dusk"}, p)
    assert prompt.startswith("Calm wheat fields at dusk. ") and "No people" in prompt and "#f5b83d" in prompt
    assert seed == reel.ai_prompt({"prompt": "Calm wheat fields at dusk"}, p)[1]  # same scene, same seed
    assert reel.generate_ai(prompt, seed, tmp_path) == b"JPEGDATA" == reel.generate_ai(prompt, seed, tmp_path)
    assert len(calls) == 1 and calls[0][0].endswith("/accounts/acct/ai/run/" + reel.AI_MODEL)
    assert calls[0][1]["seed"] == seed and calls[0][2] == "Bearer tok"
    monkeypatch.delenv("CF_AI_TOKEN")
    with pytest.raises(reel.DocError, match="CF_AI_TOKEN"):
        reel.generate_ai(prompt + " more", seed, tmp_path)


def test_word_times_share_the_reading_by_syllables_and_pin_commas_to_pauses(monkeypatch):
    np = pytest.importorskip("numpy")
    monkeypatch.setattr(reel, "heard", no_whisper)
    sr = 24000
    tone = lambda s: (0.2 * np.sin(2 * np.pi * 220 * np.arange(int(sr * s)) / sr)).astype("float32")
    a = np.concatenate([tone(1.0), np.zeros(int(0.3 * sr), dtype="float32"), tone(1.0)])  # a breath after the comma
    ws = reel.word_times("Yea meant send, the other no.", a, sr)
    assert [w["w"] for w in ws] == ["Yea", "meant", "send,", "the", "other", "no."]
    assert ws[0]["a"] == 0 and abs(ws[3]["a"] - 1.3) < 0.03  # "the" starts when the voice comes back
    assert all(x["a"] <= y["a"] for x, y in zip(ws, ws[1:])) and abs(ws[-1]["b"] - 2.3) < 0.01


def test_heard_keeps_our_words_and_order(monkeypatch):
    guess = [{"w": w, "a": t, "b": t + .3} for w, t in (("211", 0.0), ("to", 0.3), ("204.", 0.6))]

    class Word:
        def __init__(self, word, start):
            self.word, self.start = word, start

    class Model:
        def transcribe(self, *a, **k):
            return [type("S", (), {"words": [Word(" 211", 0.1), Word(" too", 0.5), Word(" 204.", 0.9)]})], None
    pytest.importorskip("faster_whisper")
    monkeypatch.setattr(reel, "_WHISPER", Model())
    out = reel.heard(["211", "to", "204."], [0.0] * 24000, 24000, guess)
    assert [w["w"] for w in out] == ["211", "to", "204."]  # the words shown are always ours
    assert out[0]["a"] == 0.1 and out[2]["a"] == 0.9 and out[0]["a"] <= out[1]["a"] <= out[2]["a"]


def test_caption_groups_are_short_and_break_at_pauses():
    ws = [{"w": w, "a": i, "b": i + 1} for i, w in enumerate("The United States House voted, 211 to 204, to send it.".split())]
    groups = [" ".join(w["w"] for w in g) for g in reel.chunks(ws)]
    assert groups == ["The United States", "House voted,", "211 to 204,", "to send it."]
    assert [reel.shown_word(w) for w in ("voted,", "it.", "U.S.", "Rep.", "204;")] == ["voted", "it", "U.S.", "Rep.", "204"]


def test_sound_effects_follow_what_lands_on_screen():
    w = lambda x, t, key=False: {"w": x, "a": t, "key": key}
    build = {"mode": "build", "hero": {"text": "12", "times": [0.05, 0.14]}, "rows": [],
             "groups": [[w("Five", 0.0), w("12", 0.4, True)]]}
    flash = {"mode": "chunks", "hero": None, "rows": [{"label": "Yea", "value": "x", "t": 0.5}],
             "groups": [[w("Send", 0.0), w("it", 0.2)]]}
    cues = reel.sfx_cues([0.0, 4.0], [build, flash])
    assert cues == [(0.05, "tick"), (0.1, "soft"), (0.14, "tick"), (0.5, "pop"), (4.0, "swoosh"), (4.1, "pop"), (4.6, "pop")]


def test_captions_scenes_take_turns_and_stress_the_right_words():
    ws = [{"w": x, "a": i * 0.3, "b": i * 0.3 + 0.3, "s": 0} for i, x in enumerate("Yea meant send it to the committee.".split())]
    p0 = reel.kinetic_plan({}, {"big": "211-204", "say": "x"}, "big", ws, 0)
    p1 = reel.kinetic_plan({}, {"chip": "What Yea and Nay meant", "say": "x"}, "plain", ws, 1)
    p2 = reel.kinetic_plan({}, {"head": "x", "rows": [["Yea", "Send it."], ["Nay", "Keep it."]], "say": "x"}, "rows", ws, 2)
    assert (p0["mode"], p0["align"], p0["tone"], p0["hero"]["text"]) == ("build", "center", "base", "211-204")
    assert len(p0["hero"]["times"]) == 7 and p0["hero"]["times"] == sorted(p0["hero"]["times"])
    assert (p1["mode"], p1["tone"]) == ("chunks", "accent") and p2["tone"] == "soft"
    words = {w["w"]: w for g in p1["groups"] for w in g}
    assert words["Yea"]["key"] and words["the"]["small"] and not words["send"]["key"]
    assert [r["t"] for r in p2["rows"]] == [0.0, 0.6]  # "Yea" is said at once; "Nay" isn't said, so it follows


def test_sentence_groups_keep_a_sentence_together():
    ws = [{"w": x, "a": i, "s": 0 if i < 3 else 1} for i, x in enumerate("One two three. Four five.".split())]
    assert [[w["w"] for w in g] for g in reel.sentence_groups(ws)] == [["One", "two", "three."], ["Four", "five."]]


def test_tiny_captions_render(monkeypatch, tmp_path):
    pytest.importorskip("playwright")
    pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    if not (shutil.which("ffmpeg") or Path(B.FFMPEG).exists()):
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(reel, "speak", sine)
    monkeypatch.setattr(reel, "heard", no_whisper)
    p = post([{"big": "5 bills", "head": "Five bills.", "say": "Five bills were filed, all on taxes."},
              {"chip": "Next", "head": "Then.", "say": "The committee meets next."}], bills=[],
             design={"palette": "plum", "mode": "dark", "font": "sans", "motion": "captions", "sfx": True})
    try:
        files = reel.render(p, p["slides"], tmp_path, fps=5, preset="ultrafast", stills=[1, 2])
    except Exception as e:
        if "Executable doesn't exist" in str(e) or "chrome" in str(e).lower() and "not found" in str(e).lower():
            pytest.skip(f"no browser: {e}")
        raise
    assert files[0]["file"] == "reel.mp4" and "Narration: Five bills were filed" in files[0]["alt_text"]
    assert (tmp_path / "still-02.png").exists()


def test_tiny_captions_render_with_an_ai_illustration(monkeypatch, tmp_path):
    pytest.importorskip("playwright")
    pytest.importorskip("numpy")
    pytest.importorskip("soundfile")
    if not (shutil.which("ffmpeg") or Path(B.FFMPEG).exists()):
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(reel, "speak", sine)
    monkeypatch.setattr(reel, "heard", no_whisper)
    png = __import__("base64").b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
    monkeypatch.setattr(reel, "generate_ai", lambda prompt, seed, cache=None: png)
    p = post([{"chip": "Water", "head": "Rivers.", "say": "The bill covers rivers.",
               "ai_image": {"prompt": "Calm river bend at dawn, soft light"}}], bills=[],
             design={"palette": "teal", "mode": "dark", "font": "sans", "motion": "captions", "sfx": True})
    try:
        reel.render(p, p["slides"], tmp_path, fps=5, preset="ultrafast", stills=[1])
    except Exception as e:
        if "Executable doesn't exist" in str(e) or "chrome" in str(e).lower() and "not found" in str(e).lower():
            pytest.skip(f"no browser: {e}")
        raise
    assert (tmp_path / "still-01.png").exists()


def test_seat_chart_fills_left_to_right_in_record_order():
    svg = reel.seats_svg({"yea": 401, "nay": 0, "not_voting": 32})
    assert svg.count("<rect") == 433 and "s-nay" not in svg
    kinds = re.findall(r'class="st s-(\w+)"', svg)
    assert kinds == ["yea"] * 401 + ["not_voting"] * 32
    seats, size = reel.seat_layout(100)
    assert len(seats) == 100 and size > 20
    assert [a for _, _, a in seats] == sorted((a for _, _, a in seats), reverse=True)
