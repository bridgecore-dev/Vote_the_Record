"""Build an Instagram post (feed image, carousel, story, Reel, or hearing clip) from a post file in social/posts/.

    python src/social/build.py social/posts/<date>-<slug>.yaml [--check]

The post-file format is in social/README.md. Slides are HTML in the site's style, rendered to PNG by headless
Chrome; a Reel joins its slides with ffmpeg and a silent audio track (music is added in the Instagram app).
Output goes to social/out/<post>/ with manifest.json: the files in posting order, each image's alt text, the
full caption (text, sources, hashtags), and whether the post may publish without the owner's approval.
--check validates the post file and writes nothing.
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHROME = [Path(r"C:/Program Files/Google/Chrome/Application/chrome.exe"), Path("/usr/bin/google-chrome"),
          Path("/usr/bin/chromium")]
FFMPEG = shutil.which("ffmpeg") or r"C:/ffmpeg/bin/ffmpeg.exe"
KINDS = {"image", "carousel", "story", "reel", "clip"}
LEVELS = {"congress", "state", "local"}
LAYOUTS = ["card", "band", "stat"]
SIZES = {"feed": (1080, 1350), "tall": (1080, 1920)}
SECONDS, FADE = 4.0, 0.4  # each Reel slide; the slide transition
SLIDE_KEYS = {"chip", "kicker", "big", "head", "text", "rows", "results", "vote", "quote", "by", "seconds", "swipe",
              "hint", "countdown", "quiz",
              "say", "doc", "broll", "image", "ai_image"}  # see social/README.md; the last four: Reels (reel.py)
TOP_KEYS = {"kind", "level", "place", "topic", "approval", "layout", "story", "sources", "slides", "caption",
            "hashtags", "rule", "notes", "publish_after", "found_via", "record_ids",
            "video", "captions", "caption_source", "speakers", "context",  # these five: clips (clip.py)
            "highlight", "bills", "voice", "design", "trial"}
REEL_FIELDS = ("say", "doc", "broll", "image")  # need reel.py's renderer; the still-slide Reel can't show them
# A bill number as written federally or in a state: H.R. 2347, S. 12, H.J.Res. 5, HB 101, SJR 3. Not "U.S. 2026".
BILL = re.compile(r"(?<![A-Za-z.])((?:H\.\s?R|S|H\.\s?J\.\s?Res|S\.\s?J\.\s?Res|H\.\s?Con\.\s?Res|S\.\s?Con\.\s?Res|"
                  r"H\.\s?Res|S\.\s?Res)\.|HB|SB|HJR|SJR|HCR|SCR)\s?(\d+)\b")
BILL_KEYS = {"id", "title", "does", "affects", "where", "summary", "say"}  # title: official short title; say: Reel voice
BILL_LIMITS = {"does": 140, "affects": 120, "where": 110}


def bill_key(text: str) -> str:
    return re.sub(r"[\s.]", "", text).upper()


def mentioned_bills(post: dict) -> dict[str, int]:
    """Each bill the post's own words name, and the first slide (0-based) that names it; -1 for the caption only."""
    found: dict[str, int] = {}
    for k, s in enumerate(post.get("slides") or []):
        q = s.get("quote") or {}
        text = " ".join(str(s.get(f) or "") for f in ("chip", "kicker", "big", "head", "text", "by")) + " " + str(q.get("text") or "")
        for m in BILL.finditer(text):
            found.setdefault(bill_key(m[0]), k)
    for m in BILL.finditer(post.get("caption") or ""):
        found.setdefault(bill_key(m[0]), -1)
    return found


def explainer(b: dict) -> dict:
    """The slide that says what a bill would do, for whom, and where it stands, with the official summary."""
    slide = {"chip": b["id"], "kicker": b.get("title") or "What the bill would do", "head": b["does"],
             "rows": [["Who it affects", b["affects"]], ["Where it stands", b["where"]]],
             "quote": {"label": "Official summary", "text": b["summary"]["text"]}, "seconds": 8}
    return {**slide, "say": b["say"]} if b.get("say") else slide


