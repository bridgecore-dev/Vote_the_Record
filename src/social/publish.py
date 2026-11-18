"""Publish merged Instagram posts whose time has come: build, upload to R2, post through the official Graph API.

Preview by default (lists what is due); --write publishes. A post is due when it is in social/posts/ (merged
to main, so approved), not yet published, and its publish_after, if any, has passed. Which posts are published
is kept in the bucket (state/published.json), so publishing never commits to the repo.

Settings come from the environment (GitHub secrets or a local .env; never committed):
  IG_USER_ID, IG_ACCESS_TOKEN                      the Instagram professional account and its token
  IG_GRAPH_HOST    graph.facebook.com (default; Facebook Login) or graph.instagram.com (Instagram Login)
  IG_GRAPH_VERSION API version, e.g. v23.0 (default)
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
  R2_PUBLIC_URL    the bucket's public base URL; Instagram fetches the files from here

    python src/social/publish.py                 # what would publish now
    python src/social/publish.py --write         # publish up to --limit posts
    python src/social/publish.py --preview <post.yaml>...   # upload built files under previews/, print URLs
    python src/social/publish.py --take-down <post>          # delete a published post from Instagram (file name, no .yaml)
    python src/social/publish.py --list-media    # what is on the account, matched to post files; posts nothing

Never twice (2026-11-18: retries after errors posted the same post many times):
- Before posting, recent media on the account is checked for the same caption; a match is recorded, not reposted.
- A post is recorded the moment its feed post publishes; story frames that fail afterward are reported, not retried.
- When Instagram says a limit was reached, the run stops and publishing pauses for PAUSE (state/publish-pause.json).
- One post per run, and a post more than LATE past its slot is not posted: it is listed as missed, to reschedule.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
POSTS = ROOT / "social/posts"
STATE_KEY = "state/published.json"
PAUSE_KEY = "state/publish-pause.json"
PAUSE = dt.timedelta(hours=2)
LATE = dt.timedelta(hours=3)
LIMIT_CODES = {4, 9, 17, 32, 613}  # Graph API rate and publishing limits
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".json": "application/json"}


def slot(p: Path) -> dt.datetime | None:
    when = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("publish_after")
    return dt.datetime.fromisoformat(str(when)) if when else None


EARLIEST = dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def due(posts: list[Path], published: dict, now: dt.datetime, late: dt.timedelta | None = None) -> list[Path]:
    """Posts not yet published whose publish_after has passed (or that have none), earliest slot first.
    With `late`, a post whose slot passed more than that long ago is left out (see missed())."""
    out = []
    for p in posts:
        if p.stem in published:
            continue
        when = slot(p)
        if when and (when > now or (late and now - when > late)):
            continue
        out.append(p)
    return sorted(out, key=lambda p: (slot(p) or EARLIEST, p.stem))


def missed(posts: list[Path], published: dict, now: dt.datetime, late: dt.timedelta) -> list[Path]:
    """Unpublished posts whose slot passed more than `late` ago: they need a new publish_after."""
    return sorted(p for p in posts if p.stem not in published and (w := slot(p)) and now - w > late)


def limited(err: str) -> bool:
    """Whether a Graph API error says a rate or publishing limit was reached."""
    m = re.search(r'"code":\s*(\d+)', err)
    return bool(m and int(m.group(1)) in LIMIT_CODES)


def key(caption: str) -> str:
    """How captions are compared: the first 150 characters, spacing and case ignored."""
    return " ".join((caption or "").split()).lower()[:150]


def media_list(most: int = 50) -> list[dict]:
    """The account's newest media, newest first."""
    out, after = [], None
    while len(out) < most:
        page = graph("GET", f"{env('IG_USER_ID')}/media", limit=min(50, most - len(out)),
                     fields="id,caption,timestamp,permalink,media_product_type", **({"after": after} if after else {}))
        out += page.get("data", [])
        after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
        if not page.get("data") or not (page.get("paging") or {}).get("next"):
            break
    return out


def already_up(caption: str, media: list[dict]) -> dict | None:
    """The oldest media already on the account with this caption, if any."""
    same = [m for m in media if key(m.get("caption", "")) == key(caption)]
    return min(same, key=lambda m: m.get("timestamp", "")) if same else None


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default  # an unset GitHub variable arrives as ""
    if not value:
        sys.exit(f"{name} is not set (see the top of src/social/publish.py)")
    return value


