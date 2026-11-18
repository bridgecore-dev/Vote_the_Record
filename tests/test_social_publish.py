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


class FakeBucket:
    def __init__(self, state):
        self.state, self.saved = state, None

    def put_object(self, Bucket, Key, Body, ContentType):
        self.saved = Body


def test_take_down_deletes_every_media_and_keeps_the_post_marked(monkeypatch):
    import json
    state = {"2026-11-15-x": {"media": ["111"], "permalink": "https://www.instagram.com/reel/x/", "published": "t"}}
    fake, calls = FakeBucket(state), []
    monkeypatch.setattr(publish, "bucket", lambda: (fake, "b"))
    monkeypatch.setattr(publish, "load_state", lambda c, n: state)
    monkeypatch.setattr(publish, "graph", lambda method, path, **k: calls.append((method, path)) or {"success": True})
    publish.take_down("2026-11-15-x")
    assert calls == [("DELETE", "111")]
    saved = json.loads(fake.saved)["2026-11-15-x"]
    assert saved["removed"] and saved["media"] == ["111"]  # still in state, so it never posts again
    publish.take_down("2026-11-15-x")  # a second take-down deletes nothing
    assert calls == [("DELETE", "111")]


def test_take_down_refuses_names_that_are_not_posts(monkeypatch):
    import pytest
    monkeypatch.setattr(publish, "bucket", lambda: (_ for _ in ()).throw(AssertionError("no bucket")))
    with pytest.raises(SystemExit):
        publish.take_down("../state")


def test_due_orders_by_slot_and_leaves_out_late_posts(tmp_path):
    posts = [post(tmp_path, "2026-11-10-old", "2026-11-14T08:00:00-06:00"),    # 14:00 UTC, 1 hour ago
             post(tmp_path, "2026-11-14-early", "2026-11-14T07:30:00-06:00"),  # 13:30 UTC
             post(tmp_path, "2026-11-12-stale", "2026-11-14T05:00:00-06:00")]  # 11:00 UTC, 4 hours ago
    late = dt.timedelta(hours=3)
    assert [p.stem for p in publish.due(posts, {}, NOW, late)] == ["2026-11-14-early", "2026-11-10-old"]
    assert [p.stem for p in publish.missed(posts, {}, NOW, late)] == ["2026-11-12-stale"]


def test_limit_errors_are_recognized():
    assert publish.limited('POST x/media_publish: HTTP 403 {"error":{"message":"Application request limit reached","code":4}}')
    assert publish.limited('HTTP 400 {"error":{"message":"User is performing too many actions","code":9}}')
    assert not publish.limited('HTTP 400 {"error":{"message":"Media ID is not available","code":100}}')


def test_a_post_already_on_instagram_is_found_by_caption():
    media = [{"id": "2", "caption": "Four new  federal laws.\n\nMore", "timestamp": "2026-11-17T21:00:00+0000"},
             {"id": "1", "caption": "four new federal laws. more", "timestamp": "2026-11-17T20:00:00+0000"},
             {"id": "3", "caption": "Something else", "timestamp": "2026-11-17T19:00:00+0000"}]
    assert publish.already_up("Four new federal laws.\nMore", media)["id"] == "1"  # the oldest copy
    assert publish.already_up("A different post", media) is None


def test_each_part_is_recorded_as_soon_as_it_publishes(monkeypatch):
    import pytest
    calls = {"n": 0}

    def graph(method, path, **k):
        if path.endswith("/media"):
            return {"id": f"c{len(k)}"}
        if path.endswith("media_publish"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError('HTTP 403 {"error":{"code":4}}')
            return {"id": "m1"}
        return {"status_code": "FINISHED"}
    monkeypatch.setattr(publish, "graph", graph)
    monkeypatch.setenv("IG_USER_ID", "u")
    seen = []
    m = {"kind": "image", "caption": "c", "files": [{"file": "a.png"}, {"file": "s.png", "story": True}]}
    with pytest.raises(RuntimeError):
        publish.publish_one(m, "https://b", seen.append)
    assert seen == [["m1"]]  # the feed post was recorded before the story frame failed