def reel_module():
    """src/social/reel.py, loaded by path ("build" is also the site builder's name, as in recap.py)."""
    if "social_reel" not in sys.modules:
        import importlib.util
        spec = importlib.util.spec_from_file_location("social_reel", Path(__file__).resolve().parent / "reel.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["social_reel"] = module
        spec.loader.exec_module(module)
    return sys.modules["social_reel"]


def expanded(post: dict) -> list[dict]:
    """The slides as published: each bill's explainer right after the first slide that names it."""
    slides = list(post.get("slides") or [])
    first = mentioned_bills(post)
    for b in reversed(post.get("bills") or []):
        at = first.get(bill_key(b.get("id", "")), -1)
        slides.insert(at + 1 if at >= 0 else min(1, len(slides)), explainer(b))
    # the owner's ask: a carousel that names bills tells people on its first slide to swipe (a Reel plays itself)
    first_text = " ".join(str(slides[0].get(f) or "") for f in ("head", "text", "by")).lower() if slides else ""
    if post.get("bills") and post.get("kind") == "carousel" and len(slides) > 1 and "swipe" not in first_text:
        slides[0] = {**slides[0], "swipe": True}
    out = []
    for s in slides:
        if isinstance(s.get("countdown"), dict) and countdown_days(post, s["countdown"]) is not None:
            s = countdown_slide(post, s)
        if quiz_ok(s.get("quiz")):
            question, answer = quiz_slides(post, s)
            out += [question, answer]
            continue
        out.append(s)
    return out


def publish_date(post: dict) -> dt.date | None:
    try:
        return dt.datetime.fromisoformat(str(post.get("publish_after"))).date()
    except ValueError:
        return None


def countdown_days(post: dict, c: dict) -> int | None:
    """Days from the day the post goes out to the event: right on the day people see it."""
    try:
        return (dt.date.fromisoformat(str(c.get("date"))) - publish_date(post)).days
    except (TypeError, ValueError):
        return None


def countdown_slide(post: dict, s: dict) -> dict:
    c, days = s["countdown"], countdown_days(post, s["countdown"])
    when = dt.date.fromisoformat(str(c["date"]))
    head = f"{'day' if days == 1 else 'days'} until {c['event']}" if days else f"Today: {c['event']}"
    return {**{k: v for k, v in s.items() if k != "countdown"}, "big": str(days) if days else "Today",
            "head": head, "text": s.get("text") or f"{when:%A, %B} {when.day}, {when.year}"}


def quiz_ok(q) -> bool:
    return (isinstance(q, dict) and isinstance(q.get("choices"), list) and 2 <= len(q["choices"]) <= 4
            and isinstance(q.get("answer"), int) and 0 <= q["answer"] < len(q["choices"]) and bool(q.get("explain")))


def quiz_slides(post: dict, s: dict) -> tuple[dict, dict]:
    """The question with lettered choices, then its answer: no sticker needed (the API can't add one)."""
    q = s["quiz"]
    letters = "ABCD"
    move = "Tap" if post.get("kind") == "story" else "Swipe"
    question = {**{k: v for k, v in s.items() if k != "quiz"},
                "rows": [[letters[i], str(c)] for i, c in enumerate(q["choices"])], "hint": f"{move} for the answer →"}
    answer = {"chip": "The answer", "head": f"{letters[q['answer']]}. {q['choices'][q['answer']]}", "text": q["explain"]}
    if q.get("quote"):
        answer["quote"] = q["quote"]
    return question, answer


def check_story_templates(post: dict) -> list[str]:
    errs = []
    for k, s in enumerate(post.get("slides") or [], 1):
        c = s.get("countdown")
        if c is not None:
            if not (isinstance(c, dict) and c.get("date") and c.get("event")):
                errs.append(f"slide {k}: countdown is {{date: YYYY-MM-DD, event}}")
            elif publish_date(post) is None:
                errs.append(f"slide {k}: a countdown needs publish_after, so its day count is right when it posts")
            elif countdown_days(post, c) is None or countdown_days(post, c) < 0:
                errs.append(f"slide {k}: the countdown date has to be on or after the day the post goes out")
            if s.get("big"):
                errs.append(f"slide {k}: a countdown makes its own big number; drop big")
        q = s.get("quiz")
        if q is not None:
            if not quiz_ok(q):
                errs.append(f"slide {k}: quiz is {{choices: 2-4, answer: index from 0, explain, quote (optional)}}")
            elif post.get("kind") not in ("story", "carousel"):
                errs.append(f"slide {k}: a quiz needs a second slide for its answer: a story or carousel")
            if not s.get("head"):
                errs.append(f"slide {k}: the quiz question goes in head")
            if post.get("approval") == "auto":
                errs.append(f"slide {k}: a quiz's answer is explained in our words: use approval review")
    return errs


def check_bills(post: dict, editorial: bool = True) -> list[str]:
    errs, bills = [], post.get("bills") or []
    given = {}
    for b in bills:
        errs += [f"bill {b.get('id')}: unknown field {k}" for k in b if k not in BILL_KEYS]
        if not BILL.fullmatch(str(b.get("id", ""))):
            errs.append(f"bill id {b.get('id')!r} isn't a bill number like H.R. 2347 or HB 101")
        for f, n in BILL_LIMITS.items():
            if not str(b.get(f) or "").strip():
                errs.append(f"bill {b.get('id')}: needs {f} (plain words; nothing the official summary doesn't say)")
            elif len(b[f]) > n:
                errs.append(f"bill {b.get('id')}: {f} is {len(b[f])} characters; keep it under {n}")
        s = b.get("summary") or {}
        if not (s.get("text") and s.get("title") and s.get("url")):
            errs.append(f"bill {b.get('id')}: summary needs text (exact official words), title, url")
        elif not official(s["url"]):
            errs.append(f"bill {b.get('id')}: the summary comes from an official site (CRS on congress.gov, the legislature's bill analysis)")
        elif len(s["text"]) > 300:
            errs.append(f"bill {b.get('id')}: summary text is {len(s['text'])} characters; quote the key 300")
        given[bill_key(str(b.get("id", "")))] = b
    if not editorial:
        return errs
    named = mentioned_bills(post)
    missing = sorted(k for k in named if k not in given)
    if missing:
        errs.append(f"the post names {', '.join(missing)} without saying what it does: add each to bills "
                    "(does, affects, where, official summary)")
    if (named or bills) and post.get("approval") == "auto":
        errs.append("explaining a bill in our words is a judgment call: use approval review")
    if (named or bills) and post.get("kind") == "image":
        errs.append("a single image can't explain a bill; use a carousel")
    return errs


def official(url: str) -> bool:
    """Government domains only: what a routine post may rest on without the owner's tap."""
    host = urlparse(url).hostname or ""
    return host.endswith(".gov") or host.endswith(".us") or host.endswith(".mil")


def check(post: dict, editorial: bool = True) -> list[str]:
    """Problems with a post file. editorial=False keeps only what rendering needs: building a post that was
    already approved (merged) must not fail because an editorial rule was added after the owner approved it."""
    errs = [f"unknown field: {k}" for k in post if k not in TOP_KEYS]
    if post.get("kind") not in KINDS:
        errs.append(f"kind must be one of {sorted(KINDS)}")
    if post.get("level") not in LEVELS:
        errs.append(f"level must be one of {sorted(LEVELS)}")
    if post.get("level") in ("state", "local") and not post.get("place"):
        errs.append("a state or local post names its place (e.g. Texas, or Harris County, Texas)")
    if post.get("approval") not in ("auto", "review"):
        errs.append("approval must be auto or review")
    if post.get("layout") and post["layout"] not in LAYOUTS:
        errs.append(f"layout must be one of {LAYOUTS}")
    sources = post.get("sources") or []
    if not sources:
        errs.append("no sources: every post cites at least one")
    for s in sources:
        if not (s.get("title") and s.get("url") and s.get("retrieved")):
            errs.append(f"each source needs title, url, retrieved: {s}")
    if editorial and post.get("approval") == "auto" and not all(official(s.get("url", "")) for s in sources):
        errs.append("approval auto needs every source on a .gov/.us/.mil domain; use review")
    # found_via: the news item that pointed us to the story; for the reviewer only, never a source
    if post.get("found_via"):
        if editorial and post.get("approval") == "auto":
            errs.append("a post found via news uses approval review")
        if post["found_via"] in {s.get("url") for s in sources}:
            errs.append("found_via is not a source; cite the official record instead")
        # post files and pull requests are public: an owner's idea is named by its ID only, never its words or link
        if not (re.fullmatch(r"https://\S+", str(post["found_via"]))
                or re.fullmatch(r"owner idea [A-Za-z0-9_:.-]{1,80}", str(post["found_via"]))):
            errs.append("found_via is a news URL or 'owner idea <id>'; never the owner's note or shared link")
        elif re.search(r"//([\w-]+\.)*(instagram\.com|facebook\.com|fb\.watch|tiktok\.com|threads\.net|x\.com|twitter\.com)\b",
                       str(post["found_via"])):
            errs.append("found_via: a social media link is the owner's shared idea; write 'owner idea <id>' instead")
    if post.get("publish_after"):
        try:
            when = dt.datetime.fromisoformat(str(post["publish_after"]))
            if when.tzinfo is None:
                errs.append("publish_after needs a time zone, e.g. 2026-11-14T08:00:00-06:00")
        except ValueError:
            errs.append(f"publish_after is not an ISO date-time: {post['publish_after']}")
    ids = post.get("record_ids")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) and i.strip() for i in ids)):
        errs.append("record_ids is a list of official IDs as strings, e.g. ['tx/90R/HB 101', 'us/119/house/roll/316']")
    motions = getattr(reel_module(), "MOTIONS", None) if post.get("kind") == "reel" else None
    errs += check_bills(post, editorial) + check_story_templates(post) + check_design(post, motions) + check_ai_images(post)
    slides = expanded(post) if post.get("kind") != "clip" else []
    n = {"image": (1, 1), "story": (1, 10), "carousel": (2, 10), "reel": (2, 20)}.get(post.get("kind"), (1, 20))
    if post.get("kind") == "clip":
        import clip
        errs += clip.check(post)
    elif post.get("kind") == "reel":
        errs += reel_module().check(post)
    elif any(s.get(f) for s in slides for f in REEL_FIELDS) or post.get("voice"):
        errs.append(f"say, doc, broll, image and voice are for Reels; this post is a {post.get('kind')}")
    if post.get("kind") != "clip" and not n[0] <= len(slides) <= n[1]:
        errs.append(f"a {post.get('kind')} has {n[0]}-{n[1]} slides, not {len(slides)}")
    for k, s in enumerate(slides, 1):
        errs += check_slide(k, s)
    if not (post.get("caption") or "").strip():
        errs.append("no caption")
    elif not errs and len(full_caption(post)) > 2200:  # Instagram's limit, counting the sources and hashtags we add
        errs.append(f"caption with sources and hashtags is {len(full_caption(post))} characters; Instagram allows 2,200")
    if post.get("highlight") and post["highlight"] not in highlights():
        errs.append(f"highlight {post['highlight']!r} isn't in social/highlights.yaml: {sorted(highlights())}")
    if len(post.get("hashtags") or []) > 5:
        errs.append("at most 5 hashtags")
    return errs


