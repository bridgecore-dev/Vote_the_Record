"""Hearing and floor video clips for src/social/build.py (kind: clip).

A clip is a span of an official video: a title card, the span itself inside our frame with the exact words
burned in as captions and a lower-third naming whoever speaks, then a source card. Captions come from the post
file, never from a model at build time, so the reviewer reads the same words the clip shows.
"""
import html
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import build as B

# Hosts that serve official House and Senate video files outside .gov; add one only after confirming it
# serves the chamber's or committee's own feed.
CLIP_HOSTS: set[str] = set()
BLOCKED = ("youtube.com", "youtu.be", "c-span.org")  # YouTube's terms forbid downloads; C-SPAN footage is theirs
PARTY = re.compile(r"\((?:R|D|I)\b|\b(?:R|D|I)-[A-Z]{2}\b|republican|democrat|independent|\bGOP\b", re.I)
VIDEO_KEYS = {"url", "page", "title", "date", "start", "end"}
TITLE_SECONDS, END_SECONDS, LOWER_SECONDS = 2.5, 3.0, 4.0
W, H = 1080, 1920
BOX = (72, 600, 936, 526)  # where the video sits: x, y, width, height (16:9)
CAPTION_AT, CAPTION_SIZE = (72, 1150), (936, 250)
LOWER_AT, LOWER_SIZE = (72, 600 + 526 - 120), (936, 120)


def seconds(stamp) -> float:
    """'1:02:13.5', '02:13', or a number of seconds."""
    if isinstance(stamp, (int, float)):
        return float(stamp)
    parts = [float(p) for p in str(stamp).split(":")]
    return sum(p * 60 ** k for k, p in enumerate(reversed(parts)))


def stamp(t: float) -> str:
    t = int(round(t))
    return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def host_ok(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host.endswith(".gov") or host in CLIP_HOSTS


def blocked(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == b or host.endswith("." + b) for b in BLOCKED)


def check(post: dict) -> list[str]:
    errs = []
    if post.get("approval") != "review":
        errs.append("a clip always uses approval review")
    if post.get("slides"):
        errs.append("a clip has no slides; its cards come from video")
    v = post.get("video") or {}
    errs += [f"video: unknown field {k}" for k in v if k not in VIDEO_KEYS]
    missing = [k for k in sorted(VIDEO_KEYS) if not v.get(k)]
    if missing:
        return errs + [f"video needs {', '.join(missing)}"]
    for k in ("url", "page"):
        if urlparse(v[k]).scheme != "https":
            errs.append(f"video {k} must be https")
        if blocked(v[k]):
            errs.append(f"video {k}: YouTube and C-SPAN are not used; take the committee's or chamber's own video")
    if not (urlparse(v["page"]).hostname or "").endswith(".gov"):
        errs.append("video page must be the official hearing or floor page on a .gov site")
    if not host_ok(v["url"]):
        errs.append("video url must be on a .gov site or a host in CLIP_HOSTS (src/social/clip.py)")
    if v["page"] not in {s.get("url") for s in post.get("sources") or []}:
        errs.append("list video page among sources")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v["date"])):
        errs.append("video date is YYYY-MM-DD")
    try:
        length = seconds(v["end"]) - seconds(v["start"])
    except ValueError:
        return errs + ["video start and end are H:MM:SS"]
    if not 5 <= length <= 180:
        errs.append(f"a clip runs 5-180 seconds, not {length:.1f}")
    caps = post.get("captions") or []
    if not caps:
        errs.append("captions: the exact words spoken, [{start, end, text}] in seconds from the clip's start")
    last = 0.0
    for k, c in enumerate(caps, 1):
        try:
            a, b = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            errs.append(f"caption {k}: needs start and end in seconds")
            continue
        if not (str(c.get("text") or "").strip()):
            errs.append(f"caption {k}: no text")
        if not (last - 0.01 <= a < b <= length + 0.01):
            errs.append(f"caption {k}: {a}-{b} overlaps, runs backward, or falls outside the clip")
        last = b
    if post.get("caption_source") not in ("official", "whisper"):
        errs.append("caption_source is official (the committee's transcript) or whisper")
    for s in post.get("speakers") or []:
        if not (isinstance(s, dict) and s.get("name") and isinstance(s.get("at"), (int, float))):
            errs.append(f"speaker needs at (seconds) and name: {s}")
        elif PARTY.search(s["name"]):
            errs.append(f"speaker {s['name']!r}: no party, ever (CLAUDE.md rule 5); write 'Rep. Name (ST)'")
        elif not 0 <= s["at"] < length:
            errs.append(f"speaker {s['name']!r}: at falls outside the clip")
    if not str(post.get("context") or "").strip():
        errs.append("context: what was said just before and after, so the reviewer can see the cut keeps its meaning")
    return errs


