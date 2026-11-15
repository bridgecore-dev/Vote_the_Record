"""Instagram Reels with real motion, for src/social/build.py (kind: reel).

    python src/social/reel.py social/posts/<date>-<slug>.yaml --out DIR [--fps 30] [--stills 1,3,5] [--cache DIR]
    python src/social/reel.py --download-model      # fetch the Kokoro voice model into the cache once

Every slide becomes a scene in one HTML page. A small script in the page draws any moment of the Reel
(`seek(t)`); one headless Chrome session (Playwright) steps it frame by frame and pipes the screenshots to
ffmpeg. Nothing depends on wall-clock time, so the same post always renders the same video.

- Layouts follow a slide's fields (layout()): a `big` number counts up, `rows` build in one at a time, a `quote`
  types in with its `key` phrase highlighted, `results`/`vote` bars grow, a bill explainer has its own look,
  a `doc` slide zooms onto a highlighted line of an official document, plain text comes in line by line.
- Scenes cut hard, with a short punch-in; a thin progress bar runs across the top. Slide 1 is the hook and is
  drawn complete from the first frame.
- Voice (optional): a slide's `say` is read by Kokoro-82M (kokoro-onnx, run locally) and burned in as
  captions, phrase by phrase. A slide without `say` is silent; nothing is ever written for the voice.
- `doc` (official document with the exact words highlighted), `broll` (official video, muted, darkened) and
  `image` (an official public-domain photo of a place, building or document) come from official hosts only,
  one request each, and stop with a clear error if a site refuses or shows a bot check.

render(post, slides, out) writes out/reel.mp4 (1080x1920, H.264, yuv420p, AAC), out/cover.png and, with a
voice, out/voice.txt, and returns the manifest entry publish.py reads. check(post) holds the rules for the
fields above, for build.check() to call.
"""
import argparse
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import importlib.util

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
# src/social/build.py, loaded by path: "build" is also the site builder's name (same as recap.py)
_spec = importlib.util.spec_from_file_location("social_build", Path(__file__).resolve().parent / "build.py")
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)
import clip as C  # noqa: E402  (host_ok, blocked, seconds)

W, H, FPS = 1080, 1920, 30
VOICE_AT, TAIL = 0.1, 0.6  # the voice starts this long into its scene; the scene holds this long after it ends
GAP = 0.22  # pause between two sentences of one slide's voice
SAY_WORDS = 45
# Reels cover one or two bills, so they stay short (owner's decision, 2026-11-15). Posts scheduled before then keep
# what they had: the data center Reel published Nov. 14 explains five.
REEL_BILLS = 2
REEL_BILLS_FROM = "2026-11-15"
ONE_BILL_FROM = "2026-11-16"  # one bill per Reel, except twins (owner's decision, 2026-11-15)
CHAMBERS_FROM = "2026-11-19"  # the voice names the chamber in full (owner's request, 2026-11-15)
STATES = ("Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|"
          "Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|"
          "Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|"
          "North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|"
          "Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming")
CHAMBER = re.compile(r"\b(House|Senate)\b")
NAMED_CHAMBER = re.compile(rf"(?:\bUnited States|\bU\.S\.|\b(?:{STATES})(?:'s)?|\bWhite) $")
VOICED_FROM = "2026-11-15"  # every Reel has a voice from here on (owner's request, 2026-11-15)
DISCLOSURE = "Voice: computer-generated (Kokoro text-to-speech)."
VOICE = {"name": "af_heart", "speed": 1.0}  # af_heart: Kokoro's clearest American English voice
VOICE_NAME = re.compile(r"[a-z]{2}_[a-z]+")
SPEED = (0.8, 1.25)
BROLL_SECONDS = (2, 20)
IMAGE_SUBJECTS = {"place", "building", "document"}  # no photos of individual people yet (social/README.md)
# what this module adds to the post format; build.check() should accept these (see the report / README)
SLIDE_FIELDS, TOP_FIELDS, BILL_FIELDS, QUOTE_FIELDS = {"say", "doc", "broll", "image"}, {"voice"}, {"say"}, {"key"}
DOC_KEYS, BROLL_KEYS = {"url", "highlight", "page", "title", "retrieved"}, {"url", "file", "page", "start", "end", "title", "retrieved"}
IMAGE_KEYS = {"url", "title", "license", "subject", "retrieved"}

MODEL_DIR = Path(os.environ.get("VTR_KOKORO_DIR") or Path.home() / ".cache/vtr-kokoro")
RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL_FILES = {  # the official kokoro-onnx release files, pinned by hash
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}
UA = "VoteTheRecord-reel/1.0 (official-records preview)"
BOT_CHECK = re.compile(r"captcha|cf-chl|just a moment\.\.\.|attention required|are you a robot|verify you are human|"
                       r"unusual traffic|access denied|request unsuccessful|incapsula", re.I)
MONTH = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s", re.I)
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
BEFORE = re.compile(r"\b(before|today|now|current(ly)?|existing|under current law)\b", re.I)
AFTER = re.compile(r"\b(after|if (it|one|this|they) pass(es)?|would|changes?|proposed)\b", re.I)
TIMING: dict = {}  # seconds spent in each step of the last render()


class DocError(RuntimeError):
    """An official document, photo or video couldn't be used: the site refused, or the text isn't there."""


class HighlightNotFound(DocError):
    """The exact highlight text isn't in the document. Never guessed, never altered."""


# ---------------------------------------------------------------- layouts, acts, timing

def explainer_bill(post: dict, s: dict) -> dict | None:
    """The bill whose explainer (build.explainer) this slide is, if it is one."""
    for b in post.get("bills") or []:
        try:
            if B.explainer(b) == s:
                return b
        except (KeyError, TypeError):
            continue
    return None


def layout(s: dict, post: dict | None = None) -> str:
    """Which motion a slide gets, from the fields it has."""
    if s.get("doc"):
        return "doc"
    if post is not None and explainer_bill(post, s):
        return "explainer"
    for f in ("results", "vote", "big", "quote", "rows"):
        if s.get(f):
            return f
    return "plain"


def act(s: dict, lay: str, k: int, prev: str | None) -> str:
    """The scene's background. Before/after chips contrast; neighbors differ; party never enters into it."""
    label = f"{s.get('chip') or ''} {s.get('kicker') or ''}"
    if k == 0:
        return "teal"
    if lay == "doc" or s.get("broll") or s.get("image"):
        return "ink"
    if lay == "explainer":  # the bill's own card, whatever its kicker says
        return "ink" if prev == "paper" else "paper"
    if BEFORE.search(label):
        return "mint"
    if AFTER.search(label):
        return "sand"
    if lay == "quote":
        return "mint" if prev == "paper" else "paper"
    order = ["paper", "teal", "ink", "mint"]
    pick = order[k % len(order)]
    return order[(k + 1) % len(order)] if pick == prev else pick


def least(s: dict, lay: str) -> float:
    """The shortest a voiced scene may be, so what builds in stays on screen long enough to read."""
    if lay == "rows":
        return 1.6 + 0.4 * len(s.get("rows") or [])
    if lay == "quote":
        return 2.5 + min(2.0, 0.028 * len(str((s.get("quote") or {}).get("text") or "")))
    if lay in ("results", "vote"):
        return 3.5
    return 2.5


def durations(slides: list[dict], voice_lengths: list[float | None], layouts: list[str] | None = None) -> list[float]:
    """Each scene: its voice plus TAIL (never shorter than least()), else its `seconds`, else build.SECONDS."""
    out = []
    for k, (s, v) in enumerate(zip(slides, voice_lengths)):
        if v:
            out.append(round(max(VOICE_AT + v + TAIL, least(s, layouts[k]) if layouts else 0), 3))
        else:
            out.append(float(s.get("seconds") or B.SECONDS))
    return out


def countable(big) -> dict | None:
    """How a `big` value counts up: prefix, number, decimals, suffix. None for dates, bill IDs, several numbers."""
    text = str(big)
    nums = NUMBER.findall(text)
    if len(nums) != 1 or MONTH.search(text + " ") or B.BILL.search(text):
        return None
    m = re.fullmatch(r"(.*?)(\d[\d,]*(?:\.\d+)?)(.*)", text, re.S)
    n = m[2]
    return {"pre": m[1], "num": float(n.replace(",", "")), "dec": len(n.split(".")[1]) if "." in n else 0,
            "comma": "," in n, "suf": m[3]}