def highlights() -> set[str]:
    spec = yaml.safe_load((ROOT / "social/highlights.yaml").read_text(encoding="utf-8")) or {}
    return {h["name"] for group in spec.values() for h in group or []}


def duplicates(path: Path, post: dict) -> list[str]:
    """record_ids another post file beside this one already covers: the same record never posts twice."""
    mine = set(post.get("record_ids") or [])
    errs = []
    for other in sorted(path.resolve().parent.glob("*.yaml")):
        if other == path.resolve() or not mine:
            continue
        theirs = set((yaml.safe_load(other.read_text(encoding="utf-8")) or {}).get("record_ids") or [])
        errs += [f"record {r} is already in {other.name}" for r in sorted(mine & theirs)]
    return errs


def level_tag(post: dict) -> str:
    return {"congress": "Congress", "state": f"State · {post.get('place', '')}",
            "local": f"Local · {post.get('place', '')}"}[post["level"]]


def source_line(post: dict) -> str:
    titles = [s["title"] for s in all_sources(post)]
    return ("Source: " if len(titles) == 1 else "Sources: ") + "; ".join(titles)


# Design registry: one place for every palette, font and text position, used by the feed slides here and by
# reel.py's Reels, so the two never drift. No red or blue palette: in U.S. politics they read as party colors.
# Each palette has a light and a dark mode (dark: dark background, light ink, one bright accent: 60-30-10, the
# accent only for the key word or number). tests/test_social_build.py checks every pairing is readable.
PALETTES = {
    "teal": {"light": dict(bg="#f2f5f4", surface="#ffffff", ink="#1b2a2f", muted="#4d5e63", line="#c7d1cf",
                           accent="#0f5c63", accent_soft="#dcebec", on_accent="#ffffff"),
             "dark": dict(bg="#0e1b1d", surface="#152629", ink="#eaf3f2", muted="#a7bdbb", line="#2b4246",
                          accent="#5ed1c8", accent_soft="#1d3a3d", on_accent="#0b1617")},
    "plum": {"light": dict(bg="#f6f2f7", surface="#ffffff", ink="#24172a", muted="#5d4c63", line="#d6c8db",
                           accent="#5b2a6e", accent_soft="#efe3f3", on_accent="#ffffff"),
             "dark": dict(bg="#1a1220", surface="#241929", ink="#f3ecf6", muted="#bfaec7", line="#3d2d45",
                          accent="#d79bf0", accent_soft="#3a2546", on_accent="#1a1220")},
    "amber": {"light": dict(bg="#fbf6ec", surface="#ffffff", ink="#2b2312", muted="#5f5340", line="#e3d6bb",
                            accent="#7a4f00", accent_soft="#f7e6c4", on_accent="#ffffff"),
              "dark": dict(bg="#1c160b", surface="#261e10", ink="#f8f1e3", muted="#c9b896", line="#40331b",
                           accent="#f5b83d", accent_soft="#3d2f12", on_accent="#1c160b")},
    "forest": {"light": dict(bg="#f2f6f2", surface="#ffffff", ink="#1a2a1d", muted="#4a5e4d", line="#c8d6ca",
                             accent="#2f5d3a", accent_soft="#e1efe3", on_accent="#ffffff"),
               "dark": dict(bg="#0f1a12", surface="#16241a", ink="#ecf4ed", muted="#a9bfad", line="#2a3f2f",
                            accent="#8fdc9c", accent_soft="#1f3a25", on_accent="#0f1a12")},
    "charcoal": {"light": dict(bg="#f5f5f4", surface="#ffffff", ink="#1c1c1c", muted="#555555", line="#d4d4d4",
                               accent="#3d3d3d", accent_soft="#ececec", on_accent="#ffffff"),
                 "dark": dict(bg="#121212", surface="#1c1c1c", ink="#f2f2f2", muted="#b3b3b3", line="#333333",
                              accent="#f0d264", accent_soft="#2e2a1a", on_accent="#121212")},
}
# Fonts in site/static/fonts (all OFL: Source Serif 4 incl. its italic, Public Sans, Nunito). "tracking" tightens
# big type; "accent" is the one-word style (Source Serif 4 italic). "round" is Nunito, softer than the others.
FONTS = {
    "serif": dict(head='"Source Serif 4", Georgia, serif', body='"Public Sans", system-ui, sans-serif',
                  tracking="0", accent_family='"Source Serif 4", Georgia, serif', accent_style="italic"),
    "sans": dict(head='"Public Sans", system-ui, sans-serif', body='"Public Sans", system-ui, sans-serif',
                 tracking="-0.02em", accent_family='"Source Serif 4", Georgia, serif', accent_style="italic"),
    "round": dict(head='"Nunito", system-ui, sans-serif', body='"Nunito", system-ui, sans-serif',
                  tracking="-0.01em", accent_family='"Source Serif 4", Georgia, serif', accent_style="italic"),
}
POSITIONS = {"top": "flex-start", "center": "center", "bottom": "flex-end"}
MODES = ("light", "dark")
DESIGN_KEYS = {"palette", "mode", "font", "position", "motion", "sfx"}
DEFAULT_DESIGN = {"palette": "teal", "mode": "light", "font": "serif", "position": "center"}


