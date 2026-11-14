"""Story Highlight covers from social/highlights.yaml.

    python src/social/covers.py

Each cover is a 1080x1920 story image: a simple line drawing centered in the circle Instagram crops to, on the
site's teal. Place covers (counties) carry a short label inside the circle too, since one pin looks like another.
The drawings are our own simple shapes. Output: social/out/covers/<name>.png.
"""
import html
import re
import shutil
from pathlib import Path

import yaml

import build as B

# 24x24 line drawings, stroked in the cover's color
ICONS = {
    "dome": '<path d="M3 21h18M5 21v-7h14v7M8 21v-7M12 21v-7M16 21v-7M4 14h16M6 14a6 6 0 0 1 12 0M12 8V4M10.5 4h3"/>',
    "columns": '<path d="M3 21h18M4 18h16M5 18V10M9.5 18V10M14.5 18V10M19 18V10M3 10h18M12 3l9 5H3z"/>',
    "pin": '<path d="M12 22s7-6.5 7-12a7 7 0 0 0-14 0c0 5.5 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/>',
    "ballot": '<path d="M4 12h16v9H4zM8 12V4h8v8M10 8l1.5 1.5L14 7M9 16h6"/>',
    "book": '<path d="M12 6c-2-1.5-5-2-8-2v15c3 0 6 .5 8 2 2-1.5 5-2 8-2V4c-3 0-6 .5-8 2zM12 6v15"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.5 5.5 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-5.5-3.5-9s1-6.5 3.5-9z"/>',
    "chart": '<path d="M4 20h16M6 20v-6M11 20V9M16 20v-9M5 10l5-4 4 3 6-5"/>',
    "percent": '<path d="M5 19L19 5"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>',
    "heart": '<path d="M12 20s-8-4.7-8-10.5A4.5 4.5 0 0 1 12 7a4.5 4.5 0 0 1 8 2.5C20 15.3 12 20 12 20z"/><path d="M6 12h3l1.5-2.5 2 5 1.5-2.5h4"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "bolt": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "drop": '<path d="M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z"/>',
    "house": '<path d="M3 11l9-7 9 7M5 9.5V20h14V9.5M10 20v-6h4v6"/>',
    "question": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01"/>',
    "calendar": '<path d="M4 6h16v15H4zM4 10h16M8 3v5M16 3v5M8 14h2M14 14h2M8 17.5h2"/>',
    "tally": '<path d="M5 20V9M10 20V5M15 20v-8M20 20V14M3 20h19"/>',
    "play": '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l6-3.5z"/>',
}


def cover_html(icon: str, label: str | None) -> str:
    e = html.escape
    text = f'<div class="label">{e(label)}</div>' if label else ""
    css = (f"{B.css(1080, 1920)} html, body {{ background: #0f5c63; padding: 0; }}"
           " body { align-items: center; justify-content: center; }"
           # Instagram crops the middle of the story to a circle; everything stays inside a 560px one
           " .disc { width: 560px; height: 560px; border-radius: 50%; display: flex; flex-direction: column;"
           " align-items: center; justify-content: center; gap: 18px; }"
           f" .disc svg {{ width: {260 if label else 340}px; height: {260 if label else 340}px; fill: none; stroke: #fff;"
           " stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }"
           " .label { color: #fff; font-family: 'Source Serif 4', serif; font-weight: 700; font-size: 64px; }")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><style>{css}</style></head><body>'
            f'<div class="disc"><svg viewBox="0 0 24 24">{ICONS[icon]}</svg>{text}</div></body></html>')


def main() -> None:
    spec = yaml.safe_load((B.ROOT / "social/highlights.yaml").read_text(encoding="utf-8"))
    out = B.ROOT / "social/out/covers"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    items = [(h, None) for h in spec.get("topics") or []]
    # a county's cover names it: "Harris Co." → "Harris"
    items += [(h, re.sub(r"\s+Co(unty|\.)?$", "", h["name"])) for h in spec.get("places") or []]
    for h, label in items:
        if h["icon"] not in ICONS:
            raise SystemExit(f"{h['name']}: no icon {h['icon']!r}; one of {sorted(ICONS)}")
        slug = re.sub(r"[^a-z0-9]+", "-", h["name"].lower()).strip("-")
        B.render(cover_html(h["icon"], label), out / f"{slug}.png", (1080, 1920))
    shutil.rmtree(out / ".chrome", ignore_errors=True)
    for page in out.glob("*.html"):
        page.unlink()
    print(f"{len(items)} covers in {out}")


if __name__ == "__main__":
    main()