def phrases(text: str, most: int = 6) -> list[str]:
    """A sentence cut into caption phrases of at most `most` words, at punctuation where it can."""
    parts = [p.strip() for p in re.split(r"(?<=[,;:—])\s+", text.strip()) if p.strip()]
    out = []
    for p in parts:
        words = p.split()
        while len(words) > most:
            k = len(words) // ((len(words) + most - 1) // most) or most
            out.append(" ".join(words[:k]))
            words = words[k:]
        if words:
            out.append(" ".join(words))
    return out


# a period after these is an abbreviation, not the end of a sentence ("U.S. House", "Rep. Bost", "Nov. 11")
ABBREV = "".join(rf"(?<!\b{a}\.)" for a in (r"U\.S", "Rep", "Sen", "Gov", "Mr", "Ms", "Dr", "St", r"H\.R", "No", "Jan", "Feb",
                                                "Mar", "Apr", "Aug", "Sept", "Oct", "Nov", "Dec"))


def sentences(text: str) -> list[str]:
    return [x for x in re.split(ABBREV + r"(?<=[.!?])\s+(?=[A-Z0-9\"“])", text.strip()) if x.strip()]


# ---------------------------------------------------------------- how numbers are read aloud
# Only the voice hears these words; captions keep the digits as written (owner's decision, 2026-11-15).
ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
SCALES = [(10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (1000, "thousand")]
SCALE_WORD = {"thousand": 1000, "million": 10**6, "billion": 10**9, "trillion": 10**12}


def words(n: int) -> str:
    """A whole number in words: 206 -> 'two hundred six', 500000 -> 'five hundred thousand'."""
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
    if n < 1000:
        return ONES[n // 100] + " hundred" + (" " + words(n % 100) if n % 100 else "")
    for size, name in SCALES:
        if n >= size:
            return words(n // size) + " " + name + (" " + words(n % size) if n % size else "")
    return str(n)


def year_words(y: int) -> str:
    """1982 -> 'nineteen eighty-two', 1900 -> 'nineteen hundred', 1905 -> 'nineteen oh five',
    2005 -> 'two thousand five', 2026 -> 'twenty twenty-six'."""
    if 2000 <= y <= 2009:
        return words(y)
    hi, lo = divmod(y, 100)
    if lo == 0:
        return words(hi) + " hundred"
    return words(hi) + " " + (("oh " + ONES[lo]) if lo < 10 else words(lo))


def _number(text: str) -> float:
    return float(text.replace(",", ""))


def _dollars(m: re.Match) -> str:
    amount, scale = _number(m[1]), (m[2] or "").lower()
    if scale:
        whole = words(int(amount))
        if "." in m[1]:
            whole += " point " + " ".join(ONES[int(d)] for d in m[1].split(".")[1])
        return f"{whole} {scale} dollars"
    dollars, cents = int(amount), round((amount - int(amount)) * 100)
    out = f"{words(dollars)} dollar{'' if dollars == 1 else 's'}"
    if cents:
        out += f" and {words(cents)} cent{'' if cents == 1 else 's'}"
    return out


DOLLARS = re.compile(r"\$(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s+(thousand|million|billion|trillion)\b)?", re.I)
YEAR = re.compile(r"(?<![\d$,.])(1[1-9]\d\d|20\d\d)(?!\d|[,.]\d)")
YEAR_RANGE = re.compile(r"(?<![\d$,.])(1[1-9]\d\d|20\d\d)[-–](1[1-9]\d\d|20\d\d)(?!\d|[,.]\d)")


def spoken(text: str) -> str:
    """What the voice reads: dollar amounts as words with 'dollars' at the end, years the way people say them.
    Bill numbers (H.R. 1982) are left as written."""
    keep = {}

    def hold(m):
        keep[f"\x00{len(keep)}\x00"] = m[0]
        return f"\x00{len(keep) - 1}\x00"
    text = B.BILL.sub(hold, text)
    text = DOLLARS.sub(_dollars, text)
    text = YEAR_RANGE.sub(lambda m: f"{year_words(int(m[1]))} to {year_words(int(m[2]))}", text)
    text = YEAR.sub(lambda m: year_words(int(m[1])), text)
    for k, v in keep.items():
        text = text.replace(k, v)
    return text


# ---------------------------------------------------------------- checks

def says(post: dict) -> list[tuple[str, str, str]]:
    """(where, say, the words that say restates) for every slide and bill that has one."""
    out = []
    for k, s in enumerate(post.get("slides") or [], 1):
        if "say" in s:
            out.append((f"slide {k}", s["say"], visible(s)))
    for b in post.get("bills") or []:
        if "say" in b:
            out.append((f"bill {b.get('id')}", b["say"], " ".join(str(b.get(f) or "") for f in ("id", "title", "does", "affects", "where"))
                        + " " + str((b.get("summary") or {}).get("text") or "")))
    return out


def twins(bills: list[dict]) -> bool:
    """Two bills with the same text (a House bill and its Senate companion), as the post's `does` says."""
    a, b = bills
    same = lambda x, y: str(x.get("does") or "").lower().startswith(f"same text as {str(y.get('id') or '').lower()}")
    return same(a, b) or same(b, a)


def visible(s: dict) -> str:
    """Every word a slide shows."""
    q = s.get("quote") or {}
    parts = [str(s.get(f) or "") for f in ("chip", "kicker", "big", "head", "text", "by")] + [str(q.get("text") or "")]
    parts += [f"{a} {b}" for a, b in B.rows(s)]
    r = s.get("results") or {}
    parts += [f'{c.get("name")} {c.get("votes")}' for c in r.get("candidates") or []] + [str(r.get(f) or "") for f in ("contest", "as_of", "outcome")]
    parts += [str(n) for n in (s.get("vote") or {}).values()]
    parts += [str((s.get("doc") or {}).get(f) or "") for f in ("highlight", "title")]
    return " ".join(parts)


def _https_official(url, what: str, video: bool = False) -> list[str]:
    errs = []
    if urlparse(str(url)).scheme != "https":
        errs.append(f"{what} must be https")
    if C.blocked(str(url)):
        errs.append(f"{what}: YouTube and C-SPAN are not used; take the official source")
    elif not (C.host_ok(str(url)) if video else B.official(str(url))):
        errs.append(f"{what} must be on an official site ({'.gov or a host in clip.CLIP_HOSTS' if video else '.gov, .us or .mil'})")
    return errs


def check(post: dict) -> list[str]:
    """Problems with a post's voice, doc, broll and image fields (for build.check)."""
    errs, slides = [], post.get("slides") or []
    used = bool(post.get("voice")) or any(s.get(f) for s in slides for f in SLIDE_FIELDS) or any("say" in b for b in post.get("bills") or [])
    if used and post.get("kind") != "reel":
        errs.append("voice, say, doc, broll and image are for kind: reel")
    bills = post.get("bills") or []
    if post.get("kind") == "reel" and len(bills) > REEL_BILLS and str(post.get("publish_after") or "")[:10] >= REEL_BILLS_FROM:
        errs.append(f"a Reel covers at most {REEL_BILLS} bills (this one has {len(bills)}); split the rest into another "
                    "Reel or a carousel")
    if post.get("kind") == "reel" and str(post.get("publish_after") or "")[:10] >= VOICED_FROM:
        silent = [f"slide {k}" for k, s in enumerate(slides, 1) if "say" not in s]
        silent += [f"bill {b.get('id')}" for b in bills if "say" not in b]
        if silent:
            errs.append("every Reel has a voice: add say to " + ", ".join(silent))
    when = str(post.get("publish_after") or "")[:10]
    if post.get("kind") == "reel" and len(bills) == 2 and when >= ONE_BILL_FROM and not twins(bills):
        errs.append("a Reel covers one bill; two only when they are twins with the same text (one bill's `does` "
                    "starts \"Same text as <the other's id>\"); give the other bill its own post")
    if post.get("kind") == "reel" and when >= CHAMBERS_FROM:
        for where, say, _ in says(post):
            m = CHAMBER.search(str(say))
            if m and not NAMED_CHAMBER.search(str(say)[:m.start()]):
                errs.append(f"{where}: say \"{m[0]}\" with its name, e.g. \"United States {m[0]}\" or \"Texas {m[0]}\"")
    v = post.get("voice")
    if v not in (None, True):
        if not isinstance(v, dict):
            errs.append("voice is true or {name, speed}")
        else:
            errs += [f"voice: unknown field {k}" for k in v if k not in VOICE]
            if "name" in v and not VOICE_NAME.fullmatch(str(v["name"])):
                errs.append(f"voice name {v['name']!r} isn't a Kokoro voice like af_heart")
            if "speed" in v and not (isinstance(v["speed"], (int, float)) and SPEED[0] <= v["speed"] <= SPEED[1]):
                errs.append(f"voice speed is a number from {SPEED[0]} to {SPEED[1]}")
    named = {B.bill_key(str(b.get("id", ""))) for b in post.get("bills") or []}
    spoken = says(post)
    for where, say, shown in spoken:
        if not (isinstance(say, str) and say.strip()):
            errs.append(f"{where}: say is the words to read aloud, as text")
            continue
        n = len(say.split())
        if n > SAY_WORDS:
            errs.append(f"{where}: say is {n} words; keep it to {SAY_WORDS}")
        for m in B.BILL.finditer(say):
            if B.bill_key(m[0]) not in named:
                errs.append(f"{where}: say names {m[0]} without saying what it does: add it to bills")
        have = {x.replace(",", "") for x in NUMBER.findall(shown)}
        for x in NUMBER.findall(say):
            if x.replace(",", "").rstrip(".") not in have:
                errs.append(f"{where}: say has the number {x}, which the slide doesn't show")
    if spoken and DISCLOSURE not in [line.strip() for line in str(post.get("caption") or "").splitlines()]:
        errs.append(f'a post with a voice has the caption line "{DISCLOSURE}"')
    for k, s in enumerate(slides, 1):
        q = s.get("quote") or {}
        if q.get("key") is not None and str(q["key"]) not in str(q.get("text") or ""):
            errs.append(f"slide {k}: quote key must be an exact part of the quote text")
        media = [f for f in ("doc", "broll", "image") if s.get(f)]
        if len(media) > 1:
            errs.append(f"slide {k}: one of doc, broll or image per slide")
        if s.get("doc"):
            errs += [f"slide {k}: {e}" for e in check_doc(s)]
        if s.get("broll"):
            errs += [f"slide {k}: {e}" for e in check_broll(s["broll"])]
        if s.get("image"):
            errs += [f"slide {k}: {e}" for e in check_image(s["image"])]
    return errs


def check_doc(s: dict) -> list[str]:
    d = s["doc"]
    if not isinstance(d, dict):
        return ["doc is {url, highlight, title, retrieved, page}"]
    errs = [f"doc: unknown field {k}" for k in d if k not in DOC_KEYS]
    errs += [f"doc needs {f}" for f in ("url", "highlight", "title", "retrieved") if not d.get(f)]
    if d.get("url"):
        errs += _https_official(d["url"], "doc url")
    if d.get("page") is not None and not (isinstance(d["page"], int) and d["page"] >= 1):
        errs.append("doc page is the PDF page number, from 1")
    if len(str(d.get("highlight") or "")) > 400:
        errs.append("doc highlight: quote at most 400 characters")
    if not s.get("head"):
        errs.append("a doc slide has a head saying what the document is, e.g. 'HB 167, Section 4, as filed.'")
    errs += [f"a doc slide shows the document; move {f} to another slide" for f in ("big", "rows", "results", "vote", "quote") if s.get(f)]
    return errs


def check_broll(b) -> list[str]:
    if not isinstance(b, dict):
        return ["broll is {url or file, start, end, title, retrieved}"]
    errs = [f"broll: unknown field {k}" for k in b if k not in BROLL_KEYS]
    if bool(b.get("url")) == bool(b.get("file")):
        errs.append("broll has a url or a file (one of them)")
    errs += [f"broll needs {f}" for f in ("title", "retrieved") if not b.get(f)]
    if b.get("url"):
        errs += _https_official(b["url"], "broll url", video=True)
    if b.get("file"):
        if Path(b["file"]).is_absolute() or ".." in Path(b["file"]).parts or not (B.ROOT / b["file"]).is_file():
            errs.append(f"broll file {b['file']} isn't in the repository (a path relative to its root)")
        if not b.get("page"):
            errs.append("a broll file names the official page it came from (page)")
    if b.get("page"):
        errs += _https_official(b["page"], "broll page", video=True)
    try:
        length = C.seconds(b.get("end")) - C.seconds(b.get("start"))
    except (TypeError, ValueError):
        return errs + ["broll start and end are seconds or H:MM:SS"]
    if not BROLL_SECONDS[0] <= length <= BROLL_SECONDS[1]:
        errs.append(f"broll runs {BROLL_SECONDS[0]}-{BROLL_SECONDS[1]} seconds, not {length:g}")
    return errs


def check_image(i) -> list[str]:
    if not isinstance(i, dict):
        return ["image is {url, title, license, subject, retrieved}"]
    errs = [f"image: unknown field {k}" for k in i if k not in IMAGE_KEYS]
    errs += [f"image needs {f}" for f in ("url", "title", "license", "subject", "retrieved") if not i.get(f)]
    if i.get("url"):
        errs += _https_official(i["url"], "image url")
    if i.get("license") and not re.search(r"public domain", str(i["license"]), re.I):
        errs.append("image license: official public-domain photos only (say 'public domain' and where the terms are stated)")
    if i.get("subject") and i["subject"] not in IMAGE_SUBJECTS:
        errs.append(f"image subject is one of {sorted(IMAGE_SUBJECTS)}: no photos of individual people yet")
    return errs


def extra_sources(post: dict) -> list[dict]:
    """Sources a Reel's doc, broll and image add to the caption, for build.all_sources() to append."""
    out, seen = [], set()
    for s in post.get("slides") or []:
        for f in ("doc", "image", "broll"):
            m = s.get(f) or {}
            url = m.get("page") or m.get("url") if f == "broll" else m.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append({"title": m.get("title"), "url": url, "retrieved": m.get("retrieved")})
    return out


# ---------------------------------------------------------------- fetching (one request per item)

def fetch(url: str, cache: Path | None = None) -> tuple[bytes, str]:
    """The file at an official URL and its content type, in one request; stops on refusals and bot checks."""
    if cache:
        key = cache / hashlib.sha256(url.encode()).hexdigest()
        if key.exists():
            meta = json.loads(key.with_suffix(".json").read_text())
            return key.read_bytes(), meta["type"]
    if C.blocked(url) or not (B.official(url) or C.host_ok(url)):
        raise DocError(f"{url}: not an official host")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data, ctype, final = r.read(), r.headers.get_content_type(), r.geturl()
    except urllib.error.HTTPError as e:
        raise DocError(f"{url}: the site answered HTTP {e.code}. Stopping: no retries, no workarounds.") from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise DocError(f"{url}: couldn't reach the site ({e}). Stopping.") from None
    if not (B.official(final) or C.host_ok(final)):
        raise DocError(f"{url}: redirected off the official site to {final}")
    if ctype in ("text/html", "application/xhtml+xml") and BOT_CHECK.search(data[:60000].decode("utf-8", "replace")):
        raise DocError(f"{url}: the site showed a bot check or refusal page. Stopping; open it by hand instead.")
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        key.write_bytes(data)
        key.with_suffix(".json").write_text(json.dumps({"url": url, "type": ctype}))
    return data, ctype


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style", "noscript")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "noscript") and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(doc: str) -> str:
    p = _Text()
    p.feed(doc)
    return norm("".join(p.parts))


def decode(data: bytes, ctype_header: str = "") -> str:
    m = re.search(rb'charset=["\']?([\w-]+)', data[:3000])
    for enc in filter(None, [m and m[1].decode(), "utf-8", "cp1252"]):
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", "replace")


FIND_JS = r"""(needle) => {
  const sp = c => /[\s ]/.test(c);
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let buf = '', prev = true; const map = [];
  for (let n; (n = walker.nextNode());) {
    const p = n.parentElement;
    if (p && ['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(p.tagName)) continue;
    for (let i = 0; i < n.data.length; i++) {
      const c = n.data[i];
      if (sp(c)) { if (prev) continue; buf += ' '; map.push([n, i]); prev = true; }
      else { buf += c; map.push([n, i]); prev = false; }
    }
  }
  const target = needle.replace(/[\s ]+/g, ' ').trim();
  const at = buf.indexOf(target);
  if (at < 0) return null;
  const segs = []; let cur = null;
  for (let j = at; j < at + target.length; j++) {
    const [n, i] = map[j];
    if (!cur || cur.n !== n) { if (cur) segs.push(cur); cur = {n, s: i, e: i + 1}; } else cur.e = i + 1;
  }
  segs.push(cur);
  const ranges = segs.filter(r => r.n.data.slice(r.s, r.e).trim()).map(r => {
    const g = new Range(); g.setStart(r.n, r.s); g.setEnd(r.n, r.e); return g; });
  CSS.highlights.set('vtr', new Highlight(...ranges));
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const g of ranges) for (const b of g.getClientRects()) {
    x0 = Math.min(x0, b.left); y0 = Math.min(y0, b.top); x1 = Math.max(x1, b.right); y1 = Math.max(y1, b.bottom); }
  const d = document.documentElement;
  return {x: x0 + scrollX, y: y0 + scrollY, w: x1 - x0, h: y1 - y0,
          pw: Math.max(d.scrollWidth, document.body.scrollWidth), ph: Math.max(d.scrollHeight, document.body.scrollHeight)};
}"""

DOC_DPR, DOC_MARGIN = 2, 300  # doc screenshots: device pixels per CSS pixel; CSS pixels kept above and below the highlight


def doc_html(browser, data: bytes, highlight: str, png: Path) -> dict:
    """Screenshot an official HTML page around the exact words, highlighted with a CSS highlight (text untouched)."""
    text = decode(data)
    if norm(highlight) not in html_text(text):
        raise HighlightNotFound(f"the exact words {highlight!r} aren't in the page. Copy them from the document; "
                                "the highlight is never guessed.")
    ctx = browser.new_context(viewport={"width": 760, "height": 1200}, device_scale_factor=DOC_DPR)
    page = ctx.new_page()
    page.route("**/*", lambda route: route.abort())  # the page's own request was the only one: no images, scripts, fonts
    page.set_content(text, wait_until="domcontentloaded")
    page.add_style_tag(content="::highlight(vtr) { background-color: #ffd84d; } html, body { background: #fff; }")
    r = page.evaluate(FIND_JS, highlight)
    if not r:
        ctx.close()
        raise HighlightNotFound(f"the exact words {highlight!r} weren't found on the rendered page")
    top = max(0, r["y"] - DOC_MARGIN)
    bottom = min(r["ph"], r["y"] + r["h"] + DOC_MARGIN)
    page.screenshot(path=str(png), full_page=True, clip={"x": 0, "y": top, "width": r["pw"], "height": bottom - top})
    ctx.close()
    d = DOC_DPR
    return {"img": png.name, "iw": round(r["pw"] * d), "ih": round((bottom - top) * d),
            "hx": r["x"] * d, "hy": (r["y"] - top) * d, "hw": r["w"] * d, "hh": r["h"] * d}


def pdf_find(pdf, highlight: str, page: int | None = None) -> tuple[int, list]:
    """The page (0-based) and one rectangle per line where the exact words appear. Line numbers in the margin
    between them are skipped (they aren't part of the text); nothing else is."""
    import pymupdf
    want = norm(highlight).split(" ")
    pages = [page - 1] if page else range(len(pdf))
    for pno in pages:
        if not 0 <= pno < len(pdf):
            raise HighlightNotFound(f"the PDF has no page {pno + 1}")
        pg = pdf[pno]
        words = pg.get_text("words", sort=True)
        margin = pg.rect.width * 0.16
        for start in range(len(words)):
            if words[start][4] != want[0]:
                continue
            got, j = [], start
            for w in want:
                while j < len(words) and words[j][4] != w and words[j][4].isdigit() and words[j][0] < margin:
                    j += 1
                if j >= len(words) or words[j][4] != w:
                    break
                got.append(words[j])
                j += 1
            if len(got) == len(want):
                lines: dict = {}
                for x0, y0, x1, y1, _, blk, ln, _ in got:
                    r = lines.setdefault((blk, ln), pymupdf.Rect(x0, y0, x1, y1))
                    r |= pymupdf.Rect(x0, y0, x1, y1)
                return pno, list(lines.values())
    raise HighlightNotFound(f"the exact words {highlight!r} aren't in the PDF{f' on page {page}' if page else ''}. "
                            "Copy them from the document; the highlight is never guessed.")


def doc_pdf(data: bytes, highlight: str, page: int | None, png: Path) -> dict:
    """Render the PDF page with a highlight over the exact words (an overlay; the text isn't changed)."""
    import pymupdf
    if not data.startswith(b"%PDF"):
        raise DocError("the doc url didn't return a PDF (a refusal or bot-check page?). Stopping.")
    pdf = pymupdf.open(stream=data, filetype="pdf")
    pno, rects = pdf_find(pdf, highlight, page)
    pg = pdf[pno]
    for r in rects:
        a = pg.add_highlight_annot(r)
        a.set_colors(stroke=(1, 0.85, 0.3))
        a.update()
    union = rects[0]
    for r in rects[1:]:
        union = union | r
    zoom = 2.4
    margin = 130
    clip = pymupdf.Rect(pg.rect.x0, max(pg.rect.y0, union.y0 - margin), pg.rect.x1, min(pg.rect.y1, union.y1 + margin))
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, annots=True)
    pix.save(str(png))
    return {"img": png.name, "iw": pix.width, "ih": pix.height, "page": pno + 1,
            "hx": (union.x0 - clip.x0) * zoom, "hy": (union.y0 - clip.y0) * zoom,
            "hw": union.width * zoom, "hh": union.height * zoom}


def prepare_doc(browser, d: dict, out: Path, k: int, cache: Path | None = None) -> dict:
    data, ctype = fetch(d["url"], cache)
    png = out / f"doc-{k:02d}.png"
    if ctype == "application/pdf" or data.startswith(b"%PDF"):
        return doc_pdf(data, d["highlight"], d.get("page"), png)
    if ctype not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise DocError(f"{d['url']}: expected an HTML page or a PDF, got {ctype}")
    return doc_html(browser, data, d["highlight"], png)


def prepare_image(i: dict, out: Path, k: int, cache: Path | None = None) -> dict:
    data, ctype = fetch(i["url"], cache)
    if not ctype.startswith("image/"):
        raise DocError(f"{i['url']}: expected an image, got {ctype} (a refusal page?). Stopping.")
    png = out / f"image-{k:02d}.{ctype.split('/')[1].replace('jpeg', 'jpg')}"
    png.write_bytes(data)
    return {"img": png.name}


def prepare_broll(b: dict, out: Path, k: int, fps: int) -> dict:
    """The span as JPEG frames, cover-cropped to the full frame; the sound is dropped."""
    src = str(B.ROOT / b["file"]) if b.get("file") else b["url"]
    if b.get("url") and (C.blocked(b["url"]) or not C.host_ok(b["url"])):
        raise DocError(f"{b['url']}: broll comes from official video only")
    start, end = C.seconds(b["start"]), C.seconds(b["end"])
    folder = out / f"broll-{k:02d}"
    folder.mkdir(exist_ok=True)
    run = subprocess.run([B.FFMPEG, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{end - start:.3f}", "-an",
                          "-vf", f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}",
                          "-q:v", "4", str(folder / "%05d.jpg")], capture_output=True, text=True)
    frames = sorted(folder.glob("*.jpg"))
    if run.returncode or not frames:
        raise DocError(f"broll {src}: ffmpeg couldn't read it ({run.stderr.strip()[-300:]}). Stopping.")
    return {"dir": folder.name, "n": len(frames)}


# ---------------------------------------------------------------- voice

_KOKORO = None


def ensure_model(folder: Path = MODEL_DIR) -> Path:
    """The Kokoro model and voices in the cache folder, downloaded once from the official release and hash-checked."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, sha in MODEL_FILES.items():
        f = folder / name
        if f.exists():
            continue
        part = f.with_suffix(f.suffix + ".part")
        for _ in range(200):  # resume after dropped connections
            have = part.stat().st_size if part.exists() else 0
            req = urllib.request.Request(RELEASE + name, headers={"User-Agent": UA, "Range": f"bytes={have}-"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r, open(part, "ab" if r.status == 206 else "wb") as w:
                    while chunk := r.read(1 << 20):
                        w.write(chunk)
                break
            except urllib.error.HTTPError as e:
                if e.code == 416:
                    break
                raise
            except (OSError, urllib.error.URLError):
                time.sleep(1)
        h = hashlib.sha256()
        with open(part, "rb") as r:
            while chunk := r.read(1 << 22):
                h.update(chunk)
        if h.hexdigest() != sha:
            part.unlink()
            sys.exit(f"{name}: downloaded file doesn't match the pinned sha256; not used")
        part.rename(f)
    return folder


def speak(text: str, voice: str, speed: float):
    """Kokoro's reading of one sentence: (mono float32 samples, sample rate). Tests replace this."""
    global _KOKORO
    import numpy as np
    if _KOKORO is None:
        from kokoro_onnx import Kokoro
        folder = ensure_model()
        _KOKORO = Kokoro(str(folder / "kokoro-v1.0.onnx"), str(folder / "voices-v1.0.bin"))
    samples, sr = _KOKORO.create(text, voice=voice, speed=speed, lang="en-us")
    return np.asarray(samples, dtype="float32"), sr


def trim(samples, sr: int, floor: float = 0.006, keep: float = 0.04):
    import numpy as np
    loud = np.nonzero(np.abs(samples) > floor)[0]
    if not len(loud):
        return samples[:0]
    pad = int(keep * sr)
    return samples[max(0, loud[0] - pad): loud[-1] + pad]


def voice_track(scripts: list[str | None], voice: dict):
    """Each slide's reading (or None), its length, and its caption phrases timed from the reading's start."""
    import numpy as np
    clips, lengths, caps, sr = [], [], [], 24000
    for say in scripts:
        if not say:
            clips.append(None), lengths.append(None), caps.append([])
            continue
        parts, timed, at = [], [], 0.0
        for sent in sentences(say):
            a, sr = speak(spoken(sent), voice["name"], float(voice["speed"]))
            a = trim(a, sr)
            dur = len(a) / sr
            ph = phrases(sent)
            total = sum(len(p) for p in ph) or 1
            t = at
            for p in ph:
                d = dur * len(p) / total
                timed.append({"a": round(t, 3), "b": round(t + d, 3), "text": p})
                t += d
            parts += [a, np.zeros(int(GAP * sr), dtype="float32")]
            at += dur + GAP
        a = np.concatenate(parts[:-1]) if parts else np.zeros(0, dtype="float32")
        clips.append(a), lengths.append(len(a) / sr), caps.append(timed)
    return clips, lengths, caps, sr


def mix(clips, starts: list[float], total: float, sr: int, out: Path) -> Path:
    """One track: each reading at its scene's start, the rest silence, normalized to -16 LUFS (two-pass loudnorm)."""
    import numpy as np
    import soundfile as sf
    track = np.zeros(int(total * sr) + sr, dtype="float32")
    for a, t in zip(clips, starts):
        if a is not None and len(a):
            i = int((t + VOICE_AT) * sr)
            seg = a[: max(0, len(track) - i)]
            track[i:i + len(seg)] += seg
    raw, done = out / "voice-raw.wav", out / "voice.wav"
    sf.write(raw, track[: int(total * sr)], sr)
    spec = "loudnorm=I=-16:TP=-1.5:LRA=11"
    probe = subprocess.run([B.FFMPEG, "-hide_banner", "-i", str(raw), "-af", spec + ":print_format=json", "-f", "null", "-"],
                           capture_output=True, text=True)
    m = json.loads(probe.stderr[probe.stderr.rindex("{"):probe.stderr.rindex("}") + 1])
    second = (f"{spec}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
              f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    subprocess.run([B.FFMPEG, "-y", "-loglevel", "error", "-i", str(raw), "-af", f"{second},aresample=48000", "-ac", "2",
                    "-c:a", "pcm_s16le", str(done)], check=True, capture_output=True)
    raw.unlink()
    return done


# ---------------------------------------------------------------- the page

CSS = """
body { padding: 0; display: block; background: #000; }
.scene { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; overflow: hidden; background: var(--bg); color: var(--fg); visibility: hidden; }
.act-teal  { --bg: #0f5c63; --fg: #fff;    --mu: #d3ebe8; --hi: #fff;    --ac: #a8e0da; --chip: rgba(255,255,255,.16); --chipfg: #fff;    --card: rgba(255,255,255,.10); --track: rgba(255,255,255,.18); }
.act-paper { --bg: #f2f5f4; --fg: #1b2a2f; --mu: #4d5e63; --hi: #0f5c63; --ac: #0f5c63; --chip: #dcebec; --chipfg: #0f5c63; --card: #fff;     --track: #dfe6e4; }
.act-mint  { --bg: #dcebec; --fg: #1b2a2f; --mu: #3f5257; --hi: #0f5c63; --ac: #0f5c63; --chip: #fff;    --chipfg: #0f5c63; --card: #fff;     --track: #c3d8d9; }
.act-sand  { --bg: #f4ead7; --fg: #1b2a2f; --mu: #5a5246; --hi: #8a4b12; --ac: #8a4b12; --chip: #fff;    --chipfg: #8a4b12; --card: #fffaf0;  --track: #e6d8bd; }
.act-ink   { --bg: #1b2a2f; --fg: #fff;    --mu: #b9c6c4; --hi: #fff;    --ac: #7fd1c7; --chip: rgba(255,255,255,.14); --chipfg: #fff;    --card: rgba(255,255,255,.08); --track: rgba(255,255,255,.18); }
.prog { position: absolute; left: 72px; right: 72px; top: 206px; height: 8px; border-radius: 4px; background: var(--track); }
.prog i { display: block; height: 100%; width: 0; border-radius: 4px; background: var(--ac); }
.brand { position: absolute; left: 72px; right: 72px; top: 236px; display: flex; justify-content: space-between; align-items: center; gap: 20px; }
.brand .mark { font-size: 30px; color: var(--fg); gap: 12px; }
.brand .mark svg { width: 40px; height: 40px; }
.brand .tag { font-size: 21px; color: var(--mu); }
.frame { position: absolute; left: 72px; right: 72px; top: 330px; bottom: var(--fb, 520px); display: flex; flex-direction: column; justify-content: center; transform-origin: 50% 45%; }
.voiced .frame { --fb: 700px; }
.fit { width: 100%; --k: 1; }
.foot { position: absolute; left: 72px; right: 72px; bottom: 428px; font-size: 21px; line-height: 1.35; color: var(--mu); max-height: 86px; overflow: hidden; }
.chip { align-self: flex-start; display: inline-block; background: var(--chip); color: var(--chipfg); font-weight: 600; font-size: calc(var(--k) * 28px); letter-spacing: 3px; text-transform: uppercase; padding: 12px 24px; border-radius: 999px; margin-bottom: 26px; }
.kicker { font-weight: 600; font-size: calc(var(--k) * 27px); letter-spacing: 3px; text-transform: uppercase; color: var(--mu); margin-bottom: 18px; }
.big { font-family: "Source Serif 4", serif; font-weight: 700; color: var(--hi); line-height: 1; font-size: calc(var(--k) * var(--bs, 220px)); transform-origin: 0 60%; }
.head { font-family: "Source Serif 4", serif; font-weight: 700; font-size: calc(var(--k) * 60px); line-height: 1.13; margin-top: 30px; }
.head:first-child, .chip + .head, .kicker + .head { margin-top: 0; }
.text { font-size: calc(var(--k) * 36px); line-height: 1.42; color: var(--mu); margin-top: 26px; }
.ln { display: block; white-space: nowrap; }
.nw { white-space: nowrap; }
.label { font-weight: 600; color: var(--fg); }
.by { font-size: calc(var(--k) * 29px); color: var(--mu); margin-top: 26px; }
.row { background: var(--card); border-radius: 18px; padding: calc(var(--k) * 18px) 26px; margin-top: 14px; font-size: calc(var(--k) * 33px); line-height: 1.3; }
.row .label { color: var(--ac); margin-right: 10px; }
.layout-rows .head { margin-bottom: 14px; }
.quote { margin-top: 34px; border-left: 8px solid var(--ac); padding-left: 30px; }
.quote .ql { font-weight: 600; font-size: calc(var(--k) * 24px); letter-spacing: 2px; text-transform: uppercase; color: var(--mu); margin-bottom: 14px; }
.quote .qt { font-family: "Source Serif 4", serif; font-weight: 700; font-size: calc(var(--k) * 46px); line-height: 1.25; }
.quote.small .qt { font-family: "Public Sans", sans-serif; font-weight: 400; font-size: calc(var(--k) * 32px); line-height: 1.4; }
mark.key { color: #1b2a2f; background: linear-gradient(#ffd166, #ffd166) no-repeat 0 0 / 0% 100%; border-radius: 6px; padding: 0 .06em; }
.bar { margin-top: 22px; }
.bar .bl { display: flex; justify-content: space-between; gap: 20px; font-size: calc(var(--k) * 34px); }
.bar .nm { font-weight: 600; }
.bar .nv { color: var(--mu); white-space: nowrap; }
.bar .tr { height: calc(var(--k) * 24px); background: var(--track); border-radius: 12px; margin-top: 10px; overflow: hidden; }
.bar .fill { height: 100%; width: 0; background: var(--ac); border-radius: 12px; }
.bars.compact .bar { margin-top: 10px; } .bars.compact .bl { font-size: calc(var(--k) * 26px); } .bars.compact .tr { height: 12px; }
.note { font-size: calc(var(--k) * 25px); color: var(--mu); margin-top: 18px; }
.outcome { font-weight: 600; font-size: calc(var(--k) * 30px); margin-top: 18px; }
.contest { font-weight: 600; font-size: calc(var(--k) * 30px); color: var(--mu); margin: 26px 0 4px; }
.exp-id { font-family: "Source Serif 4", serif; font-weight: 700; color: var(--hi); font-size: calc(var(--k) * 150px); line-height: 1; transform-origin: 0 60%; }
.exp-kick { font-weight: 600; font-size: calc(var(--k) * 26px); letter-spacing: 3px; text-transform: uppercase; color: var(--mu); margin: 18px 0 22px; }
.exp .head { font-size: calc(var(--k) * 52px); }
.exp-cards { display: flex; gap: 16px; margin-top: 26px; }
.exp-card { flex: 1; background: var(--card); border-radius: 20px; padding: 22px 24px; }
.exp-card .cl { font-weight: 600; font-size: calc(var(--k) * 22px); letter-spacing: 2px; text-transform: uppercase; color: var(--ac); margin-bottom: 10px; }
.exp-card .cv { font-size: calc(var(--k) * 30px); line-height: 1.32; }
.exp .quote { margin-top: 28px; }
.docwin { position: relative; height: calc(var(--k) * 640px); background: #fff; border-radius: 24px; overflow: hidden; margin-top: 26px; box-shadow: 0 18px 50px rgba(0,0,0,.35); }
.docwin img { position: absolute; left: 0; top: 0; transform-origin: 0 0; max-width: none; }
.doc .head { font-size: calc(var(--k) * 50px); }
.doctitle { font-size: calc(var(--k) * 25px); color: var(--mu); margin-top: 20px; }
.bg { position: absolute; inset: 0; overflow: hidden; }
.bg img { position: absolute; left: 0; top: 0; width: 1080px; height: 1920px; object-fit: cover; transform-origin: 50% 50%; }
.bg.dark img { filter: brightness(.36) saturate(.85); }
.bg.photo img { filter: brightness(.5); }
.bg::after { content: ""; position: absolute; inset: 0; background: linear-gradient(rgba(10,18,20,.35), rgba(10,18,20,.15) 40%, rgba(10,18,20,.6)); }
.has-bg { background: #0b1214; }
#cap { position: absolute; left: 56px; right: 56px; bottom: 530px; display: flex; justify-content: center; visibility: hidden; z-index: 5; }
#cap span { background: rgba(12,21,24,.9); color: #fff; font-weight: 600; font-size: 50px; line-height: 1.22; padding: 16px 30px; border-radius: 20px; text-align: center; max-width: 968px; border: 2px solid rgba(255,255,255,.18); }
"""

ENGINE = r"""
const R = window.REEL; let SC = [];
const cl = x => Math.max(0, Math.min(1, x));
const out3 = p => 1 - Math.pow(1 - p, 3);
const inout = p => p < .5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
const STEP = {rise: .2, pop: .3, slide: .34, count: .5, bar: .14, type: 0};
const DUR = {rise: .5, pop: .45, slide: .45, count: 1.1, bar: .9, mark: .55};
function fmt(v, dec, comma) {
  return comma ? v.toLocaleString('en-US', {minimumFractionDigits: dec, maximumFractionDigits: dec}) : v.toFixed(dec);
}
function fit(sc) {
  const box = sc.querySelector('.fit'), fr = sc.querySelector('.frame'); let k = 1;
  while (box.offsetHeight > fr.clientHeight && k > .5) { k -= .03; box.style.setProperty('--k', k); }
}
function splitLines(el) {
  const words = el.textContent.trim().split(/\s+/); el.textContent = '';
  const spans = words.map(w => { const s = document.createElement('span'); s.textContent = w; s.style.whiteSpace = 'nowrap'; el.appendChild(s);
    el.appendChild(document.createTextNode(' ')); return s; });
  const lines = []; let top = null;
  for (const s of spans) { if (s.offsetTop !== top) { lines.push([]); top = s.offsetTop; } lines[lines.length - 1].push(s.textContent); }
  el.textContent = '';
  return lines.map(ws => { const l = document.createElement('span'); l.className = 'ln'; l.textContent = ws.join(' '); el.appendChild(l); return l; });
}
function schedule(sc, info) {
  const items = []; let cur = .08;
  for (const el of sc.querySelectorAll('[data-in]')) {
    const kind = el.dataset.in, at = cur + (+(el.dataset.delay || 0));
    if (kind === 'lines') { const ls = splitLines(el); ls.forEach((l, i) => items.push({el: l, kind: 'rise', at: at + i * .26, dur: .5}));
      cur = at + ls.length * .26; continue; }
    if (kind === 'type') { const chs = [...el.querySelectorAll('.ch')]; const d = Math.min(2.0, chs.length * .028);
      items.push({el, kind, at, dur: d, chs}); el.querySelectorAll('mark').forEach(m => items.push({el: m, kind: 'mark', at: at + d, dur: DUR.mark}));
      cur = at + d + .25; continue; }
    items.push({el, kind, at, dur: DUR[kind]}); cur = at + STEP[kind];
  }
  const end = Math.max(0, ...items.map(i => i.at + i.dur)), room = Math.max(.8, info.dur * .6);
  if (end > room) { const f = room / end; items.forEach(i => { i.at *= f; i.dur *= f; }); }
  return items;
}
function apply(it, p) {
  const e = it.el, q = out3(p);
  switch (it.kind) {
    case 'rise': e.style.opacity = q; e.style.transform = `translateY(${(1 - q) * 38}px)`; break;
    case 'pop': e.style.opacity = Math.min(1, q * 1.6); e.style.transform = `scale(${.86 + .14 * q})`; break;
    case 'slide': e.style.opacity = q; e.style.transform = `translateX(${(q - 1) * 80}px)`; break;
    case 'count': { const d = e.dataset; e.textContent = d.pre + fmt(+d.num * q, +d.dec, d.comma === '1') + d.suf;
      e.style.transform = `scale(${.9 + .1 * q})`; break; }
    case 'bar': { e.querySelector('.fill').style.width = (+e.dataset.pct * q) + '%'; e.style.opacity = Math.min(1, p * 4);
      for (const n of e.querySelectorAll('[data-num]')) n.textContent = fmt(+n.dataset.num * q, +n.dataset.dec, true) + (n.dataset.suf || '');
      break; }
    case 'type': { const n = Math.round(it.chs.length * p); it.chs.forEach((c, i) => { c.style.opacity = i < n ? 1 : 0; }); break; }
    case 'mark': e.style.backgroundSize = `${q * 100}% 100%`; break;
  }
}
function docZoom(img, lt, info) {
  const win = img.parentElement, ww = win.clientWidth, wh = win.clientHeight, d = img.dataset;
  const hw = +d.hw, hh = +d.hh, cx = +d.hx + hw / 2, cy = +d.hy + hh / 2, iw = +d.iw, ih = +d.ih;
  const s0 = ww / iw, s1 = Math.max(s0 * 1.35, Math.min(.94 * ww / hw, .8 * wh / hh, s0 * 2.6));
  const p = inout(cl((lt - .35) / Math.max(.5, info.dur - .9))), s = s0 + (s1 - s0) * p;
  let tx = ww / 2 - cx * s, ty = wh / 2 - cy * s;
  if (iw * s >= ww) tx = Math.min(0, Math.max(ww - iw * s, tx)); else tx = (ww - iw * s) / 2;
  if (ih * s >= wh) ty = Math.min(0, Math.max(wh - ih * s, ty)); else ty = (wh - ih * s) / 2;
  img.style.transform = `translate(${tx}px, ${ty}px) scale(${s})`;
}
async function init() {
  await document.fonts.ready;
  SC = [...document.querySelectorAll('.scene')].map((el, i) => {
    fit(el); return {el, items: schedule(el, R.scenes[i]), frame: el.querySelector('.frame'), prog: el.querySelector('.prog i'),
      doc: el.querySelector('.docwin img'), photo: el.querySelector('.bg.photo img'), broll: el.querySelector('.bg.dark img')};
  });
  const imgs = [...document.images].map(i => i.decode().catch(() => null));
  await Promise.all(imgs);
  return true;
}
let shown = -1;
async function seek(t) {
  let idx = R.scenes.findIndex(s => t >= s.start && t < s.start + s.dur); if (idx < 0) idx = R.scenes.length - 1;
  if (idx !== shown) { SC.forEach((s, i) => { s.el.style.visibility = i === idx ? 'visible' : 'hidden'; }); shown = idx; }
  const sc = SC[idx], info = R.scenes[idx], lt = t - info.start;
  for (const it of sc.items) apply(it, info.hook ? 1 : cl((lt - it.at) / it.dur));
  let s = 1 + .022 * cl(lt / info.dur);
  if (!info.hook) s += .07 * (1 - out3(cl(lt / .32)));
  sc.frame.style.transform = `scale(${s})`;
  sc.prog.style.width = (100 * cl(t / R.total)) + '%';
  if (sc.doc) docZoom(sc.doc, lt, info);
  if (sc.photo) { const p = cl(lt / info.dur); sc.photo.style.transform = `scale(${1.06 + .12 * p}) translate(${-1.6 * p}%, ${-1.2 * p}%)`; }
  if (sc.broll) { const d = sc.broll.dataset, n = Math.min(+d.n, 1 + Math.floor(lt * R.fps)), src = `${d.dir}/${String(n).padStart(5, '0')}.jpg`;
    if (!sc.broll.src.endsWith(src)) { sc.broll.src = src; await sc.broll.decode().catch(() => null); } }
  const cap = document.getElementById('cap'), c = window.NOCAP ? null : R.caps.find(c => t >= c.a && t < c.b);
  if (c) { const sp = cap.firstChild; if (sp.textContent !== c.text) sp.textContent = c.text;
    cap.style.visibility = 'visible'; const q = out3(cl((t - c.a) / .12)); sp.style.transform = `scale(${.96 + .04 * q})`; }
  else cap.style.visibility = 'hidden';
  return idx;
}
"""


def _attrs(**kv) -> str:
    return "".join(f' data-{k.replace("_", "-")}="{html.escape(str(v), quote=True)}"' for k, v in kv.items() if v is not None)


def big_html(big) -> str:
    text, e = str(big), html.escape
    size = 260 if len(text) <= 4 else 210 if len(text) <= 7 else 150 if len(text) <= 10 else 120
    c = countable(text)
    style = f' style="--bs:{size}px"'
    if c:
        return (f'<div class="big"{style} data-in="count"{_attrs(pre=c["pre"], num=c["num"], dec=c["dec"], comma=int(c["comma"]), suf=c["suf"])}>'
                f'{e(text)}</div>')
    return f'<div class="big"{style}>{e(text)}</div>'


def nb(text: str) -> str:
    """Escaped text with each hyphenated word kept on one line ("minimum-wage" never splits at the hyphen)."""
    return re.sub(r"\S*\w-\w\S*", lambda m: f'<span class="nw">{m[0]}</span>', html.escape(text))


def typed(text: str, key: str | None) -> str:
    """Each character in its own span (so it can type in without the lines moving); the key phrase in a mark."""
    e = html.escape
    chars = lambda s: "".join(f'<span class="ch">{e(ch)}</span>' for ch in s)
    if key and key in text:
        a, b = text.split(key, 1)
        return chars(a) + f'<mark class="key">{chars(key)}</mark>' + chars(b)
    return chars(text)


def bars_html(lines: list[tuple[str, int, str | None]], total: int) -> str:
    e, out = html.escape, ""
    for name, n, party in lines:
        pct = 100 * n / (total or 1)
        tag = f' <span class="nv">{e(party)}</span>' if party else ""
        out += (f'<div class="bar" data-in="bar"{_attrs(pct=round(pct, 3))}><div class="bl"><span class="nm">{e(name)}{tag}</span>'
                f'<span class="nv"><span{_attrs(num=n, dec=0)}>{n:,}</span> · <span{_attrs(num=round(pct, 1), dec=1, suf="%")}>{pct:.1f}%</span></span></div>'
                f'<div class="tr"><div class="fill"></div></div></div>')
    return f'<div class="bars{" compact" if len(lines) > 6 else ""}">{out}</div>'


def content_html(s: dict, lay: str, media: dict | None) -> str:
    e = html.escape
    lines_mode = lay in ("plain",)
    out = f'<div class="chip">{e(s["chip"])}</div>' if s.get("chip") else ""
    out += f'<div class="kicker">{e(s["kicker"])}</div>' if s.get("kicker") else ""
    if lay == "explainer":
        rows = B.rows(s)
        q = s.get("quote") or {}
        return (f'<div class="exp"><div class="exp-id">{e(s["chip"])}</div>'
                f'<div class="exp-kick">{e(s.get("kicker") or "")}</div>'
                f'<div class="head">{nb(s["head"])}</div>'
                '<div class="exp-cards">' + "".join(f'<div class="exp-card" data-in="slide"><div class="cl">{e(a)}</div><div class="cv">{e(b)}</div></div>'
                                                    for a, b in rows) + "</div>"
                + (f'<div class="quote small" data-in="rise"><div class="ql">{e(q.get("label", "Official summary"))}</div>'
                   f'<div class="qt">“{e(q["text"])}”</div></div>' if q.get("text") else "") + "</div>")
    if lay == "doc":
        d = s["doc"]
        title = d["title"] + (f' · page {media["page"]}' if media and media.get("page") else "")
        head = f'<div class="head">{nb(s["head"])}</div>' if s.get("head") else ""
        img = (f'<img src="{media["img"]}" alt=""{_attrs(iw=media["iw"], ih=media["ih"], hx=round(media["hx"], 1), hy=round(media["hy"], 1), hw=round(media["hw"], 1), hh=round(media["hh"], 1))}'
               f' style="width:{media["iw"]}px;height:{media["ih"]}px">') if media else ""
        return (f'<div class="doc">{out}{head}<div class="docwin" data-in="pop">{img}</div>'
                f'<div class="doctitle" data-in="rise">{e(title)}</div></div>')
    if s.get("big"):
        out += big_html(s["big"])
    if s.get("head"):
        out += f'<div class="head">{nb(s["head"])}</div>'
    if s.get("text"):
        out += f'<div class="text" data-in="{"lines" if lines_mode else "rise"}">{e(s["text"])}</div>'
    for a, b in B.rows(s):
        out += f'<div class="row" data-in="slide">{f"<span class=label>{e(a)}</span>" if a else ""}{e(b)}</div>'
    if s.get("results"):
        r = s["results"]
        lines = [(c["name"], c["votes"], c.get("party")) for c in r["candidates"]]
        if r.get("other_votes"):
            lines.append(("Other write-ins", r["other_votes"], None))
        out += f'<div class="contest" data-in="rise">{e(r["contest"])}</div>' + bars_html(lines, sum(n for _, n, _ in lines))
        if r.get("outcome"):
            out += f'<div class="outcome" data-in="rise">{e(r["outcome"])}</div>'
        when = f'{r["status"].capitalize()} results as of {r["as_of"]}' + (f' · {r["reporting"]}' if r.get("reporting") else "")
        out += f'<div class="note" data-in="rise">{e(when)}</div>' + (f'<div class="note">{e(r["note"])}</div>' if r.get("note") else "")
    if s.get("vote"):
        v = s["vote"]
        lines = [(label, v[k], None) for k, label in B.VOTE_LABELS.items() if k in v]
        out += bars_html(lines, sum(v.values()))
    if s.get("quote"):
        q = s["quote"]
        if lay == "quote":
            out += (f'<div class="quote"><div class="ql" data-in="rise">{e(q.get("label", "Official text"))}</div>'
                    f'<div class="qt" data-in="type"><span class="ch">“</span>{typed(q["text"], q.get("key"))}<span class="ch">”</span></div></div>')
        else:
            out += (f'<div class="quote small" data-in="rise"><div class="ql">{e(q.get("label", "Official text"))}</div>'
                    f'<div class="qt">“{e(q["text"])}”</div></div>')
    if s.get("by"):
        out += f'<div class="by" data-in="rise">{e(s["by"])}</div>'
    return out


def page_html(post: dict, slides: list[dict], scenes: list[dict], media: list[dict | None], data: dict) -> str:
    e = html.escape
    body = ""
    tag = B.level_tag(post)
    for s, sc, m in zip(slides, scenes, media):
        bg, cls = "", f'scene act-{sc["act"]} layout-{sc["layout"]}' + (" voiced" if sc["voiced"] else "")
        foot = B.source_line(post)
        if s.get("broll"):
            bg = f'<div class="bg dark"><img alt=""{_attrs(dir=m["dir"], n=m["n"])} src="{m["dir"]}/00001.jpg"></div>'
            foot = f'Video: {s["broll"]["title"]}. {foot}'
            cls += " has-bg"
        elif s.get("image"):
            bg = f'<div class="bg photo"><img alt="" src="{m["img"]}"></div>'
            foot = f'Photo: {s["image"]["title"]} ({s["image"]["license"]}). {foot}'
            cls += " has-bg"
        body += (f'<section class="{cls}">{bg}<div class="prog"><i></i></div>'
                 f'<div class="brand"><span class="mark">{B.LOGO}Vote the Record</span><span class="tag">{e(tag)}</span></div>'
                 f'<div class="frame"><div class="fit">{content_html(s, sc["layout"], m if not (s.get("broll") or s.get("image")) else None)}</div></div>'
                 f'<div class="foot">{e(foot)}</div></section>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{B.css(W, H)}{CSS}</style></head>'
            f'<body>{body}<div id="cap"><span></span></div>'
            f'<script>window.REEL = {json.dumps(data)};{ENGINE}</script></body></html>')


# ---------------------------------------------------------------- render

def launch(p):
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    try:
        return p.chromium.launch(args=args)
    except Exception:  # bundled Chromium not installed: use the local Chrome
        return p.chromium.launch(channel="chrome", args=args)


def alt_text(post: dict, slides: list[dict], scripts: list[str | None]) -> str:
    parts, src = [], B.source_line(post)
    for s, say in zip(slides, scripts):
        a = B.alt_text(post, s)
        a = a[: a.rfind(src)].strip() if src in a else a  # the sources once, at the end
        if s.get("doc"):
            a += f' Official document shown: {s["doc"]["title"]}. Highlighted words: "{norm(s["doc"]["highlight"])}".'
        if s.get("broll"):
            a += f' Background video: {s["broll"]["title"]}.'
        if s.get("image"):
            a += f' Background photo: {s["image"]["title"]}.'
        if say:
            a += f" Narration: {say}"
        parts.append(a)
    return " ".join(parts + [src + "."])


def render(post: dict, slides: list[dict], out: Path, *, fps: int = FPS, stills=None, cache: Path | None = None,
           preset: str = "medium") -> list[dict]:
    """Write out/reel.mp4 and out/cover.png (and out/voice.txt with a voice); the manifest's files entry.
    stills: slide numbers (1-based) to also save as still-NN.png, mid-way through each."""
    from playwright.sync_api import sync_playwright
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    TIMING.clear()
    t0 = time.perf_counter()
    scripts = []
    for s in slides:
        b = explainer_bill(post, s)
        scripts.append((b or {}).get("say") if b else s.get("say"))
    voice = {**VOICE, **(post["voice"] if isinstance(post.get("voice"), dict) else {})}
    voiced = any(scripts)
    clips, lengths, caps, sr = voice_track(scripts, voice) if voiced else ([None] * len(slides), [None] * len(slides), [[]] * len(slides), 24000)
    lays = [layout(s, post) for s in slides]
    durs = durations(slides, lengths, lays)
    starts = [round(sum(durs[:k]), 3) for k in range(len(durs))]
    total = round(sum(durs), 3)
    TIMING["voice"] = time.perf_counter() - t0
    scenes, prev = [], None
    for k, (s, lay) in enumerate(zip(slides, lays)):
        prev = act(s, lay, k, prev)
        scenes.append({"layout": lay, "act": prev, "voiced": bool(scripts[k])})
    allcaps = [{"a": round(starts[k] + VOICE_AT + c["a"], 3), "b": round(starts[k] + VOICE_AT + c["b"], 3), "text": c["text"]}
               for k, cs in enumerate(caps) for c in cs]
    data = {"fps": fps, "total": total, "caps": allcaps,
            "scenes": [{"start": a, "dur": d, "hook": k == 0} for k, (a, d) in enumerate(zip(starts, durs))]}
    if voiced:
        audio = ["-i", str(mix(clips, starts, total, sr, out))]
        (out / "voice.txt").write_text("\n".join(f"Slide {k}: {say}" for k, say in enumerate(scripts, 1) if say) + "\n", encoding="utf-8")
    else:
        audio = ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    TIMING["audio"] = time.perf_counter() - t0 - TIMING["voice"]
    temp = [out / "reel.html"]
    with sync_playwright() as p:
        browser = launch(p)
        try:
            t1 = time.perf_counter()
            media = []
            for k, s in enumerate(slides, 1):
                if s.get("doc"):
                    media.append(prepare_doc(browser, s["doc"], out, k, cache))
                elif s.get("broll"):
                    media.append(prepare_broll(s["broll"], out, k, fps))
                elif s.get("image"):
                    media.append(prepare_image(s["image"], out, k, cache))
                else:
                    media.append(None)
                if media[-1]:
                    temp.append(out / (media[-1].get("img") or media[-1]["dir"]))
            TIMING["media"] = time.perf_counter() - t1
            (out / "reel.html").write_text(page_html(post, slides, scenes, media, data), encoding="utf-8")
            page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            page.goto((out / "reel.html").as_uri())
            page.evaluate("init()")
            cdp = page.context.new_cdp_session(page)
            n = max(1, round(total * fps))
            log = open(out / "ffmpeg.log", "w")
            temp.append(out / "ffmpeg.log")
            ff = subprocess.Popen([B.FFMPEG, "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
                                   "-i", "-", *audio, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", preset, "-crf", "20",
                                   "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p",  # JPEG frames are full range
                                   "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                                   "-r", str(fps), "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
                                   "-t", f"{total:.3f}", "-movflags", "+faststart", str(out / "reel.mp4")],
                                  stdin=subprocess.PIPE, stderr=log)
            t2 = time.perf_counter()
            for i in range(n):
                page.evaluate("t => seek(t)", i / fps)
                shot = cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 92, "optimizeForSpeed": True})
                ff.stdin.write(base64.b64decode(shot["data"]))
            ff.stdin.close()
            code = ff.wait()
            log.close()
            TIMING["frames"] = time.perf_counter() - t2
            TIMING["frame_count"] = n
            if code:
                raise RuntimeError("ffmpeg failed: " + (out / "ffmpeg.log").read_text()[-800:])
            page.evaluate("window.NOCAP = true")
            page.evaluate("t => seek(t)", max(0.0, durs[0] - 0.05))
            page.screenshot(path=str(out / "cover.png"))
            page.evaluate("window.NOCAP = false")
            for k in stills or []:
                if 1 <= k <= len(slides):
                    page.evaluate("t => seek(t)", starts[k - 1] + durs[k - 1] * 0.55)
                    page.screenshot(path=str(out / f"still-{k:02d}.png"))
        finally:
            browser.close()
    for f in temp + [out / "voice.wav"]:
        if f.is_dir():
            for x in f.iterdir():
                x.unlink()
            f.rmdir()
        else:
            f.unlink(missing_ok=True)
    TIMING["total"] = time.perf_counter() - t0
    TIMING["video_seconds"] = total
    return [{"file": "reel.mp4", "cover": "cover.png", "alt_text": alt_text(post, slides, scripts)}]