def design_of(post_or_design) -> dict:
    """The full design: the post's choices over the defaults."""
    d = (post_or_design or {}).get("design", post_or_design) if isinstance(post_or_design, dict) else None
    return {**DEFAULT_DESIGN, **(d if isinstance(d, dict) else {})}


def design_css(design: dict | None = None) -> str:
    """CSS custom properties for a design, for these slides and for reel.py's page."""
    d = design_of(design)
    c, f = PALETTES[d["palette"]][d["mode"]], FONTS[d["font"]]
    names = {"bg": "bg", "surface": "surface", "ink": "ink", "muted": "muted", "line": "line", "accent": "accent",
             "accent_soft": "accent-soft", "on_accent": "on-accent"}
    props = "".join(f"--{names[k]}: {v}; " for k, v in c.items())
    props += (f"--head-font: {f['head']}; --body-font: {f['body']}; --head-tracking: {f['tracking']}; "
              f"--accent-font: {f['accent_family']}; --accent-style: {f['accent_style']}; "
              f"--text-align: {POSITIONS[d['position']]};")
    return (f":root {{ {props} }} "
            ".head, .big { letter-spacing: var(--head-tracking); } "
            ".accent-word { font-family: var(--accent-font); font-style: var(--accent-style); color: var(--accent); }\n")


AI_DISCLOSURE = "AI-generated illustration"


def uses_ai_image(post: dict) -> bool:
    """Whether any slide shows an AI-generated image (its own ai_image, or an image or b-roll marked ai: true)."""
    return any(s.get("ai_image") or (s.get(f) or {}).get("ai") for s in post.get("slides") or [] for f in ("image", "broll")
               if isinstance(s, dict) and isinstance(s.get(f, {}), dict))


def check_ai_images(post: dict) -> list[str]:
    """Rule 9 (owner, 2026-11-19): AI images are decoration only, disclosed in the caption, and always reviewed."""
    if not uses_ai_image(post):
        return []
    errs = []
    if AI_DISCLOSURE.lower() not in (post.get("caption") or "").lower():
        errs.append(f'a post with an AI image says "{AI_DISCLOSURE}" in its caption')
    if post.get("approval") != "review":
        errs.append("a post with an AI image uses approval review")
    return errs


def check_design(post: dict, motions=None) -> list[str]:
    errs, d = [], post.get("design")
    if d is None:
        pass
    elif not isinstance(d, dict):
        errs.append("design is {palette, mode, font, position, motion, sfx}")
    else:
        errs += [f"design: unknown field {k}" for k in d if k not in DESIGN_KEYS]
        for k, allowed in (("palette", PALETTES), ("mode", MODES), ("font", FONTS), ("position", POSITIONS)):
            if k in d and d[k] not in allowed:
                errs.append(f"design {k} is one of {', '.join(allowed)}")
        if "sfx" in d and not isinstance(d["sfx"], bool):
            errs.append("design sfx is true or false")
        if "motion" in d and motions is not None and d["motion"] not in motions:
            errs.append(f"design motion is one of {', '.join(motions)}")
        if ({"motion", "sfx"} & set(d)) and post.get("kind") != "reel":
            errs.append("design motion and sfx are for Reels")
    if post.get("trial") is not None:
        if post.get("trial") not in ("auto", "manual"):
            errs.append("trial is auto or manual")
        elif post.get("kind") != "reel":
            errs.append("trial is for Reels only (Instagram's trial Reels)")
    return errs


