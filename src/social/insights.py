"""What's working: a report on our own published posts, from Instagram's official insights.

    python src/social/insights.py [--days 7] [--out report.md]

For every post recorded as published in the last --days (the state file in the bucket, so extra copies of a
post never count), this reads its insights through the Graph API (reach, views, saves, shares, likes,
comments; average watch time for Reels) and prints a Markdown report: totals, then the same numbers by format,
level, topic, layout, and time of day, then the posts that did best. Rates are per 100 accounts reached, so a
post seen by more people doesn't win just for that. It reads only; it never posts. Needs the token to have
instagram_manage_insights; without it the report says so.
"""
import argparse
import datetime as dt
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (graph(), env(), bucket(), load_state(), POSTS)

METRICS = {  # what the Graph API reports for each kind of media; "views" covers every kind since v22
    "reel": ["reach", "views", "saved", "shares", "likes", "comments", "ig_reels_avg_watch_time"],
    "feed": ["reach", "views", "saved", "shares", "likes", "comments"],
    "story": ["reach", "views", "shares", "replies"],
}
RATES = ["saved", "shares", "comments", "likes"]  # shown per 100 reached


def media_kind(post_kind: str) -> str:
    return {"reel": "reel", "clip": "reel", "story": "story"}.get(post_kind, "feed")


def insights(media_id: str, kind: str) -> dict:
    """The media's numbers, by metric name. A metric the API won't give for this media is left out."""
    out, wanted = {}, METRICS[kind]
    try:
        data = publish.graph("GET", f"{media_id}/insights", metric=",".join(wanted))["data"]
        return {d["name"]: (d.get("values") or [{}])[0].get("value", d.get("total_value", {}).get("value")) for d in data}
    except RuntimeError:
        for m in wanted:  # one bad metric fails the whole request; ask one at a time instead
            try:
                d = publish.graph("GET", f"{media_id}/insights", metric=m)["data"][0]
                out[m] = (d.get("values") or [{}])[0].get("value")
            except (RuntimeError, IndexError, KeyError):
                pass
    return out


def rows(state: dict, posts: dict[str, dict], since: dt.datetime) -> list[dict]:
    """One row per published post in the window: its post-file facts and its numbers."""
    out = []
    for stem, rec in sorted(state.items()):
        when = rec.get("published")
        ids = [i for i in rec.get("media") or [] if not str(i).startswith("pending:")]
        if not (when and ids and stem in posts) or dt.datetime.fromisoformat(when) < since:
            continue
        post = posts[stem]
        kind = media_kind(post.get("kind", ""))
        out.append({"stem": stem, "kind": post.get("kind"), "level": post.get("level"), "topic": post.get("topic") or "—",
                    "layout": layout(stem, post), "hour": hour_band(when), "permalink": rec.get("permalink"),
                    **insights(str(ids[0]), kind)})
    return out


def layout(stem: str, post: dict) -> str:
    """The slide layout the post got: its own, or the one build.py rotates to by post name. Reels draw their own."""
    if post.get("kind") in ("reel", "clip"):
        return "motion"
    names = ["card", "band", "stat"]  # build.LAYOUTS, in order
    return post.get("layout") or names[int(hashlib.sha1(stem.encode()).hexdigest(), 16) % len(names)]


def hour_band(iso: str) -> str:
    """Central time, in four bands."""
    h = (dt.datetime.fromisoformat(iso).astimezone(dt.timezone(dt.timedelta(hours=-6)))).hour
    return "morning (5-11)" if 5 <= h < 11 else "midday (11-16)" if h < 16 else "evening (16-21)" if h < 21 else "night"


def per100(n, reach) -> float | None:
    return round(100 * n / reach, 2) if reach and n is not None else None


def table(rs: list[dict], by: str) -> list[str]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rs:
        groups[str(r.get(by))].append(r)
    lines = [f"| {by} | posts | avg reach | " + " | ".join(f"{m} /100" for m in RATES) + " |",
             "|---|---:|---:|" + "---:|" * len(RATES)]
    for name, g in sorted(groups.items(), key=lambda kv: -sum(r.get("reach") or 0 for r in kv[1]) / len(kv[1])):
        reach = sum(r.get("reach") or 0 for r in g)
        cells = [per100(sum(r.get(m) or 0 for r in g), reach) for m in RATES]
        lines.append(f"| {name} | {len(g)} | {reach // len(g):,} | " + " | ".join("—" if c is None else f"{c}" for c in cells) + " |")
    return lines


def report(rs: list[dict], days: int, followers: int | None) -> str:
    if not rs:
        return f"# What's working: last {days} days\n\nNo published posts with insights in this window.\n"
    reach = sum(r.get("reach") or 0 for r in rs)
    out = [f"# What's working: last {days} days", "",
           f"{len(rs)} posts, {reach:,} accounts reached in total" + (f", {followers:,} followers now." if followers is not None else "."),
           "Rates are per 100 accounts reached, so bigger posts don't win just for their size. Our own posts only.", ""]
    for by, title in [("kind", "By format"), ("level", "By level"), ("topic", "By topic"), ("layout", "By layout"),
                      ("hour", "By time of day (Central)")]:
        out += [f"## {title}", "", *table(rs, by), ""]
    reels = [r for r in rs if r.get("ig_reels_avg_watch_time") is not None]
    if reels:
        avg = sum(r["ig_reels_avg_watch_time"] for r in reels) / len(reels) / 1000
        out += [f"Reels: average watch time {avg:.1f} s across {len(reels)} Reels.", ""]
    def score(r: dict, metric: str) -> float:
        return (r.get("reach") or 0) if metric == "reach" else (per100(r.get(metric), r.get("reach")) or 0)

    for metric, title in [("shares", "Most shared (per 100 reached)"), ("saved", "Most saved (per 100 reached)"),
                          ("reach", "Widest reach")]:
        rank = sorted(rs, key=lambda r: -score(r, metric))
        out += [f"## {title}", ""]
        for r in rank[:5]:
            val = f"{r.get('reach') or 0:,} reached" if metric == "reach" else f"{per100(r.get(metric), r.get('reach')) or 0} per 100"
            out.append(f"- {r['stem']} ({r['kind']}): {val}" + (f" — {r['permalink']}" if r.get("permalink") else ""))
        out.append("")
    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", type=Path, help="also write the report here")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    client, name = publish.bucket()
    state = publish.load_state(client, name)
    posts = {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) or {} for p in publish.POSTS.glob("*.yaml")}
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.days)
    try:
        followers = publish.graph("GET", publish.env("IG_USER_ID"), fields="followers_count").get("followers_count")
        rs = rows(state, posts, since)
    except RuntimeError as e:
        sys.exit(f"Instagram refused the insights request (the token needs instagram_manage_insights): {str(e)[:300]}")
    text = report(rs, args.days, followers)
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
