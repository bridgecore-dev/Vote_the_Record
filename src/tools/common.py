"""Shared helpers for the upkeep tools in src/tools/ (see docs/MAINTAINING.md)."""

from __future__ import annotations

import html
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "collectors"))
from yamlio import NotRoundTrip, read_doc, write_doc  # noqa: E402,F401

USER_AGENT = "Mozilla/5.0 (compatible; VoteTheRecord-upkeep/1.0; +https://github.com/bridgecore-dev/Vote_the_Record)"
# Files a collector generates: fix them by re-running the collector, never by hand.
GENERATED = ("zips.yaml", "precincts.yaml")


def data_files(root: Path) -> list[Path]:
    return sorted((root / "data" / "states").rglob("*.yaml"))


def walk_facts(doc, trail=()) -> Iterator[tuple[tuple, dict]]:
    """Every fact (a dict with a path-based id and a source_url) in a document, with its location."""
    if isinstance(doc, dict):
        if isinstance(doc.get("id"), str) and "#" in doc["id"] and "source_url" in doc:
            yield trail, doc
        for key, value in doc.items():
            yield from walk_facts(value, trail + (key,))
    elif isinstance(doc, list):
        for i, value in enumerate(doc):
            yield from walk_facts(value, trail + (i,))


def all_facts(root: Path) -> Iterator[tuple[Path, tuple, dict]]:
    for path in data_files(root):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        for trail, fact in walk_facts(doc):
            yield path, trail, fact


def fetch(url: str, tries: int = 3, wait: float = 20.0, timeout: float = 120.0, method: str = "GET") -> bytes:
    """GET (or HEAD) with up to `tries` attempts, waiting longer after each failure."""
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read() if method == "GET" else b""
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            last = err
            if attempt + 1 < tries:
                time.sleep(wait * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def page_text(raw: bytes) -> str:
    """Upper-case ASCII text of a page, keeping script contents (some rosters are JSON in scripts)."""
    raw_text = re.sub(r"<style.*?</style>", " ", raw.decode("utf-8", errors="replace"), flags=re.S)
    # Visible text, then the raw markup too: some rosters keep names in attributes (JSON in data-*).
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw_text)) + " " + html.unescape(raw_text)
    text = text.replace('\\"', '"')
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()


def name_tokens(name: str) -> list[str]:
    name = unicodedata.normalize("NFKD", re.sub(r'"[^"]*"|“[^”]*”', " ", name)).encode("ascii", "ignore").decode()
    return [t for t in re.findall(r"[A-Z]+", name.upper()) if len(t) > 1 and t not in ("JR", "SR", "II", "III", "IV")]


def name_on_page(name: str, text: str) -> bool:
    """Surname on the page with another of the person's names close by (either order)."""
    toks = name_tokens(name)
    if len(toks) < 2:
        return False
    last, others = toks[-1], toks[:-1]
    for m in re.finditer(r"\b" + last + r"\b", text):
        near = text[max(0, m.start() - 80):m.end() + 40]
        if any(re.search(r"\b" + t + r"\b", near) for t in others):
            return True
    return False