def page(body: str, extra_css: str = "", transparent: bool = False) -> str:
    bg = "html, body { background: transparent !important; padding: 0 !important; }" if transparent else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{B.css(W, H)}{extra_css}{bg}</style>'
            f"</head><body>{body}</body></html>")


def frame_html(post: dict) -> str:
    """Everything around the video: our card, the hearing's name and time, the source; the video box is black."""
    e, v = html.escape, post["video"]
    css = (f".card {{ position: relative; }} .hear {{ padding: 30px 52px 0; height: {BOX[1] - 230 - 122}px; }}"
           ".hear .head { font-size: 44px; margin-top: 0; } .hear .by { margin-top: 14px; font-size: 28px; }"
           f".video {{ position: absolute; left: 0; top: {BOX[1] - 230}px; width: {BOX[2] - 4}px; height: {BOX[3]}px; background: #000; }}"
           ".card .src { position: absolute; bottom: 0; left: 0; right: 0; background: #fff; }")
    top = (f'<div class="top"><span class="mark">{B.LOGO}Vote the Record</span>'
           f'<span class="tag">{e(B.level_tag(post))}</span></div>')
    hear = (f'<div class="hear"><div class="head">{e(v["title"])}</div>'
            f'<div class="by">{e(v["date"])} · at {stamp(seconds(v["start"]))} in the full video</div></div>')
    return page(f'<div class="card">{top}{hear}<div class="video"></div>'
                f'<div class="src">{e(B.source_line(post))}</div></div>', css)


def caption_html(text: str) -> str:
    css = (f"html, body {{ width: {CAPTION_SIZE[0]}px; height: {CAPTION_SIZE[1]}px; }}"
           ".cap { width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; padding: 0 44px;"
           " text-align: center; font-weight: 600; font-size: 44px; line-height: 1.3; color: #1b2a2f; }")
    return page(f'<div class="cap">{html.escape(text)}</div>', css, transparent=True)


def lower_html(name: str) -> str:
    css = (f"html, body {{ width: {LOWER_SIZE[0]}px; height: {LOWER_SIZE[1]}px; }}"
           ".low { position: absolute; left: 24px; bottom: 20px; background: #0f5c63; color: #fff; font-weight: 600;"
           " font-size: 34px; padding: 14px 26px; border-radius: 12px; }")
    return page(f'<div class="low">{html.escape(name)}</div>', css, transparent=True)


def render_png(doc: str, png: Path, size: tuple[int, int], transparent: bool = False) -> None:
    chrome = next((c for c in B.CHROME if c.exists()), None)
    p = png.with_suffix(".html")
    p.write_text(doc, encoding="utf-8")
    args = [str(chrome), "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={png.parent / '.chrome'}",
            f"--window-size={size[0]},{size[1]}", f"--screenshot={png}"]
    if transparent:
        args.append("--default-background-color=00000000")
    subprocess.run(args + [p.as_uri()], check=True, capture_output=True)


