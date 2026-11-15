"""Each day's feed alternates Reel, post, Reel, post (owner's request, 2026-11-15; docs/SOCIAL_ROUTINE.md)."""
import datetime as dt
from collections import defaultdict
from pathlib import Path

import yaml

POSTS = Path(__file__).resolve().parents[1] / "social/posts"
FROM = "2026-11-16"  # the first full day on the alternating schedule
REEL, POST = {"reel", "clip"}, {"carousel", "image"}


def schedule() -> dict[str, list[tuple[str, str, str]]]:
    days = defaultdict(list)
    for p in POSTS.glob("*.yaml"):
        d = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        when = d.get("publish_after")
        if when and d.get("kind") in REEL | POST:
            t = dt.datetime.fromisoformat(str(when))
            if t.date().isoformat() >= FROM:
                days[t.date().isoformat()].append((t.isoformat(), "reel" if d["kind"] in REEL else "post", p.stem))
    return days


def test_reels_and_posts_take_turns():
    bad = []
    for day, items in sorted(schedule().items()):
        items.sort()
        for a, b in zip(items, items[1:]):
            if a[1] == b[1]:
                bad.append(f"{day}: two {a[1]}s in a row, {a[2]} then {b[2]}")
    assert not bad, "\n".join(bad)


def test_never_two_posts_at_once():
    seen = defaultdict(list)
    for p in POSTS.glob("*.yaml"):
        when = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("publish_after")
        if when and str(when)[:10] >= FROM:
            seen[dt.datetime.fromisoformat(str(when))].append(p.stem)
    assert not {t: s for t, s in seen.items() if len(s) > 1}
