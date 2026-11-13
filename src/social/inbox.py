"""Post ideas the owner shares to @vote.the.record by direct message, read through the official Messaging API.

The owner shares a reel or post (with a note, if they like) from their own account to @vote.the.record. This
reads the account's Instagram conversations through the linked Facebook Page, keeps only messages from the
usernames in IG_IDEA_SENDERS, and saves them as ideas.json in a PRIVATE bucket (R2_PRIVATE_BUCKET, never
served on r2.dev or any public URL), where the scout routine reads them with --read (docs/SOCIAL_ROUTINE.md). Read-only: it never replies, reacts or marks anything seen, and never keeps
messages from anyone else. A shared reel is a topic tip only; posts still rest on official records.

Settings: the same environment as src/social/publish.py, plus
  IG_IDEA_SENDERS    comma-separated Instagram usernames whose shares count as ideas (the owner's own accounts)
  R2_PRIVATE_BUCKET  a bucket with public access off; the owner's messages never go in the public one
The token needs instagram_manage_messages and pages_manage_metadata, and the account's "Allow access to
messages" (Settings > Messages and story replies > Message controls > Connected tools) must be on.

    python src/social/inbox.py            # print the ideas (local use only)
    python src/social/inbox.py --raw      # print only the field names of kept messages, to check the setup

The repo's Actions logs are public, so with --write nothing personal is printed: only a count. Field values
are never printed by --raw. IG_IDEA_SENDERS is a secret (masked in logs); without it the run does nothing.
    python src/social/inbox.py --write    # also save ideas.json to the private bucket
    python src/social/inbox.py --read     # print the saved ideas (the routine, with a read-only key)
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (graph(), env(), bucket())

KEY = "ideas.json"
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


def shape(value):
    """The field names and types of a message, with every value removed."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


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
            if raw:  # shape only, never values: the Actions logs are public
                print(json.dumps(shape(m)))
            urls, titles = links(m)
            out.append({"received": when.isoformat(timespec="seconds"), "from": who,
                        "note": m.get("message") or "", "urls": urls, "titles": titles})
    return sorted(out, key=lambda i: i["received"], reverse=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="save ideas.json to the private bucket")
    ap.add_argument("--read", action="store_true", help="print the saved ideas.json from the private bucket")
    ap.add_argument("--raw", action="store_true", help="print the field names of each kept message (no values)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.read:  # the routine's environment has only the private bucket's read-only key
        os.environ.setdefault("R2_BUCKET", publish.env("R2_PRIVATE_BUCKET"))
        client, _ = publish.bucket()
        print(client.get_object(Bucket=publish.env("R2_PRIVATE_BUCKET"), Key=KEY)["Body"].read().decode())
        return
    if not os.environ.get("IG_IDEA_SENDERS"):
        print("IG_IDEA_SENDERS is not set; nothing to read")
        return
    senders = {s.strip().lstrip("@").lower() for s in publish.env("IG_IDEA_SENDERS").split(",") if s.strip()}
    now = dt.datetime.now(dt.timezone.utc)
    out = {"generated": now.isoformat(timespec="seconds"), "ideas": ideas(senders, now - dt.timedelta(days=DAYS),
                                                                          args.raw)}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.write:  # in GitHub Actions: a count only, since the logs are public
        print(f"{len(out['ideas'])} idea(s)")
        client, _ = publish.bucket()
        private = publish.env("R2_PRIVATE_BUCKET")
        client.put_object(Bucket=private, Key=KEY, Body=text.encode(), ContentType="application/json")
        print(f"saved {KEY} to the private bucket", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
