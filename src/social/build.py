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
SLIDE_KEYS = {"chip", "kicker", "big", "head", "text", "rows", "results", "quote", "by", "seconds"}  # see social/README.md
TOP_KEYS = {"kind", "level", "place", "topic", "approval", "layout", "story", "sources", "slides", "caption",
            "hashtags", "rule", "notes", "publish_after", "found_via", "record_ids",
            "video", "captions", "caption_source", "speakers", "context",  # these five: clips (clip.py)
            "highlight", "bills"}
# A bill number as written federally or in a state: H.R. 2347, S. 12, H.J.Res. 5, HB 101, SJR 3. Not "U.S. 2026".
BILL = re.compile(r"(?<![A-Za-z.])((?:H\.\s?R|S|H\.\s?J\.\s?Res|S\.\s?J\.\s?Res|H\.\s?Con\.\s?Res|S\.\s?Con\.\s?Res|"
                  r"H\.\s?Res|S\.\s?Res)\.|HB|SB|HJR|SJR|HCR|SCR)\s?(\d+)\b")
BILL_KEYS = {"id", "title", "does", "affects", "where", "summary"}  # title: the official short title, optional
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
    return {"chip": b["id"], "kicker": b.get("title") or "What the bill would do", "head": b["does"],
            "rows": [["Who it affects", b["affects"]], ["Where it stands", b["where"]]],
            "quote": {"label": "Official summary", "text": b["summary"]["text"]}, "seconds": 8}


def expanded(post: dict) -> list[dict]:
    """The slides as published: each bill's explainer right after the first slide that names it."""
    slides = list(post.get("slides") or [])
    first = mentioned_bills(post)
    for b in reversed(post.get("bills") or []):
        at = first.get(bill_key(b.get("id", "")), -1)
        slides.insert(at + 1 if at >= 0 else min(1, len(slides)), explainer(b))
    return slides


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
    errs += check_bills(post, editorial)
    slides = expanded(post) if post.get("kind") != "clip" else []
    n = {"image": (1, 1), "story": (1, 10), "carousel": (2, 10), "reel": (2, 20)}.get(post.get("kind"), (1, 20))
    if post.get("kind") == "clip":
        import clip
        errs += clip.check(post)
    elif not n[0] <= len(slides) <= n[1]:
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