# ---------------------------------------------------------------- command line

NEW_FIELD = re.compile(r"unknown field:? (say|doc|broll|image|voice)$|bill .*: unknown field say$")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("post", type=Path, nargs="?")
    ap.add_argument("--out", type=Path, help="folder for reel.mp4, cover.png, voice.txt")
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--stills", default="", help="slide numbers to save as still-NN.png, e.g. 1,3,5")
    ap.add_argument("--cache", type=Path, default=os.environ.get("VTR_DOC_CACHE"),
                    help="keep fetched docs and photos here, so repeat previews don't ask the site again")
    ap.add_argument("--download-model", action="store_true", help="fetch the Kokoro model into the cache and stop")
    a = ap.parse_args()
    if a.download_model:
        print(f"Kokoro model ready in {ensure_model()}")
        return
    if not (a.post and a.out):
        ap.error("give a post file and --out")
    post = yaml.safe_load(a.post.read_text(encoding="utf-8"))
    # build.check() learns these fields when reel.py is wired in; until then its 'unknown field' notes are skipped here
    errs = [x for x in B.check(post, editorial=False) if not NEW_FIELD.search(x)] + check(post)
    if errs:
        sys.exit(f"{a.post}:\n  " + "\n  ".join(errs))
    files = render(post, B.expanded(post), a.out, fps=a.fps, cache=Path(a.cache) if a.cache else None,
                   stills=[int(x) for x in a.stills.split(",") if x.strip()])
    print(json.dumps(files[0] | {"timing": {k: round(v, 2) for k, v in TIMING.items()}}, indent=2))


if __name__ == "__main__":
    main()
