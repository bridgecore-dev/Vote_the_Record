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
"""
import argparse
import datetime as dt
import json
import os
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
TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".mp4": "video/mp4", ".json": "application/json"}


def due(posts: list[Path], published: dict, now: dt.datetime) -> list[Path]:
    """Posts not yet published whose publish_after has passed (or that have none), oldest name first."""
    out = []
    for p in sorted(posts):
        if p.stem in published:
            continue
        when = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("publish_after")
        if when and dt.datetime.fromisoformat(str(when)) > now:
            continue
        out.append(p)
    return out


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
    data = urllib.parse.urlencode({**params, "access_token": env("IG_ACCESS_TOKEN")})
    url = f"https://{host}/{version}/{path}"
    req = (urllib.request.Request(url, data=data.encode(), method="POST") if method == "POST"
           else urllib.request.Request(f"{url}?{data}"))
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


def publish_one(manifest: dict, base: str) -> list[str]:
    """Publish one built post; the Instagram media IDs, feed post first."""
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
    elif kind == "reel":
        f = feed[0]
        containers.append(make(media_type="REELS", video_url=url(f["file"]), cover_url=url(f["cover"]),
                               caption=caption, share_to_feed="true"))
    elif kind == "story":
        stories = feed
    for f in stories:
        containers.append(make(media_type="STORIES", image_url=url(f["file"])))
    for c in containers:
        ids.append(graph("POST", f"{ig}/media_publish", creation_id=c)["id"])
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
    for f in m["files"]:
        shown = f.get("cover", f["file"])
        lines.append(f"![{f['alt_text'][:120]}]({base}/{shown})" if base else f"- `{f['file']}`: {f['alt_text']}")
    if not base:
        lines.append("\n_Images are in this run's artifacts until R2 is set up._")
    lines += ["", "Caption:", "", *("> " + line for line in m["caption"].splitlines()), ""]
    return "\n".join(lines)


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
        with urllib.request.urlopen(url, timeout=60) as r:
            same = r.read() == body
        print(f"R2: upload and public read {'work' if same else 'returned different content'} ({url})")
        bad |= not same
    except urllib.error.URLError as e:
        print(f"R2: uploaded, but the public URL failed: {e}")
        bad = 1
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="publish (default: list what is due)")
    ap.add_argument("--limit", type=int, default=3, help="most posts to publish in one run, to space them out")
    ap.add_argument("--preview", type=Path, nargs="+", help="build these posts and upload them under previews/")
    ap.add_argument("--check-setup", action="store_true", help="test the token and the bucket; posts nothing")
    args = ap.parse_args()
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
    todo = due(list(POSTS.glob("*.yaml")), state, now)
    print(f"{len(todo)} post(s) due" + (f"; publishing up to {args.limit}" if args.write else ""))
    for p in todo[:args.limit] if args.write else todo:
        print(f"  {p.stem}")
        if not args.write:
            continue
        out = build.build(p)
        manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        base = upload(client, name, out, "posts")
        try:
            ids = publish_one(manifest, base)
        except RuntimeError as e:
            print(f"  FAILED {p.stem}: {e}")
            continue
        # record at once, so a later failure never publishes the same post twice
        state[p.stem] = {"media": ids, "permalink": None, "published": now.isoformat(timespec="seconds")}
        save = lambda: client.put_object(Bucket=name, Key=STATE_KEY, Body=json.dumps(state, indent=2).encode(),
                                         ContentType="application/json")
        save()
        try:
            state[p.stem]["permalink"] = graph("GET", ids[0], fields="permalink").get("permalink")
            save()
        except RuntimeError:
            pass  # published; only the link is missing
        print(f"  published {state[p.stem]['permalink'] or ids}")


if __name__ == "__main__":
    main()