def css(width: int, height: int) -> str:
    fonts = (ROOT / "site/static/fonts").as_uri()
    tall = height > 1500
    pad = "230px 72px 420px" if tall else "72px"  # a story or Reel keeps clear of Instagram's top bar and caption
    return f"""
@font-face {{ font-family: "Public Sans"; src: url("{fonts}/public-sans-latin-400-normal.woff2"); font-weight: 400; }}
@font-face {{ font-family: "Public Sans"; src: url("{fonts}/public-sans-latin-600-normal.woff2"); font-weight: 600; }}
@font-face {{ font-family: "Source Serif 4"; src: url("{fonts}/source-serif-4-latin-700-normal.woff2"); font-weight: 700; }}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: {width}px; height: {height}px; overflow: hidden; }}
body {{ background: #f2f5f4; color: #1b2a2f; font-family: "Public Sans", sans-serif; padding: {pad}; display: flex; }}
.card {{ flex: 1; background: #fff; border: 2px solid #c7d1cf; border-radius: 36px; overflow: hidden; display: flex; flex-direction: column; }}
.top {{ background: #0f5c63; color: #fff; padding: 32px 52px; display: flex; justify-content: space-between; align-items: center; gap: 24px; }}
.mark {{ display: flex; align-items: center; gap: 16px; font-family: "Source Serif 4", serif; font-weight: 700; font-size: 36px; white-space: nowrap; }}
.mark svg {{ width: 52px; height: 52px; }}
.tag {{ font-weight: 600; font-size: 26px; letter-spacing: 2px; text-transform: uppercase; text-align: right; }}
.main {{ flex: 1; padding: 52px 52px 28px; display: flex; flex-direction: column; justify-content: center; }}
.chip {{ align-self: flex-start; background: #dcebec; color: #0f5c63; font-weight: 600; font-size: 28px; letter-spacing: 3px;
         text-transform: uppercase; padding: 12px 24px; border-radius: 999px; margin-bottom: 24px; }}
.kicker {{ font-weight: 600; font-size: 28px; letter-spacing: 3px; text-transform: uppercase; color: #4d5e63; margin-bottom: 16px; }}
.big {{ font-family: "Source Serif 4", serif; font-weight: 700; font-size: {200 if tall else 180}px; color: #0f5c63; line-height: 1; }}
.big.id {{ font-size: 120px; }}
.head {{ font-family: "Source Serif 4", serif; font-weight: 700; font-size: 62px; line-height: 1.12; margin-top: 24px; }}
.text {{ font-size: 36px; line-height: 1.45; color: #4d5e63; margin-top: 28px; }}
.label {{ font-weight: 600; color: #1b2a2f; }}
.by {{ font-size: 30px; color: #4d5e63; margin-top: 28px; }}
.dots {{ display: flex; gap: 12px; margin-top: 32px; }}
.dots i {{ width: 18px; height: 18px; border-radius: 50%; background: #c7d1cf; }}
.dots i.on {{ background: #0f5c63; }}
.src {{ border-top: 2px solid #c7d1cf; padding: 26px 52px; font-size: 24px; line-height: 1.4; color: #4d5e63; }}
/* band: no card, a full-width teal band carries the number */
body.layout-band {{ padding: 0; flex-direction: column; background: #fff; }}
body.layout-band .card {{ border: 0; border-radius: 0; }}
body.layout-band .top {{ padding: {"230px" if tall else "56px"} 72px 40px; }}
body.layout-band .lead {{ background: #0f5c63; color: #fff; padding: 0 72px 56px; display: flex; flex-direction: column; }}
body.layout-band .lead .big, body.layout-band .lead .kicker, body.layout-band .lead .chip {{ color: #fff; }}
body.layout-band .lead .chip {{ background: rgba(255,255,255,.18); }}
body.layout-band .main {{ padding: 40px 72px; justify-content: center; }}
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
.list {{ margin-top: 24px; border-top: 2px solid #dcebec; }}
.list .item {{ font-size: 30px; line-height: 1.35; color: #1b2a2f; padding: 14px 0; border-bottom: 2px solid #dcebec; }}
.list .label {{ color: #0f5c63; margin-right: 6px; }}
/* results: alphabetical, one color for every candidate, no winner marked */
.res {{ margin-top: 20px; }}
.res-contest {{ font-weight: 600; font-size: 30px; color: #4d5e63; margin-bottom: 10px; }}
.res-row {{ padding: 12px 0; }}
.res-line {{ display: flex; justify-content: space-between; gap: 20px; font-size: 32px; }}
.res-name {{ font-weight: 600; }}
.res-n {{ color: #4d5e63; white-space: nowrap; }}
.res-bar {{ height: 16px; background: #eef2f1; border-radius: 8px; margin-top: 8px; overflow: hidden; }}
.res-bar i {{ display: block; height: 100%; background: #0f5c63; }}
.res-note {{ font-size: 24px; color: #4d5e63; margin-top: 14px; }}
.res-outcome {{ font-weight: 600; font-size: 30px; margin-top: 16px; }}
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
                   and set(c) <= {"name", "votes"} for c in cands):
            errs.append(f"slide {k}: each result is {{name, votes}} (a whole number); never a party (rule 5)")
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
    return errs


RESULT_KEYS = {"contest", "status", "as_of", "reporting", "candidates", "measure", "other_votes", "outcome", "note"}


def results_html(res: dict) -> str:
    """A contest's vote counts: alphabetical, one color for everyone, no winner marked; the status says how final."""
    e = html.escape
    lines = [(c["name"], c["votes"]) for c in res["candidates"]]
    if res.get("other_votes"):
        lines.append(("Other write-ins", res["other_votes"]))
    total = sum(v for _, v in lines) or 1
    out = (f'<div class="res{" compact" if len(lines) > 6 else ""}">'
           f'<div class="res-contest">{e(res["contest"])}</div>')
    for name, votes in lines:
        pct = 100 * votes / total
        out += (f'<div class="res-row"><div class="res-line"><span class="res-name">{e(name)}</span>'
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
    if s.get("quote"):
        q = s["quote"]
        body += f'<div class="text"><span class="label">{e(q.get("label", "Official text"))}:</span> “{e(q["text"])}”</div>'
    if s.get("by"):
        body += f'<div class="by">{e(s["by"])}</div>'
    if n > 1:
        body += '<div class="dots">' + "".join(f'<i class="{"on" if j == k else ""}"></i>' for j in range(1, n + 1)) + "</div>"
    tag = level_tag(post) + (f" · {k}/{n}" if n > 1 else "")
    top = f'<div class="top"><span class="mark">{LOGO}Vote the Record</span><span class="tag">{e(tag)}</span></div>'
    if layout == "band":
        main = f'<div class="lead">{lead}</div><div class="main">{body}</div>'
    else:
        main = f'<div class="main">{lead}{body}</div>'
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{css(*size)}</style></head>'
            f'<body class="layout-{layout}"><div class="card{" dense" if words > 300 else ""}">{top}{main}<div class="src">{e(source_line(post))}</div></div></body></html>')


def alt_text(post: dict, s: dict) -> str:
    """What the image says, in reading order, for screen readers."""
    q = s.get("quote") or {}
    parts = [level_tag(post), s.get("chip"), s.get("kicker"), s.get("big"), s.get("head"), s.get("text"),
             *(f"{label}: {value}" if label else value for label, value in rows(s)),
             *([s["results"]["contest"]] + [f'{c["name"]}: {c["votes"]:,} votes' for c in s["results"]["candidates"]]
               + ([f'Other write-ins: {s["results"]["other_votes"]:,} votes'] if s["results"].get("other_votes") else [])
               + ([s["results"]["outcome"]] if s["results"].get("outcome") else [])
               + [f'{s["results"]["status"].capitalize()} results as of {s["results"]["as_of"]}']
               + ([s["results"]["note"]] if s["results"].get("note") else [])
               if s.get("results") else []),
             f'{q.get("label", "Official text")}: "{q["text"]}"' if q else None, s.get("by"), source_line(post)]
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
    for k, s in enumerate(slides, 1):
        png = out / f"slide-{k:02d}.png"
        render(slide_html(post, s, k, n, layout, size), png, size)
        files.append({"file": png.name, "alt_text": alt_text(post, s)})
    if kind == "image" and post.get("story"):
        png = out / "story.png"
        render(slide_html(post, slides[0], 1, 1, layout, SIZES["tall"]), png, SIZES["tall"])
        files.append({"file": png.name, "alt_text": alt_text(post, slides[0]), "story": True})
    if kind == "reel":
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
                "highlight": post.get("highlight")}
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