def css(width: int, height: int, design: dict | None = None) -> str:
    fonts = (ROOT / "site/static/fonts").as_uri()
    tall = height > 1500
    pad = "230px 72px 420px" if tall else "72px"  # a story or Reel keeps clear of Instagram's top bar and caption
    return design_css(design) + f"""
@font-face {{ font-family: "Public Sans"; src: url("{fonts}/public-sans-latin-400-normal.woff2"); font-weight: 400; }}
@font-face {{ font-family: "Public Sans"; src: url("{fonts}/public-sans-latin-600-normal.woff2"); font-weight: 600; }}
@font-face {{ font-family: "Source Serif 4"; src: url("{fonts}/source-serif-4-latin-700-normal.woff2"); font-weight: 700; }}
@font-face {{ font-family: "Source Serif 4"; src: url("{fonts}/source-serif-4-latin-600-italic.woff2"); font-weight: 600; font-style: italic; }}
@font-face {{ font-family: "Source Serif 4"; src: url("{fonts}/source-serif-4-latin-700-italic.woff2"); font-weight: 700; font-style: italic; }}
@font-face {{ font-family: "Nunito"; src: url("{fonts}/nunito-latin-400-normal.woff2"); font-weight: 400; }}
@font-face {{ font-family: "Nunito"; src: url("{fonts}/nunito-latin-700-normal.woff2"); font-weight: 700; }}
@font-face {{ font-family: "Nunito"; src: url("{fonts}/nunito-latin-800-normal.woff2"); font-weight: 800; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: {width}px; height: {height}px; overflow: hidden; }}
body {{ background: var(--bg); color: var(--ink); font-family: var(--body-font); padding: {pad}; display: flex; }}
.card {{ flex: 1; background: var(--surface); border: 2px solid var(--line); border-radius: 36px; overflow: hidden; display: flex; flex-direction: column; }}
.top {{ background: var(--accent); color: var(--on-accent); padding: 32px 52px; display: flex; justify-content: space-between; align-items: center; gap: 24px; }}
.mark {{ display: flex; align-items: center; gap: 16px; font-family: var(--head-font); font-weight: 700; font-size: 36px; white-space: nowrap; }}
.mark svg {{ width: 52px; height: 52px; }}
.tag {{ font-weight: 600; font-size: 26px; letter-spacing: 2px; text-transform: uppercase; text-align: right; }}
.main {{ flex: 1; padding: 52px 52px 28px; display: flex; flex-direction: column; justify-content: var(--text-align); }}
.chip {{ align-self: flex-start; background: var(--accent-soft); color: var(--accent); font-weight: 600; font-size: 28px; letter-spacing: 3px;
         text-transform: uppercase; padding: 12px 24px; border-radius: 999px; margin-bottom: 24px; }}
.kicker {{ font-weight: 600; font-size: 28px; letter-spacing: 3px; text-transform: uppercase; color: var(--muted); margin-bottom: 16px; }}
.big {{ font-family: var(--head-font); font-weight: 700; font-size: {200 if tall else 180}px; color: var(--accent); line-height: 1; }}
.big.id {{ font-size: 120px; }}
.head {{ font-family: var(--head-font); font-weight: 700; font-size: 62px; line-height: 1.12; margin-top: 24px; }}
.text {{ font-size: 36px; line-height: 1.45; color: var(--muted); margin-top: 28px; }}
.label {{ font-weight: 600; color: var(--ink); }}
.by {{ font-size: 30px; color: var(--muted); margin-top: 28px; }}
.dots {{ display: flex; gap: 12px; margin-top: 32px; }}
.dots i {{ width: 18px; height: 18px; border-radius: 50%; background: var(--line); }}
.dots i.on {{ background: var(--accent); }}
.src {{ border-top: 2px solid var(--line); padding: 26px 52px; font-size: 24px; line-height: 1.4; color: var(--muted); }}
/* band: no card, a full-width teal band carries the number */
body.layout-band {{ padding: 0; flex-direction: column; background: var(--surface); }}
body.layout-band .card {{ border: 0; border-radius: 0; }}
body.layout-band .top {{ padding: {"230px" if tall else "56px"} 72px 40px; }}
body.layout-band .lead {{ background: var(--accent); color: var(--on-accent); padding: 0 72px 56px; display: flex; flex-direction: column; }}
body.layout-band .lead .big, body.layout-band .lead .kicker, body.layout-band .lead .chip {{ color: var(--on-accent); }}
body.layout-band .lead .chip {{ background: rgba(255,255,255,.18); }}
body.layout-band .main {{ padding: 40px 72px; justify-content: var(--text-align); }}
body.layout-band .src {{ padding: 26px 72px {"420px" if tall else "56px"}; }}
/* stat: everything centered around the number */
body.layout-stat .main {{ align-items: center; text-align: center; }}
body.layout-stat .chip {{ align-self: center; }}
body.layout-stat .dots {{ justify-content: center; }}
/* dense: a slide with a lot to say (a bill explainer) steps its type down so the source line still fits */
.dense .head {{ font-size: 50px; }}
.dense .text {{ font-size: 31px; margin-top: 20px; }}
.dense .main {{ padding-top: 36px; padding-bottom: 20px; }}
.dense .kicker, .dense .chip {{ margin-bottom: 12px; }}
/* list: 4-8 rows, one per line */
.list {{ margin-top: 24px; border-top: 2px solid var(--accent-soft); }}
.list .item {{ font-size: 30px; line-height: 1.35; color: var(--ink); padding: 14px 0; border-bottom: 2px solid var(--accent-soft); }}
.list .label {{ color: var(--accent); margin-right: 6px; }}
/* results: alphabetical, one color for every candidate, no winner marked */
.res {{ margin-top: 20px; }}
.res-contest {{ font-weight: 600; font-size: 30px; color: var(--muted); margin-bottom: 10px; }}
.res-row {{ padding: 12px 0; }}
.res-line {{ display: flex; justify-content: space-between; gap: 20px; font-size: 32px; }}
.res-name {{ font-weight: 600; }}
.res-n {{ color: var(--muted); white-space: nowrap; }}
.res-bar {{ height: 16px; background: var(--accent-soft); border-radius: 8px; margin-top: 8px; overflow: hidden; }}
.res-bar i {{ display: block; height: 100%; background: var(--accent); }}
.res-note {{ font-size: 24px; color: var(--muted); margin-top: 14px; }}
.res-outcome {{ font-weight: 600; font-size: 30px; margin-top: 16px; }}
.res-party {{ font-weight: 400; color: var(--muted); }}
/* vote: each count with what it means under it, one style for every side */
.vote {{ display: flex; gap: 18px; margin-top: 28px; }}
.vote-cell {{ flex: 1; background: var(--bg); border-radius: 18px; padding: 22px 12px; text-align: center; }}
.vote-n {{ font-family: var(--head-font); font-weight: 700; font-size: 64px; color: var(--accent); line-height: 1; }}
.vote-l {{ font-size: 26px; color: var(--muted); margin-top: 10px; }}
.swipe {{ font-weight: 600; font-size: 30px; color: var(--accent); margin-top: 28px; }}
.res.compact .res-row {{ padding: 6px 0; }}
.res.compact .res-line {{ font-size: 26px; }}
.res.compact .res-bar {{ height: 10px; margin-top: 4px; }}
"""