def bucket():
    import boto3  # requirements-social.txt; only the publishing step needs it
    client = boto3.client("s3", endpoint_url=f"https://{env('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
                          aws_access_key_id=env("R2_ACCESS_KEY_ID"), aws_secret_access_key=env("R2_SECRET_ACCESS_KEY"),
                          region_name="auto")
    return client, env("R2_BUCKET")


def load_json(client, name: str, key_: str) -> dict:
    try:
        return json.loads(client.get_object(Bucket=name, Key=key_)["Body"].read())
    except client.exceptions.NoSuchKey:
        return {}


def load_state(client, name: str) -> dict:
    try:
        return json.loads(client.get_object(Bucket=name, Key=STATE_KEY)["Body"].read())
    except client.exceptions.NoSuchKey:
        return {}


def upload(client, name: str, out: Path, prefix: str) -> str:
    """Upload a built post folder; the public base URL its files are served from."""
    for f in sorted(out.iterdir()):
        if f.is_file():
            client.upload_file(str(f), name, f"{prefix}/{out.name}/{f.name}",
                               ExtraArgs={"ContentType": TYPES.get(f.suffix, "application/octet-stream")})
    return f"{env('R2_PUBLIC_URL').rstrip('/')}/{prefix}/{out.name}"


def graph(method: str, path: str, **params) -> dict:
    host, version = env("IG_GRAPH_HOST", "graph.facebook.com"), env("IG_GRAPH_VERSION", "v23.0")
    # a caller may pass its own access_token (inbox.py uses the Page's token)
    data = urllib.parse.urlencode({"access_token": env("IG_ACCESS_TOKEN"), **params})
    url = f"https://{host}/{version}/{path}"
    req = (urllib.request.Request(url, data=data.encode(), method="POST") if method == "POST"
           else urllib.request.Request(f"{url}?{data}", method=method))
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        # the error body names the problem; the token is never in it
        raise RuntimeError(f"{method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:500]}") from None


