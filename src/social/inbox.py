"""Post ideas the owner sends to @vote.the.record by direct message, read through the official Messaging API.

The owner sends a note, a shared reel or post, or a video they recorded (e.g. a screen recording of a reel)
from their own account to @vote.the.record. This reads the account's Instagram conversations through the
linked Facebook Page and keeps only messages from the usernames in IG_IDEA_SENDERS. It saves them as
ideas.json, and any video or audio the owner sent under media/, in a PRIVATE bucket (R2_PRIVATE_BUCKET, never
served on r2.dev or any public URL). The scout routine fetches them with --fetch and transcribes videos with
src/social/listen.py (docs/SOCIAL_ROUTINE.md). Read-only on Instagram: it never replies, reacts or marks
anything seen, and never keeps messages from anyone else. Ideas are topic tips; posts rest on official records.

Settings: the same environment as src/social/publish.py, plus
  IG_IDEA_SENDERS    comma-separated Instagram usernames whose messages count as ideas (the owner's accounts)
  R2_PRIVATE_BUCKET  a bucket with public access off; the owner's messages never go in the public one
The token needs instagram_manage_messages, and the account's "Allow access to messages" (Settings > Messages
and story replies > Message controls > Connected tools) must be on.

The repo's Actions logs are public, so with --write nothing personal is printed: only counts. --raw prints
field names, never values. IG_IDEA_SENDERS is a secret (masked in logs); without it the run does nothing.

    python src/social/inbox.py              # print the ideas (local use only)
    python src/social/inbox.py --raw        # print only the field names of kept messages, to check the setup
    python src/social/inbox.py --write      # save ideas.json and new videos to the private bucket
    python src/social/inbox.py --read       # print the saved ideas (the routine, with a read-only key)
    python src/social/inbox.py --fetch DIR  # download ideas.json and the saved videos into DIR (the routine)
"""
import argparse
import datetime as dt
import json
import mimetypes
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import publish  # noqa: E402  (graph(), env(), bucket())

KEY = "ideas.json"
DAYS = 21  # ideas older than this drop off, and their videos are deleted
MAX_BYTES = 300 * 1024 * 1024
ATTACHMENT_FIELDS = "id,mime_type,name,size,image_data,video_data,file_url"
MESSAGE_FIELDS = f"id,created_time,from,message,attachments{{{ATTACHMENT_FIELDS}}},shares"


def page() -> tuple[str, str]:
    """The Facebook Page linked to IG_USER_ID, and its Page token (the conversations API needs one)."""
    ig = publish.env("IG_USER_ID")
    for p in publish.graph("GET", "me/accounts", fields="id,access_token,instagram_business_account").get("data", []):
        if (p.get("instagram_business_account") or {}).get("id") == ig:
            return p["id"], p["access_token"]
    raise RuntimeError("no Page linked to IG_USER_ID is reachable with this token")


def media(message: dict) -> list[tuple[str, str]]:
    """(download URL, mime type) of each video or audio file the sender uploaded; shared reels are not included."""
    out = []
    for a in (message.get("attachments") or {}).get("data", []):
        mime = a.get("mime_type") or ""
        url = (a.get("video_data") or {}).get("url") or (a.get("file_url") if mime.startswith(("video/", "audio/"))
                                                        else None)
        if url:
            out.append((url, mime or "video/mp4"))
    return out


def links(message: dict, skip: set[str]) -> tuple[list[str], list[str]]:
    """The shared item's URLs and titles (a reel's title is its caption), from attachments and shares."""
    urls, titles = [], []
    items = [*(message.get("attachments") or {}).get("data", []), *(message.get("shares") or {}).get("data", [])]
    for item in items:
        for part in (item, item.get("payload") or {}):
            for k in ("url", "link"):
                if part.get(k) and part[k] not in urls and part[k] not in skip:
                    urls.append(part[k])
            for k in ("title", "description"):
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
            files = media(m)
            urls, titles = links(m, {u for u, _ in files})
            out.append({"id": m["id"], "received": when.isoformat(timespec="seconds"), "from": who,
                        "note": m.get("message") or "", "urls": urls, "titles": titles, "_files": files})
    return sorted(out, key=lambda i: i["received"], reverse=True)


