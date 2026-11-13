"""Which merged posts the publisher treats as due, and the review comment it writes."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/social"))
import publish  # noqa: E402

NOW = dt.datetime(2026, 11, 14, 15, 0, tzinfo=dt.timezone.utc)


def post(tmp_path, name, when=None):
    p = tmp_path / f"{name}.yaml"
    p.write_text("kind: image\n" + (f"publish_after: '{when}'\n" if when else ""), encoding="utf-8")
    return p


def test_due_skips_published_and_future_posts(tmp_path):
    posts = [post(tmp_path, "2026-11-13-a"), post(tmp_path, "2026-11-13-b"),
             post(tmp_path, "2026-11-14-c", "2026-11-14T08:00:00-06:00"),   # 14:00 UTC: passed
             post(tmp_path, "2026-11-14-d", "2026-11-14T10:00:00-06:00")]   # 16:00 UTC: not yet
    assert [p.stem for p in publish.due(posts, {"2026-11-13-a": {}}, NOW)] == ["2026-11-13-b", "2026-11-14-c"]


def test_comment_marks_review_and_shows_where_it_was_found():
    m = {"post": "x", "kind": "image", "level": "state", "place": "Texas", "topic": "Calendar", "approval": "review",
         "files": [{"file": "slide-01.png", "alt_text": "A card."}], "caption": "Hi.\n\nSource:\nT: https://a.gov",
         "publish_after": None, "found_via": "https://news.example/story"}
    text = publish.comment(m, "https://media.example/previews/x")
    assert "Waits for your approval" in text
    assert "Found via (not a source, never published): https://news.example/story" in text
    assert "![A card.](https://media.example/previews/x/slide-01.png)" in text
    assert "> Hi." in text
