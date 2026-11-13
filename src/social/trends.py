"""What is getting attention on Instagram, through the official API as @vote.the.record, for the scout routine.

Hashtag search (top posts for a few civic hashtags) and Business Discovery (recent posts of public
professional accounts) only: no scraping, no browsing. Instagram allows 30 different hashtags per rolling
week, so a run uses at most HASHTAGS_PER_RUN, rotating by day. The summary is uploaded to the bucket as
state/trends.json, where the routine reads it (docs/SOCIAL_ROUTINE.md). Trends are a signal for topics
and formats; posts still rest only on official records, and never reuse other accounts' content.

Settings: the same environment as src/social/publish.py.

    python src/social/trends.py            # print the summary
    python src/social/trends.py --write    # also upload it
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (graph(), env(), bucket())

# civic topics only; nothing that names or implies a party
HASHTAGS = ["congress", "txlege", "civics", "uspolitics", "senate", "houseofrepresentatives", "statelegislature",
            "votingrights", "supremecourt", "governmentshutdown", "localgovernment", "citycouncil"]
HASHTAGS_PER_RUN = 4  # 4 a day stays under 30 a week
# public professional accounts to compare formats with; Business Discovery only reaches professional accounts
ACCOUNTS = ["uscapitol", "senategov", "housegov", "texaslegislature"]
FIELDS = "caption,media_type,permalink,like_count,comments_count,timestamp"


def todays(tags: list[str], day: dt.date) -> list[str]:
    start = day.toordinal() * HASHTAGS_PER_RUN % len(tags)
    return [tags[(start + i) % len(tags)] for i in range(HASHTAGS_PER_RUN)]


def trim(media: list[dict], n: int = 5) -> list[dict]:
    """The n posts with the most likes plus comments, captions cut short."""
    ranked = sorted(media, key=lambda m: (m.get("like_count") or 0) + (m.get("comments_count") or 0), reverse=True)
    return [{**{k: m.get(k) for k in ("media_type", "permalink", "like_count", "comments_count", "timestamp")},
             "caption": (m.get("caption") or "")[:300]} for m in ranked[:n]]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="upload state/trends.json to the bucket")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    ig, now = publish.env("IG_USER_ID"), dt.datetime.now(dt.timezone.utc)
    out = {"generated": now.isoformat(timespec="seconds"), "hashtags": {}, "accounts": {}, "problems": []}
    for tag in todays(HASHTAGS, now.date()):
        try:
            found = publish.graph("GET", "ig_hashtag_search", user_id=ig, q=tag).get("data") or []
            if not found:
                continue
            top = publish.graph("GET", f"{found[0]['id']}/top_media", user_id=ig, fields=FIELDS, limit=25)
            out["hashtags"][tag] = trim(top.get("data") or [])
        except RuntimeError as e:
            out["problems"].append(f"#{tag}: {e}")
    for name in ACCOUNTS:
        try:
            bd = publish.graph("GET", ig, fields=f"business_discovery.username({name})"
                               f"{{followers_count,media_count,media.limit(25){{{FIELDS}}}}}")["business_discovery"]
            out["accounts"][name] = {"followers": bd.get("followers_count"),
                                     "top_recent": trim((bd.get("media") or {}).get("data") or [], 3)}
        except (RuntimeError, KeyError) as e:
            out["problems"].append(f"@{name}: {e}")
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.write:
        client, name = publish.bucket()
        client.put_object(Bucket=name, Key="state/trends.json", Body=text.encode(), ContentType="application/json")
        print(f"uploaded {publish.env('R2_PUBLIC_URL').rstrip('/')}/state/trends.json", file=sys.stderr)


if __name__ == "__main__":
    main()