LOGO = ('<svg viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#fff"/><path d="M9 16.5l4.5 4.5L23 11.5" '
        'fill="none" stroke="#0f5c63" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def rows(s: dict) -> list[tuple[str, str]]:
    """A slide's rows as (label, text); a plain string is a row with no label."""
    return [("", str(r)) if isinstance(r, str) else (str(r[0]), str(r[1])) for r in s.get("rows") or []]


def check_slide(k: int, s: dict) -> list[str]:
    errs = [f"slide {k}: unknown field {x}" for x in s if x not in SLIDE_KEYS]
    if not (s.get("head") or s.get("big") or s.get("results")):
        errs.append(f"slide {k}: needs head, big, or results")
    r = s.get("rows")
    if r is not None and not (isinstance(r, list) and len(r) <= 8 and all(
            isinstance(x, str) or (isinstance(x, list) and len(x) == 2) for x in r)):
        errs.append(f"slide {k}: rows is a list of up to 8 items, each text or [label, text]")
    res = s.get("results")
    if res is not None:
        cands = res.get("candidates") or []
        errs += [f"slide {k}: results needs {f}" for f in ("contest", "status", "as_of") if not res.get(f)]
        if res.get("status") not in ("unofficial", "official"):
            errs.append(f"slide {k}: results status is unofficial or official, as the report says")
        if not 1 <= len(cands) <= 12:
            errs.append(f"slide {k}: results lists 1-12 candidates or choices, not {len(cands)}")
        if not all(isinstance(c, dict) and c.get("name") and isinstance(c.get("votes"), int) and c["votes"] >= 0
                   and set(c) <= {"name", "votes", "party"} for c in cands):
            errs.append(f"slide {k}: each result is {{name, votes}} (a whole number), plus party only as the ballot lists it")
        elif any("party" in c for c in cands) and not all("party" in c for c in cands):
            # rule 5: party as the ballot lists it, shown the same way for everyone (null where the ballot lists none)
            errs.append(f"slide {k}: give every candidate a party as the ballot lists it (null if it lists none), or none")
        elif not res.get("measure") and [c["name"].casefold() for c in cands] != sorted(c["name"].casefold() for c in cands):
            errs.append(f"slide {k}: results list candidates alphabetically, never by votes (a measure: measure: true)")
        if res.get("measure") not in (None, True, False):
            errs.append(f"slide {k}: measure is true or omitted")
        ov = res.get("other_votes")
        if ov is not None and not (isinstance(ov, int) and ov >= 0):
            errs.append(f"slide {k}: other_votes is a whole number")
        if res.get("outcome") and res.get("status") != "official":
            errs.append(f"slide {k}: an outcome comes only from the official canvass (status official)")
        if set(res) - RESULT_KEYS:
            errs.append(f"slide {k}: results fields are {', '.join(sorted(RESULT_KEYS))}")
    v = s.get("vote")
    if v is not None:
        if not (isinstance(v, dict) and set(v) <= set(VOTE_LABELS) and {"yea", "nay"} <= set(v)
                and all(isinstance(n, int) and n >= 0 for n in v.values())):
            errs.append(f"slide {k}: vote is {{yea, nay, not_voting, present}} as whole numbers, exactly as the record "
                        "gives them; yea and nay required")
    return errs


# what each count means, under the number: the record's word first, then plain English
VOTE_LABELS = {"yea": "Yea (for)", "nay": "Nay (against)", "present": "Present", "not_voting": "Didn't vote"}


def vote_html(v: dict) -> str:
    cells = "".join(f'<div class="vote-cell"><div class="vote-n">{v[key]:,}</div><div class="vote-l">{label}</div></div>'
                    for key, label in VOTE_LABELS.items() if key in v)
    return f'<div class="vote">{cells}</div>'


RESULT_KEYS = {"contest", "status", "as_of", "reporting", "candidates", "measure", "other_votes", "outcome", "note"}


def results_html(res: dict) -> str:
    """A contest's vote counts: alphabetical, one color for everyone, no winner marked; the status says how final."""
    e = html.escape
    lines = [(c["name"], c["votes"], c.get("party")) for c in res["candidates"]]
    if res.get("other_votes"):
        lines.append(("Other write-ins", res["other_votes"], None))
    total = sum(v for _, v, _ in lines) or 1
    out = (f'<div class="res{" compact" if len(lines) > 6 else ""}">'
           f'<div class="res-contest">{e(res["contest"])}</div>')
    for name, votes, party in lines:
        pct = 100 * votes / total
        tag = f' <span class="res-party">{e(party)}</span>' if party else ""  # as the ballot lists it; same style for all
        out += (f'<div class="res-row"><div class="res-line"><span class="res-name">{e(name)}{tag}</span>'
                f'<span class="res-n">{votes:,} · {pct:.1f}%</span></div>'
                f'<div class="res-bar"><i style="width:{pct:.1f}%"></i></div></div>')
    if res.get("outcome"):
        out += f'<div class="res-outcome">{e(res["outcome"])}</div>'
    when = f'{res["status"].capitalize()} results as of {res["as_of"]}' + (f' · {res["reporting"]}' if res.get("reporting") else "")
    note = f'<div class="res-note">{e(res["note"])}</div>' if res.get("note") else ""
    return out + f'<div class="res-note">{e(when)}</div>{note}</div>'


def slide_html(post: dict, s: dict, k: int, n: int, layout: str, size: tuple[int, int]) -> str:
    e = html.escape
    big = s.get("big")
    # characters of running text; past ~300 the type steps down (.dense)
    words = len(" ".join(str(s.get(f) or "") for f in ("head", "text", "by"))) + len(str((s.get("quote") or {}).get("text") or "")) \
        + sum(len(v) for _, v in rows(s)) + 60 * len((s.get("results") or {}).get("candidates") or [])
    lead = ((f'<div class="chip">{e(s["chip"])}</div>' if s.get("chip") else "")
            + (f'<div class="kicker">{e(s["kicker"])}</div>' if s.get("kicker") else "")
            + (f'<div class="big{" id" if big and re.search(r"[A-Za-z]", str(big)) else ""}">{e(str(big))}</div>' if big else ""))
    body = (f'<div class="head">{e(s["head"])}</div>' if s.get("head") else "")
    if s.get("text"):
        body += f'<div class="text">{e(s["text"])}</div>'
    items = rows(s)
    if len(items) > 3:  # a list: compact, one line each, divided
        body += '<div class="list">' + "".join(
            f'<div class="item">{f"<span class=label>{e(label)}</span> " if label else ""}{e(value)}</div>'
            for label, value in items) + "</div>"
    else:
        for label, value in items:
            body += f'<div class="text">{f"<span class=label>{e(label)}:</span> " if label else ""}{e(value)}</div>'
    if s.get("results"):
        body += results_html(s["results"])
    if s.get("vote"):
        body += vote_html(s["vote"])
    if s.get("swipe"):
        body += '<div class="swipe">Swipe to see the bills →</div>'
    if s.get("hint"):
        body += f'<div class="swipe">{e(s["hint"])}</div>'
    if s.get("quote"):
        q = s["quote"]
        body += f'<div class="text"><span class="label">{e(q.get("label", "Official text"))}:</span> “{e(q["text"])}”</div>'
    if s.get("by"):
        body += f'<div class="by">{e(s["by"])}</div>'
    if n > 1:
        body += '<div class="dots">' + "".join(f'<i class="{"on" if j == k else ""}"></i>' for j in range(1, n + 1)) + "</div>"
    tag = level_tag(post) + (f" · {k}/{n}" if n > 1 else "")
    top = f'<div class="top"><span class="mark">{LOGO}Vote the Record</span><span class="tag">{e(tag)}</span></div>'
    if layout == "band" and not lead:
        layout = "card"  # nothing to put in the band (no chip, kicker or number): an empty teal bar looks broken
    if layout == "band":
        main = f'<div class="lead">{lead}</div><div class="main">{body}</div>'
    else:
        main = f'<div class="main">{lead}{body}</div>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{css(*size, post.get("design"))}</style></head>'
            f'<body class="layout-{layout}"><div class="card{" dense" if words > 300 else ""}">{top}{main}<div class="src">{e(source_line(post))}</div></div></body></html>')