def save_media(client, private: str, idea: dict) -> int:
    """Copy the idea's videos into the private bucket once (Instagram's links expire) and list their keys.
    Returns how many failed. A failure is only counted, never printed: errors can carry signed CDN URLs, and
    the Actions logs are public."""
    idea["media"], failed = [], 0
    for n, (url, mime) in enumerate(idea.pop("_files")):
        ext = mimetypes.guess_extension(mime.split(";")[0]) or ".mp4"
        key = f"media/{''.join(c if c.isalnum() else '_' for c in idea['id'][-24:])}-{n}{ext}"
        try:
            try:
                client.head_object(Bucket=private, Key=key)
            except client.exceptions.ClientError:
                with urllib.request.urlopen(url, timeout=300) as r:
                    body = r.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    failed += 1  # too large to transcribe; the note still counts
                    continue
                client.put_object(Bucket=private, Key=key, Body=body, ContentType=mime)
            idea["media"].append(key)
        except Exception:  # noqa: BLE001  (see the docstring)
            failed += 1
    return failed


def prune(client, private: str, keep: set[str]) -> int:
    """Delete saved videos no current idea refers to, so the owner's recordings don't pile up."""
    gone = 0
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=private, Prefix="media/"):
        for obj in page.get("Contents", []):
            if obj["Key"] not in keep:
                client.delete_object(Bucket=private, Key=obj["Key"])
                gone += 1
    return gone


def fetch(into: Path) -> None:
    """Download ideas.json and its videos from the private bucket (read-only key) into a local folder."""
    os.environ.setdefault("R2_BUCKET", publish.env("R2_PRIVATE_BUCKET"))
    client, _ = publish.bucket()
    private = publish.env("R2_PRIVATE_BUCKET")
    into.mkdir(parents=True, exist_ok=True)
    data = client.get_object(Bucket=private, Key=KEY)["Body"].read()
    (into / KEY).write_bytes(data)
    for idea in json.loads(data).get("ideas", []):
        for key in idea.get("media", []):
            target = into / key
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                client.download_file(private, key, str(target))
    print(f"saved {KEY} and its videos under {into}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="save ideas.json and new videos to the private bucket")
    ap.add_argument("--read", action="store_true", help="print the saved ideas.json from the private bucket")
    ap.add_argument("--fetch", type=Path, help="download ideas.json and the saved videos into this folder")
    ap.add_argument("--raw", action="store_true", help="print the field names of each kept message (no values)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.fetch:
        fetch(args.fetch)
        return
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
    found = ideas(senders, now - dt.timedelta(days=DAYS), args.raw)
    if args.write:  # in GitHub Actions: counts only, since the logs are public
        client, _ = publish.bucket()
        private = publish.env("R2_PRIVATE_BUCKET")
        failed = sum(save_media(client, private, idea) for idea in found)
        out = {"generated": now.isoformat(timespec="seconds"), "ideas": found}
        client.put_object(Bucket=private, Key=KEY, Body=json.dumps(out, ensure_ascii=False, indent=2).encode(),
                          ContentType="application/json")
        gone = prune(client, private, {k for i in found for k in i["media"]})
        print(f"{len(found)} idea(s), {sum(len(i['media']) for i in found)} video(s) saved to the private bucket, "
              f"{failed} video(s) failed, {gone} old video(s) deleted")
    else:
        for idea in found:
            idea["media"] = [u for u, _ in idea.pop("_files")]
        print(json.dumps({"generated": now.isoformat(timespec="seconds"), "ideas": found}, ensure_ascii=False,
                         indent=2))


if __name__ == "__main__":
    main()
