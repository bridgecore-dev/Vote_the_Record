"""Build an Instagram post (feed image, carousel, story, or Reel) from a post file in social/posts/.

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
KINDS = {"image", "carousel", "story", "reel"}
LEVELS = {"congress", "state", "local"}
LAYOUTS = ["card", "band", "stat"]
SIZES = {"feed": (1080, 1350), "tall": (1080, 1920)}
SECONDS, FADE = 4.0, 0.4  # each Reel slide; the slide transition
SLIDE_KEYS = {"chip", "kicker", "big", "head", "text", "quote", "by", "seconds"}
TOP_KEYS = {"kind", "level", "place", "topic", "approval", "layout", "story", "sources", "slides", "caption",
            "hashtags", "rule", "notes", "publish_after", "found_via", "record_ids"}


def official(url: str) -> bool:
    """Government domains only: what a routine post may rest on without the owner's tap."""
    host = urlparse(url).hostname or ""
    return host.endswith(".gov") or host.endswith(".us") or host.endswith(".mil")


def check(post: dict) -> list[str]:
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
    if post.get("approval") == "auto" and not all(official(s.get("url", "")) for s in sources):
        errs.append("approval auto needs every source on a .gov/.us/.mil domain; use review")
    # found_via: the news item that pointed us to the story; for the reviewer only, never a source
    if post.get("found_via"):
        if post.get("approval") == "auto":
            errs.append("a post found via news uses approval review")
        if post["found_via"] in {s.get("url") for s in sources}:
            errs.append("found_via is not a source; cite the official record instead")
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
    slides = post.get("slides") or []
    n = {"image": (1, 1), "story": (1, 10), "carousel": (2, 10), "reel": (2, 20)}.get(post.get("kind"), (1, 20))
    if not n[0] <= len(slides) <= n[1]:
        errs.append(f"a {post.get('kind')} has {n[0]}-{n[1]} slides, not {len(slides)}")
    for k, s in enumerate(slides, 1):
        errs += [f"slide {k}: unknown field {x}" for x in s if x not in SLIDE_KEYS]
        if not (s.get("head") or s.get("big")):
            errs.append(f"slide {k}: needs head or big")
    if not (post.get("caption") or "").strip():
        errs.append("no caption")
    elif not errs and len(full_caption(post)) > 2200:  # Instagram's limit, counting the sources and hashtags we add
        errs.append(f"caption with sources and hashtags is {len(full_caption(post))} characters; Instagram allows 2,200")
    if len(post.get("hashtags") or []) > 5:
        errs.append("at most 5 hashtags")
    return errs


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
    titles = [s["title"] for s in post["sources"]]
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
body.layout-band .main {{ padding: 56px 72px 28px; justify-content: flex-start; }}
body.layout-band .src {{ padding: 26px 72px {"420px" if tall else "56px"}; }}
/* stat: everything centered around the number */
body.layout-stat .main {{ align-items: center; text-align: center; }}
body.layout-stat .chip {{ align-self: center; }}
body.layout-stat .dots {{ justify-content: center; }}
"""


LOGO = ('<svg viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#fff"/><path d="M9 16.5l4.5 4.5L23 11.5" '
        'fill="none" stroke="#0f5c63" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/></svg>')


def slide_html(post: dict, s: dict, k: int, n: int, layout: str, size: tuple[int, int]) -> str:
    e = html.escape
    big = s.get("big")
    lead = ((f'<div class="chip">{e(s["chip"])}</div>' if s.get("chip") else "")
            + (f'<div class="kicker">{e(s["kicker"])}</div>' if s.get("kicker") else "")
            + (f'<div class="big{" id" if big and re.search(r"[A-Za-z]", str(big)) else ""}">{e(str(big))}</div>' if big else ""))
    body = (f'<div class="head">{e(s["head"])}</div>' if s.get("head") else "")
    if s.get("text"):
        body += f'<div class="text">{e(s["text"])}</div>'
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
            f'<body class="layout-{layout}"><div class="card">{top}{main}<div class="src">{e(source_line(post))}</div></div></body></html>')


def alt_text(post: dict, s: dict) -> str:
    """What the image says, in reading order, for screen readers."""
    q = s.get("quote") or {}
    parts = [level_tag(post), s.get("chip"), s.get("kicker"), s.get("big"), s.get("head"), s.get("text"),
             f'{q.get("label", "Official text")}: "{q["text"]}"' if q else None, s.get("by"), source_line(post)]
    return " ".join(str(p) if re.search(r'[.?!"”]$', str(p)) else f"{p}." for p in parts if p)


def render(doc: str, png: Path, size: tuple[int, int]) -> None:
    chrome = next((c for c in CHROME if c.exists()), None)
    if not chrome:
        sys.exit("Chrome not found")
    page = png.with_suffix(".html")
    page.write_text(doc, encoding="utf-8")
    profile = png.parent / ".chrome"  # a throwaway profile, so a running Chrome isn't disturbed
    subprocess.run([str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                    f"--window-size={size[0]},{size[1]}", f"--screenshot={png}", page.as_uri()],
                   check=True, capture_output=True)


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


def full_caption(post: dict) -> str:
    sources = "\n".join(f"{s['title']}: {s['url']}" for s in post["sources"])
    tags = " ".join("#" + t.lstrip("#") for t in post.get("hashtags") or [])
    return "\n\n".join(p for p in (post["caption"].strip(), ("Sources:\n" if len(post["sources"]) > 1 else "Source:\n") + sources, tags) if p)


def build(path: Path) -> Path:
    post = yaml.safe_load(path.read_text(encoding="utf-8"))
    errs = check(post) + duplicates(path, post)
    if errs:
        sys.exit(f"{path}:\n  " + "\n  ".join(errs))
    out = ROOT / "social/out" / path.stem
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    # the layout rotates by post unless the file names one, so the grid doesn't repeat itself
    layout = post.get("layout") or LAYOUTS[int(hashlib.sha1(path.stem.encode()).hexdigest(), 16) % len(LAYOUTS)]
    kind, slides = post["kind"], post["slides"]
    size = SIZES["tall" if kind in ("story", "reel") else "feed"]
    files, n = [], len(slides)
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
                "caption": full_caption(post), "sources": post["sources"],
                "publish_after": str(post["publish_after"]) if post.get("publish_after") else None,
                "record_ids": post.get("record_ids") or [], "found_via": post.get("found_via")}
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
