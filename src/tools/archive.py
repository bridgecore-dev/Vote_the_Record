"""Archive sources that still lack an archive_url on the Wayback Machine, and fill them in.

Usage:
  python src/tools/archive.py [--limit 20] [--delay 15] [--write]

Finds every fact under data/ with `archive_url: null`, asks the Wayback Machine to save each
source (3 tries, waiting longer after each failure), and for documents checks that the archived
copy's SHA-256 matches the fact's sha256 before using it. Without --write it only prints the
results. With --write it fills in archive_url on those facts (a data/ change, which needs the
owner's approval). It never touches verification fields.

Skipped: bulk downloads (.zip) that are too large to archive, and files a collector generates
(zips.yaml, precincts.yaml), whose sources are set in the collector and regenerated.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import GENERATED, REPO, USER_AGENT, NotRoundTrip, all_facts, fetch, read_doc, walk_facts, write_doc  # noqa: E402

SAVE = "https://web.archive.org/save/"


def pending(root: Path) -> dict[str, list[tuple[Path, dict]]]:
    """source_url -> facts that cite it with no archive_url yet."""
    out: dict[str, list[tuple[Path, dict]]] = {}
    for path, _trail, fact in all_facts(root):
        if fact.get("archive_url") or path.name in GENERATED:
            continue
        url = fact.get("source_url") or ""
        if not url.startswith("http") or url.lower().split("?")[0].endswith(".zip"):
            continue
        out.setdefault(url, []).append((path, fact))
    return out


def save(url: str, tries: int = 3, wait: float = 30.0) -> str | None:
    """Ask Save Page Now to archive url; the snapshot URL, or None after `tries` failures."""
    for attempt in range(tries):
        req = urllib.request.Request(SAVE + url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                final = resp.geturl()
        except urllib.error.HTTPError as err:
            final = err.headers.get("Location") or ""
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            final = ""
        m = re.search(r"https?://web\.archive\.org/web/(\d{14})/", final)
        if m:
            return f"https://web.archive.org/web/{m.group(1)}/{url}"
        if attempt + 1 < tries:
            time.sleep(wait * (attempt + 1))
    return None


def snapshot_sha256(snapshot: str) -> set[str]:
    """SHA-256 of the archived original bytes. Wayback can replay a file with its original gzip transfer
    encoding still applied, so a gzip body is hashed both as sent and unpacked."""
    raw_url = re.sub(r"/web/(\d{14})/", r"/web/\1id_/", snapshot, count=1)  # the original bytes
    data = fetch(raw_url)
    hashes = {hashlib.sha256(data).hexdigest()}
    if data[:2] == b"\x1f\x8b":
        try:
            hashes.add(hashlib.sha256(gzip.decompress(data)).hexdigest())
        except (OSError, EOFError):
            pass
    return hashes


def set_archive_url(path: Path, fact_id: str, archive_url: str) -> None:
    """Fill one fact's archive_url, keeping the file's formatting."""
    try:
        header, doc = read_doc(path)
    except NotRoundTrip:
        _edit_text(path, fact_id, archive_url)
        return
    for _trail, fact in walk_facts(doc):
        if fact["id"] == fact_id and fact.get("archive_url") is None:
            fact["archive_url"] = archive_url
    write_doc(path, header, doc)


def _edit_text(path: Path, fact_id: str, archive_url: str) -> None:
    """For hand-formatted files: change only the `archive_url: null` line inside this fact."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines)
                 if re.match(r"^\s*-?\s*id:\s*[\"']?" + re.escape(fact_id) + r"[\"']?\s*$", line))
    indent = len(lines[start]) - len(lines[start].lstrip(" -"))
    for i in range(start + 1, len(lines)):
        line = lines[i]
        stripped = line.lstrip(" ")
        if stripped.startswith("- ") and len(line) - len(stripped) < indent:
            break  # next list item: the fact had no archive_url line
        if re.match(r"^\s*archive_url:\s*null\s*$", line) and len(line) - len(stripped) == indent:
            lines[i] = " " * indent + f'archive_url: "{archive_url}"\n'
            path.write_text("".join(lines), encoding="utf-8", newline="\n")
            for _t, fact in walk_facts(yaml.safe_load("".join(lines))):  # confirm the edit parsed
                if fact["id"] == fact_id:
                    assert fact["archive_url"] == archive_url, f"{path}: edit didn't parse as expected"
            return
    raise ValueError(f"{path}: no 'archive_url: null' line found for {fact_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--limit", type=int, default=20, help="most sources to try this run")
    parser.add_argument("--delay", type=float, default=15.0, help="seconds between sources")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)

    todo = pending(args.root)
    print(f"{len(todo)} source(s) without an archived copy; trying up to {args.limit}.")
    done = 0
    for n, (url, facts) in enumerate(list(todo.items())[:args.limit]):
        if n:
            time.sleep(args.delay)
        snap = save(url)
        if not snap:
            print(f"FAILED (3 tries) {url}")
            continue
        hashes = {f.get("sha256") for _p, f in facts if f.get("source_kind") == "document" and f.get("sha256")}
        if hashes:
            try:
                got = snapshot_sha256(snap)
            except RuntimeError as err:
                print(f"SAVED but couldn't check the copy {snap}: {err}")
                continue
            if not got & hashes:
                print(f"MISMATCH {snap}: archived file's SHA-256 {sorted(got)} differs from the fact's; not used")
                continue
        print(f"ARCHIVED {snap} ({len(facts)} fact(s))")
        if args.write:
            for path, fact in facts:
                set_archive_url(path, fact["id"], snap)
        done += 1
    print(f"{done} archived." + ("" if args.write else " Run with --write to fill them in."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