def alt_text(post: dict, s: dict) -> str:
    """What the image says, in reading order, for screen readers."""
    q = s.get("quote") or {}
    parts = [level_tag(post), s.get("chip"), s.get("kicker"), s.get("big"), s.get("head"), s.get("text"),
             *(f"{label}: {value}" if label else value for label, value in rows(s)),
             *([s["results"]["contest"]] + [f'{c["name"]}{" (" + c["party"] + ")" if c.get("party") else ""}: {c["votes"]:,} votes' for c in s["results"]["candidates"]]
               + ([f'Other write-ins: {s["results"]["other_votes"]:,} votes'] if s["results"].get("other_votes") else [])
               + ([s["results"]["outcome"]] if s["results"].get("outcome") else [])
               + [f'{s["results"]["status"].capitalize()} results as of {s["results"]["as_of"]}']
               + ([s["results"]["note"]] if s["results"].get("note") else [])
               if s.get("results") else []),
             *(f"{VOTE_LABELS[key]}: {n:,}" for key, n in (s.get("vote") or {}).items()),
             f'{q.get("label", "Official text")}: "{q["text"]}"' if q else None, s.get("by"),
             "Swipe to see the bills." if s.get("swipe") else None, s.get("hint"), source_line(post)]
    return " ".join(str(p) if re.search(r'[.?!"”]$', str(p)) else f"{p}." for p in parts if p)


def chrome() -> str:
    """CHROME_PATH, a known install, or the first Chrome/Chromium on PATH."""
    found = os.environ.get("CHROME_PATH") or next((str(c) for c in CHROME if c.exists()), None) \
        or next(filter(None, map(shutil.which, ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))), None)
    if not found:
        sys.exit("Chrome not found: install Chrome or Chromium, or set CHROME_PATH")
    return found