def ready(container: str, minutes: int = 10) -> None:
    """Wait until Instagram has fetched and processed a container (videos take a while)."""
    for _ in range(minutes * 6):
        status = graph("GET", container, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {container}: {status}")
        time.sleep(10)
    raise RuntimeError(f"container {container}: not ready after {minutes} minutes")


def make(**params) -> str:
    container = graph("POST", f"{env('IG_USER_ID')}/media", **params)["id"]
    ready(container)
    return container


def publish_one(manifest: dict, base: str, record=lambda ids: None) -> list[str]:
    """Publish one built post; the Instagram media IDs, feed post first. `record` gets the IDs after each one
    publishes, so a failure later in the post never makes a retry publish the earlier parts again."""
    ig, kind, caption = env("IG_USER_ID"), manifest["kind"], manifest["caption"]
    feed = [f for f in manifest["files"] if not f.get("story")]
    stories = [f for f in manifest["files"] if f.get("story")]
    url = lambda f: f"{base}/{f}"
    ids, containers = [], []
    if kind == "image":
        containers.append(make(image_url=url(feed[0]["file"]), caption=caption))
    elif kind == "carousel":
        children = [make(image_url=url(f["file"]), is_carousel_item="true") for f in feed]
        containers.append(make(media_type="CAROUSEL", children=",".join(children), caption=caption))
    elif kind in ("reel", "clip"):  # a hearing clip is published as a Reel
        f = feed[0]
        containers.append(make(media_type="REELS", video_url=url(f["file"]), cover_url=url(f["cover"]),
                               caption=caption, share_to_feed="true"))
    elif kind == "story":
        stories = feed
    for f in stories:
        containers.append(make(media_type="STORIES", image_url=url(f["file"])))
    for c in containers:
        ids.append(graph("POST", f"{ig}/media_publish", creation_id=c)["id"])
        record(list(ids))
    return ids


def comment(manifest: dict, base: str | None) -> str:
    """Markdown for the pull request: what the owner approves by merging."""
    m = manifest
    where = m["level"].capitalize() + (f" · {m['place']}" if m.get("place") else "")
    lines = [f"### {m['post']}", "",
             f"**{'Posts automatically after the checks pass' if m['approval'] == 'auto' else 'Waits for your approval: merge to post'}**"
             f" · {m['kind']} · {where} · {m.get('topic') or 'no topic'}"
             + (f" · scheduled {m['publish_after']}" if m.get("publish_after") else ""), ""]
    if m.get("found_via"):
        lines += [f"Found via (not a source, never published): {m['found_via']}", ""]
    if m.get("context"):  # clips: what came just before and after the cut, so the owner can judge it
        lines += [f"Context around the cut (never published): {m['context']}", ""]
    if m.get("highlight"):  # the Graph API can't add stories to a Highlight; the owner files them in the app
        lines += [f"File in Highlight: **{m['highlight']}**", ""]
    if m.get("caption_source"):
        lines += [f"On-screen captions from: {m['caption_source']}", ""]
    for f in m["files"]:
        shown = f.get("cover", f["file"])
        lines.append(f"![{f['alt_text'][:120]}]({base}/{shown})" if base else f"- `{f['file']}`: {f['alt_text']}")
    if not base:
        lines.append("\n_Images are in this run's artifacts until R2 is set up._")
    lines += ["", "Caption:", "", *("> " + line for line in m["caption"].splitlines()), ""]
    return "\n".join(lines)


def take_down(stem: str) -> None:
    """Delete a published post from Instagram. Its state entry stays, marked removed, so it never posts again."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", stem):
        sys.exit(f"{stem!r} isn't a post name (its file name in social/posts/, without .yaml)")
    client, name = bucket()
    state = load_state(client, name)
    entry = state.get(stem)
    if not entry:
        sys.exit(f"{stem} isn't published")
    if entry.get("removed"):
        print(f"{stem} was already taken down on {entry['removed']}")
        return
    for media in entry["media"]:  # the feed post, then any story frames
        graph("DELETE", media)
    entry["removed"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    client.put_object(Bucket=name, Key=STATE_KEY, Body=json.dumps(state, indent=2).encode(), ContentType="application/json")
    print(f"took down {stem} ({entry.get('permalink') or entry['media']})")


def check_setup() -> int:
    """Read-only checks that publishing can work: the account the token reaches, and a public bucket file."""
    bad = 0
    try:
        me = graph("GET", env("IG_USER_ID"), fields="username,media_count")
        print(f"Instagram: token works for @{me.get('username')} ({me.get('media_count')} posts)")
    except RuntimeError as e:
        print(f"Instagram: FAILED {e}")
        bad = 1
    client, name = bucket()
    key, body = "state/setup-check.txt", f"setup check {dt.datetime.now(dt.timezone.utc).isoformat()}".encode()
    client.put_object(Bucket=name, Key=key, Body=body, ContentType="text/plain")
    url = f"{env('R2_PUBLIC_URL').rstrip('/')}/{key}"
    try:
        # r2.dev refuses Python's default User-Agent; this is our own bucket, so name the check plainly
        req = urllib.request.Request(url, headers={"User-Agent": "vote-the-record-setup-check"})
        with urllib.request.urlopen(req, timeout=60) as r:
            same = r.read() == body
        print(f"R2: upload and public read {'work' if same else 'returned different content'} ({url})")
        bad |= not same
    except urllib.error.URLError as e:
        print(f"R2: uploaded, but the public URL failed: {e}")
        bad = 1
    return bad


def list_media() -> None:
    """Print what is on the account, matched to post files by caption, and any post that appears more than once."""
    keys = {}
    for p in sorted(POSTS.glob("*.yaml")):
        cap = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("caption") or ""
        if cap.strip():
            keys.setdefault(key(cap)[:120], p.stem)
    media, seen = media_list(200), {}
    for m in reversed(media):  # oldest first
        stem = next((s for k, s in keys.items() if key(m.get("caption", "")).startswith(k)), "?")
        seen.setdefault(stem, []).append(m)
        print(f"{m.get('timestamp', '')[:16]}  {m.get('media_product_type', ''):6}  {stem}  {m.get('permalink', '')}")
    print(f"\n{len(media)} media")
    for stem, ms in seen.items():
        if stem != "?" and len(ms) > 1:
            print(f"DUPLICATE x{len(ms)} {stem}: keep {ms[0].get('permalink')}; extra "
                  + " ".join(x.get("permalink", x["id"]) for x in ms[1:]))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="publish (default: list what is due)")
    ap.add_argument("--limit", type=int, default=1, help="most posts to publish in one run, to space them out")
    ap.add_argument("--preview", type=Path, nargs="+", help="build these posts and upload them under previews/")
    ap.add_argument("--check-setup", action="store_true", help="test the token and the bucket; posts nothing")
    ap.add_argument("--take-down", metavar="POST", help="delete this published post from Instagram (file name, no .yaml)")
    ap.add_argument("--list-media", action="store_true", help="list what is on the account; posts nothing")
    args = ap.parse_args()
    if args.list_media:
        sys.stdout.reconfigure(encoding="utf-8")
        return list_media()
    if args.take_down:
        return take_down(args.take_down)
    if args.check_setup:
        sys.exit(check_setup())
    sys.stdout.reconfigure(encoding="utf-8")  # the comment goes to a file; Windows consoles default to cp1252
    sys.path.insert(0, str(ROOT / "src/social"))
    import build  # noqa: E402  (src/social/build.py)

    if args.preview:
        # builds each post and prints the pull request comment; images show inline once R2 is set up
        client, name = bucket() if os.environ.get("R2_BUCKET") else (None, None)
        for p in args.preview:
            out = build.build(p)
            print(comment(json.loads((out / "manifest.json").read_text(encoding="utf-8")),
                          upload(client, name, out, "previews") if client else None))
        return

    now = dt.datetime.now(dt.timezone.utc)
    client, name = bucket()
    state = load_state(client, name)
    save = lambda: client.put_object(Bucket=name, Key=STATE_KEY, Body=json.dumps(state, indent=2).encode(),
                                     ContentType="application/json")
    posts = list(POSTS.glob("*.yaml"))
    for p in missed(posts, state, now, LATE):
        print(f"MISSED {p.stem}: its slot passed more than {LATE} ago; give it a new publish_after")
    todo = due(posts, state, now, LATE)
    print(f"{len(todo)} post(s) due" + (f"; publishing up to {args.limit}" if args.write else ""))
    if not args.write:
        for p in todo:
            print(f"  {p.stem}")
        return
    if not todo:
        return
    pause = load_json(client, name, PAUSE_KEY)
    if pause.get("until") and dt.datetime.fromisoformat(pause["until"]) > now:
        print(f"Paused until {pause['until']}: {pause.get('why', '')[:200]}")
        return
    try:
        quota = graph("GET", f"{env('IG_USER_ID')}/content_publishing_limit", fields="quota_usage,config")["data"][0]
        used, total = quota.get("quota_usage", 0), (quota.get("config") or {}).get("quota_total", 0)
        print(f"Instagram publishing quota: {used} of {total} used in the last 24 hours")
        if total and used >= total:
            return
    except (RuntimeError, KeyError, IndexError) as e:
        print(f"(couldn't read the publishing quota: {str(e)[:200]})")
    try:
        recent = media_list(50)
    except RuntimeError as e:
        # without the list we can't tell whether an earlier attempt went out, so post nothing this run
        sys.exit(f"couldn't list recent media, so nothing was posted: {e}")
    failed = []
    for p in todo[:args.limit]:
        print(f"  {p.stem}")

        def record(ids, stem=p.stem):  # the moment anything publishes, so it is never posted twice
            state[stem] = {"media": ids, "permalink": (state.get(stem) or {}).get("permalink"),
                           "published": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
            save()
        try:  # one broken post never stops the others
            out = build.build(p)
            manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            up = already_up(manifest["caption"], recent)
            if up:  # an earlier attempt went out even though Instagram reported an error
                record([up["id"]])
                state[p.stem].update(permalink=up.get("permalink"), adopted=True)
                save()
                print(f"  already on Instagram, recorded: {up.get('permalink') or up['id']}")
                continue
            base = upload(client, name, out, "posts")
            ids = publish_one(manifest, base, record)
        except (SystemExit, Exception) as e:
            print(f"  FAILED {p.stem}: {e}")
            failed.append(p.stem)
            if limited(str(e)):
                until = (now + PAUSE).isoformat(timespec="seconds")
                client.put_object(Bucket=name, Key=PAUSE_KEY, ContentType="application/json",
                                  Body=json.dumps({"until": until, "why": str(e)[:300]}).encode())
                print(f"  Instagram limit reached: publishing paused until {until}")
                break
            continue
        try:
            state[p.stem]["permalink"] = graph("GET", ids[0], fields="permalink").get("permalink")
            save()
        except RuntimeError:
            pass  # published; only the link is missing
        print(f"  published {state[p.stem]['permalink'] or ids}")
    if failed:
        # the run shows as failed, so GitHub emails the owner instead of the posts silently waiting
        sys.exit(f"{len(failed)} post(s) failed to publish: {', '.join(failed)}")


if __name__ == "__main__":
    main()