def has_audio(path: Path) -> bool:
    probe = shutil.which("ffprobe") or str(Path(B.FFMPEG).with_name("ffprobe.exe"))
    out = subprocess.run([probe, "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True)
    return bool(out.stdout.strip())


def make(post: dict, out: Path) -> list[dict]:
    """Render the clip into out/; the manifest's files entry."""
    v = post["video"]
    start, end = seconds(v["start"]), seconds(v["end"])
    length = end - start
    # 1. just the span, re-encoded so the cut lands on the exact frame
    seg = out / "segment.mp4"
    subprocess.run([B.FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", v["url"], "-t", f"{length:.3f}", "-r", "30",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(seg)],
                   check=True, capture_output=True)
    # 2. our frame, the captions, the lower-thirds, and the title and source cards, as PNGs
    render_png(frame_html(post), out / "frame.png", (W, H))
    caps = post["captions"]
    for k, c in enumerate(caps):
        render_png(caption_html(c["text"]), out / f"cap-{k:03d}.png", CAPTION_SIZE, transparent=True)
    lows = post.get("speakers") or []
    for k, s in enumerate(lows):
        render_png(lower_html(s["name"]), out / f"low-{k:02d}.png", LOWER_SIZE, transparent=True)
    title = {"kicker": "Hearing clip" if "hearing" in v["title"].lower() else "On the record",
             "head": v["title"], "text": f'{v["date"]} · at {stamp(start)} in the full video'}
    B.render(B.slide_html(post, title, 1, 1, "card", (W, H)), out / "cover.png", (W, H))
    end_card = {"kicker": "Watch the full video", "head": "The whole record, unedited, is on the official page.",
                "text": v["page"]}
    B.render(B.slide_html(post, end_card, 1, 1, "card", (W, H)), out / "end.png", (W, H))
    # 3. one pass: frame + video + timed overlays, then title card, clip, source card back to back
    args = [B.FFMPEG, "-y", "-loop", "1", "-framerate", "30", "-t", f"{length:.3f}", "-i", str(out / "frame.png"),
            "-i", str(seg)]
    overlays = [(out / f"cap-{k:03d}.png", CAPTION_AT, float(c["start"]), float(c["end"])) for k, c in enumerate(caps)]
    overlays += [(out / f"low-{k:02d}.png", LOWER_AT, float(s["at"]), min(float(s["at"]) + LOWER_SECONDS, length))
                 for k, s in enumerate(lows)]
    for png, *_ in overlays:
        args += ["-i", str(png)]
    n = 2 + len(overlays)
    args += ["-loop", "1", "-framerate", "30", "-t", str(TITLE_SECONDS), "-i", str(out / "cover.png"),
             "-loop", "1", "-framerate", "30", "-t", str(END_SECONDS), "-i", str(out / "end.png"),
             "-f", "lavfi", "-t", str(TITLE_SECONDS + END_SECONDS + length), "-i", "anullsrc=r=44100:cl=stereo"]
    x, y, w, h = BOX
    f = [f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[vid]",
         f"[0:v][vid]overlay={x}:{y}[b0]"]
    for k, (_, (ox, oy), a, b) in enumerate(overlays):
        f.append(f"[b{k}][{2 + k}:v]overlay={ox}:{oy}:enable='between(t,{a:.3f},{b:.3f})'[b{k + 1}]")
    main = f"b{len(overlays)}"
    silence = n + 2
    f.append(f"[{silence}:a]asplit=3[s1][s2][s3]")
    f.append(f"[s1]atrim=0:{TITLE_SECONDS},asetpts=PTS-STARTPTS[a0]")
    f.append(f"[s3]atrim=0:{END_SECONDS},asetpts=PTS-STARTPTS[a2]")
    if has_audio(seg):
        f.append("[1:a]aresample=44100,asetpts=PTS-STARTPTS[a1]")
        f.append("[s2]anullsink")
    else:
        f.append(f"[s2]atrim=0:{length:.3f},asetpts=PTS-STARTPTS[a1]")
    f.append(f"[{n}:v]setsar=1[t0];[{main}]setsar=1[t1];[{n + 1}:v]setsar=1[t2]")
    f.append("[t0][a0][t1][a1][t2][a2]concat=n=3:v=1:a=1[cv][ca];[cv]format=yuv420p[ov]")
    args += ["-filter_complex", ";".join(f), "-map", "[ov]", "-map", "[ca]", "-c:v", "libx264", "-preset", "medium",
             "-crf", "18", "-c:a", "aac", "-movflags", "+faststart", str(out / "clip.mp4")]
    subprocess.run(args, check=True, capture_output=True)
    for p in [seg, out / "frame.png", out / "end.png", *out.glob("cap-*.png"), *out.glob("low-*.png")]:
        p.unlink(missing_ok=True)
    words = " ".join(c["text"].strip() for c in caps)
    who = "; ".join(s["name"] for s in lows)
    alt = (f"Video clip from {v['title']}, {v['date']}, at {stamp(start)} in the full video."
           + (f" Speaking: {who}." if who else "") + f" Transcript: {words} {B.source_line(post)}.")
    return [{"file": "clip.mp4", "cover": "cover.png", "alt_text": alt}]