def render(doc: str, png: Path, size: tuple[int, int], transparent: bool = False) -> None:
    page = png.with_suffix(".html")
    page.write_text(doc, encoding="utf-8")
    profile = png.parent / ".chrome"  # a throwaway profile, so a running Chrome isn't disturbed
    args = [chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            "--disable-dev-shm-usage",  # containers have a tiny /dev/shm; Chrome hangs or crashes without this
            f"--user-data-dir={profile}", f"--window-size={size[0]},{size[1]}", f"--screenshot={png}"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # Chrome won't start its sandbox as root, as in most cloud containers
    if transparent:
        args.append("--default-background-color=00000000")
    try:
        subprocess.run(args + [page.as_uri()], check=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        sys.exit(f"Chrome took over 60 seconds to render {png.name}; check that it runs headless here")
    if not png.exists():
        sys.exit(f"Chrome ran but wrote no {png.name}")


def reel(pngs: list[Path], seconds: list[float], mp4: Path) -> None:
    args, filters, last, offset = [FFMPEG, "-y"], [], "0:v", 0.0
    for png, t in zip(pngs, seconds):
        args += ["-loop", "1", "-framerate", "30", "-t", str(t), "-i", str(png)]
    for k in range(1, len(pngs)):
        offset += seconds[k - 1] - FADE
        filters.append(f"[{last}][{k}:v]xfade=transition=slideleft:duration={FADE}:offset={offset:.2f}[v{k}]")
        last = f"v{k}"
    total = sum(seconds) - FADE * (len(pngs) - 1)
    args += ["-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
             "-filter_complex", ";".join(filters) + f";[{last}]format=yuv420p[out]",
             "-map", "[out]", "-map", f"{len(pngs)}:a", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(mp4)]
    subprocess.run(args, check=True, capture_output=True)


def all_sources(post: dict) -> list[dict]:
    """The post's sources plus each bill's official summary, once each."""
    out = list(post.get("sources") or [])
    for b in post.get("bills") or []:
        s = b.get("summary") or {}
        if s.get("url") and s["url"] not in {x.get("url") for x in out}:
            out.append({"title": s.get("title"), "url": s["url"], "retrieved": s.get("retrieved")})
    if post.get("kind") == "reel":  # the official pages behind a Reel's documents, photos and video
        for s in reel_module().extra_sources(post):
            if s.get("url") not in {x.get("url") for x in out}:
                out.append(s)
    return out


def full_caption(post: dict) -> str:
    srcs = all_sources(post)
    sources = "\n".join(f"{s['title']}: {s['url']}" for s in srcs)
    # what each bill does goes in the caption too: most people read the caption, not every slide
    bills = post.get("bills") or []
    name = lambda b: f"{b['id']} ({b['title']})" if b.get("title") else b["id"]
    if len(bills) > 2:  # a recap: one line per bill, so the caption fits; the slides carry the rest
        about = "\n".join(f"{name(b)}: {b['does']}" for b in bills) + "\n\nWho each affects and where it stands: on the slides."
    else:
        about = "\n\n".join(f"{name(b)}: {b['does']}\nWho it affects: {b['affects']}\nWhere it stands: {b['where']}"
                            for b in bills)
    tags = " ".join("#" + t.lstrip("#") for t in post.get("hashtags") or [])
    return "\n\n".join(p for p in (post["caption"].strip(), about, ("Sources:\n" if len(srcs) > 1 else "Source:\n") + sources, tags) if p)


def build(path: Path) -> Path:
    """Render a post. The full check (editorial rules, duplicates) runs on the pull request with --check; here
    only what rendering needs, so a post the owner already approved still builds after the rules change."""
    post = yaml.safe_load(path.read_text(encoding="utf-8"))
    errs = check(post, editorial=False)
    if errs:
        sys.exit(f"{path}:\n  " + "\n  ".join(errs))
    out = ROOT / "social/out" / path.stem
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    # the layout rotates by post unless the file names one, so the grid doesn't repeat itself
    layout = post.get("layout") or LAYOUTS[int(hashlib.sha1(path.stem.encode()).hexdigest(), 16) % len(LAYOUTS)]
    kind, slides = post["kind"], (expanded(post) if post["kind"] != "clip" else [])
    size = SIZES["tall" if kind in ("story", "reel") else "feed"]
    files, n = [], len(slides)
    if kind == "clip":
        import clip
        files = clip.make(post, out)
    if kind == "reel":
        try:
            import playwright  # noqa: F401  (reel.py's renderer; requirements-reel.txt)
            files, slides = reel_module().render(post, slides, out), []
        except ImportError:
            # until a machine has the Reel tools, a post with no voice, document, photo or video still goes out,
            # as still slides; one that has them can't be shown without them
            if post.get("voice") or any(s.get(f) for s in slides for f in REEL_FIELDS):
                sys.exit(f"{path}: needs the Reel renderer (pip install -r requirements-reel.txt; playwright install chromium)")
            print(f"{path.name}: Reel renderer not installed; building still slides instead", file=sys.stderr)
    for k, s in enumerate(slides, 1):
        png = out / f"slide-{k:02d}.png"
        render(slide_html(post, s, k, n, layout, size), png, size)
        files.append({"file": png.name, "alt_text": alt_text(post, s)})
    if kind == "image" and post.get("story"):
        png = out / "story.png"
        render(slide_html(post, slides[0], 1, 1, layout, SIZES["tall"]), png, SIZES["tall"])
        files.append({"file": png.name, "alt_text": alt_text(post, slides[0]), "story": True})
    if kind == "reel" and slides:  # the still-slide fallback
        reel([out / f["file"] for f in files], [float(s.get("seconds", SECONDS)) for s in slides], out / "reel.mp4")
        shutil.copy(out / files[0]["file"], out / "cover.png")
        files = [{"file": "reel.mp4", "cover": "cover.png", "alt_text": " ".join(f["alt_text"] for f in files)}]
    shutil.rmtree(out / ".chrome", ignore_errors=True)
    for page in out.glob("*.html"):
        page.unlink()
    manifest = {"post": path.stem, "kind": kind, "level": post["level"], "place": post.get("place"),
                "topic": post.get("topic"), "layout": layout, "approval": post["approval"], "files": files,
                "caption": full_caption(post), "sources": all_sources(post),
                "publish_after": str(post["publish_after"]) if post.get("publish_after") else None,
                "record_ids": post.get("record_ids") or [], "found_via": post.get("found_via"),
                "context": post.get("context"), "caption_source": post.get("caption_source"), "video": post.get("video"),
                "highlight": post.get("highlight"), "design": design_of(post), "trial": post.get("trial")}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("posts", type=Path, nargs="+")
    ap.add_argument("--check", action="store_true", help="validate only")
    args = ap.parse_args()
    bad = 0
    for p in args.posts:
        if args.check:
            post = yaml.safe_load(p.read_text(encoding="utf-8"))
            errs = check(post) + duplicates(p, post)
            print(f"{p}: " + ("ok" if not errs else "\n  " + "\n  ".join(errs)))
            bad += bool(errs)
        else:
            print(f"built {build(p)}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
