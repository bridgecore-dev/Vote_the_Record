"""WCAG AA contrast for the color tokens in site/static/css/site.css, light and dark."""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).resolve().parents[1] / "site" / "static" / "css" / "site.css").read_text(encoding="utf-8")

# (foreground, background, minimum ratio). 4.5 for text, 3.0 for UI boundaries and focus rings.
PAIRS = [
    ("ink", "bg", 4.5), ("ink", "surface", 4.5),
    ("muted", "bg", 4.5), ("muted", "surface", 4.5),
    ("accent", "bg", 4.5), ("accent", "surface", 4.5),
    ("on-accent", "accent", 4.5),
    ("warn-ink", "warn-bg", 4.5),
    ("demo-ink", "demo-bg", 4.5),
    ("control", "bg", 3.0), ("control", "surface", 3.0),
    ("focus", "bg", 3.0), ("focus", "surface", 3.0),
]


def tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6})", block))


def themes() -> dict[str, dict[str, str]]:
    light = re.search(r"^:root\s*\{(.*?)\}", CSS, re.S | re.M).group(1)
    dark = re.search(r"prefers-color-scheme:\s*dark\)\s*\{\s*:root[^{]*\{(.*?)\}", CSS, re.S).group(1)
    return {"light": tokens(light), "dark": tokens(dark)}


def pressed_dark() -> dict[str, str]:
    """The tokens the Night button applies (:root[data-theme="dark"])."""
    return tokens(re.search(r'^:root\[data-theme="dark"\]\s*\{(.*?)\}', CSS, re.S | re.M).group(1))


def luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("fg,bg,minimum", PAIRS)
def test_contrast(theme, fg, bg, minimum):
    t = themes()[theme]
    value = ratio(t[fg], t[bg])
    assert value >= minimum, f"{theme}: --{fg} on --{bg} is {value:.2f}:1, needs {minimum}:1"


def test_both_themes_define_the_same_tokens():
    t = themes()
    assert set(t["light"]) == set(t["dark"])


def test_night_button_uses_the_same_dark_tokens():
    assert pressed_dark() == themes()["dark"]
