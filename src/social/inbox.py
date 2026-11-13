"""Post ideas the owner shares to @vote.the.record by direct message, read through the official Messaging API.

The owner shares a reel or post (with a note, if they like) from their own account to @vote.the.record. This
reads the account's Instagram conversations through the linked Facebook Page, keeps only messages from the
usernames in IG_IDEA_SENDERS, and uploads them to the bucket as state/ideas.json for the scout routine
(docs/SOCIAL_ROUTINE.md). Read-only: it never replies, reacts or marks anything seen, and never keeps
messages from anyone else. A shared reel is a topic tip only; posts still rest on official records.

Settings: the same environment as src/social/publish.py, plus
  IG_IDEA_SENDERS  comma-separated Instagram usernames whose shares count as ideas (the owner's own accounts)
The token needs instagram_manage_messages and pages_manage_metadata, and the account's "Allow access to
messages" (Settings > Messages and story replies > Message controls > Connected tools) must be on.

    python src/social/inbox.py            # print the ideas
    python src/social/inbox.py --raw      # print the raw fields of the newest messages, to check the setup
    python src/social/inbox.py --write    # also upload state/ideas.json
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (graph(), env(), bucket())

DAYS = 21  # ideas older than this drop off
MESSAGE_FIELDS = "id,created_time,from,message,attachments,shares"


def page() -> tuple[str, str]:
    """The Facebook Page linked to IG_USER_ID, and its Page token (the conversations API needs one)."""
    ig = publish.env("IG_USER_ID")
    for p in publish.graph("GET", "me/accounts", fields="id,access_token,instagram_business_account").get("data", []):
        if (p.get("instagram_business_account") or {}).get("id") == ig:
            return p["id"], p["access_token"]
    raise RuntimeError("no Page linked to IG_USER_ID is reachable with this token")


def links(message: dict) -> tuple[list[str], list[str]]:
    """The shared item's URLs and titles (a reel's title is its caption), from attachments and shares."""
    urls, titles = [], []
    items = [*(message.get("attachments") or {}).get("data", []), *(message.get("shares") or {}).get("data", [])]
    for item in items:
        for part in (item, item.get("payload") or {}):
            for k in ("url", "link"):
                if part.get(k) and part[k] not in urls:
                    urls.append(part[k])
            for k in ("title", "name", "description"):
                if part.get(k) and part[k] not in titles:
                    titles.append(part[k][:600])
    return urls, titles


def ideas(senders: set[str], since: dt.datetime, raw: bool = False) -> list[dict]:
    page_id, token = page()
    convs = publish.graph("GET", f"{page_id}/conversations", platform="instagram", access_token=token,
                          fields=f"updated_time,messages.limit(25){{{MESSAGE_FIELDS}}}", limit=25)
    out = []
    for conv in convs.get("data", []):
        for m in (conv.get("messages") or {}).get("data", []):
            who = ((m.get("from") or {}).get("username") or "").lower()
            if who not in senders:
                continue  # never keep anyone else's messages
            when = dt.datetime.fromisoformat(m["created_time"].replace("+0000", "+00:00"))
            if when < since:
                continue
            if raw:
                print(json.dumps(m, ensure_ascii=False, indent=2))
            urls, titles = links(m)
            out.append({"received": when.isoformat(timespec="seconds"), "from": who,
                        "note": m.get("message") or "", "urls": urls, "titles": titles})
    return sorted(out, key=lambda i: i["received"], reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="upload state/ideas.json to the bucket")
    ap.add_argument("--raw", action="store_true", help="also print the raw fields of each kept message")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    senders = {s.strip().lstrip("@").lower() for s in publish.env("IG_IDEA_SENDERS").split(",") if s.strip()}
    now = dt.datetime.now(dt.timezone.utc)
    out = {"generated": now.isoformat(timespec="seconds"), "ideas": ideas(senders, now - dt.timedelta(days=DAYS),
                                                                          args.raw)}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if args.write:
        client, name = publish.bucket()
        client.put_object(Bucket=name, Key="state/ideas.json", Body=text.encode(), ContentType="application/json")
        print(f"uploaded {publish.env('R2_PUBLIC_URL').rstrip('/')}/state/ideas.json", file=sys.stderr)


if __name__ == "__main__":
    main()
