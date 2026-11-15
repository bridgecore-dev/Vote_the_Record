"""The motion Reel renderer (src/social/reel.py): layouts, timing, the voice/doc/broll/image rules, and a tiny render.

No network and no voice model: speak() is replaced by a sine tone and fetch() by saved fixtures.
tests/fixtures/social/reel/hb167-introduced-excerpt.htm is an excerpt of HB 167 (90R) as introduced, from Texas
Legislature Online (capitol.texas.gov/tlodocs/90R/billtext/html/HB00167I.htm), retrieved 2026-11-14.
"""
import json
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
