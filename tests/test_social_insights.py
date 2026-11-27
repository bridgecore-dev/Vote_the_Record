"""src/social/insights.py: the report, from made-up numbers (no Instagram calls)."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/social"))
import insights  # noqa: E402

ROWS = [
    {"stem": "2026-11-16-a", "kind": "reel", "level": "congress", "topic": "Votes", "layout": "card", "hour": "midday (11-16)",
     "reach": 1000, "saved": 30, "shares": 50, "likes": 80, "comments": 5, "ig_reels_avg_watch_time": 9000},
    {"stem": "2026-11-16-b", "kind": "carousel", "level": "state", "topic": "Calendar", "layout": "band", "hour": "morning (5-11)",
     "reach": 4000, "saved": 40, "shares": 20, "likes": 100, "comments": 2},
]


def test_rates_are_per_100_reached_so_size_alone_doesnt_win():
    text = insights.report(ROWS, 7, 120)
    most_shared = text.split("## Most shared")[1].split("##")[0]
    assert most_shared.index("2026-11-16-a") < most_shared.index("2026-11-16-b")  # 5.0 per 100 beats 0.5
    widest = text.split("## Widest reach")[1]
    assert widest.index("2026-11-16-b") < widest.index("2026-11-16-a")
    assert "120 followers now" in text and "average watch time 9.0 s" in text


def test_only_posts_recorded_as_published_in_the_window_count(monkeypatch):
    monkeypatch.setattr(insights, "insights", lambda media_id, kind: {"reach": 10})
    now = dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc)
    state = {"2026-11-16-a": {"media": ["111"], "published": "2026-11-16T18:00:00+00:00"},
             "2026-11-01-old": {"media": ["222"], "published": "2026-11-01T18:00:00+00:00"},
             "2026-11-17-story": {"media": ["pending:333"], "published": "2026-11-17T02:00:00+00:00"}}
    posts = {"2026-11-16-a": {"kind": "reel"}, "2026-11-01-old": {"kind": "reel"}, "2026-11-17-story": {"kind": "story"}}
    got = insights.rows(state, posts, now - dt.timedelta(days=7))
    assert [r["stem"] for r in got] == ["2026-11-16-a"]  # old one out of window; a pending story never published


def test_an_empty_window_says_so():
    assert "No published posts" in insights.report([], 7, None)


def test_the_layout_matches_the_builders_rotation():
    import importlib.util
    spec = importlib.util.spec_from_file_location("social_build", Path(__file__).resolve().parents[1] / "src/social/build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    assert insights.layout("x", {"kind": "carousel"}) == build.LAYOUTS[int(__import__("hashlib").sha1(b"x").hexdigest(), 16) % 3]
    assert insights.layout("x", {"kind": "reel"}) == "motion"
    assert build.LAYOUTS == ["card", "band", "stat"]  # insights.layout() copies this order


def test_missing_numbers_show_as_na_not_zero():
    text = insights.report([{"stem": "2026-11-16-x", "kind": "reel", "level": "congress", "topic": "T", "layout": "motion",
                             "hour": "night"}], 2, 3)
    assert "n/a accounts reached" in text and "| n/a |" in text and "0 per 100" not in text


def test_a_refused_request_is_kept_with_its_error(monkeypatch):
    def refuse(method, path, **k):
        raise RuntimeError('GET x/insights: HTTP 400 {"error":{"message":"(#10) Application does not have permission","code":10}}')
    monkeypatch.setattr(insights.publish, "graph", refuse)
    insights.ERRORS.clear()
    assert insights.insights("1", "reel") == {}
    assert "permission" in insights.ERRORS[0] and len(insights.ERRORS) == 1 + len(insights.METRICS["reel"])


def test_design_tables_use_each_posts_design_with_defaults():
    assert insights.design({"kind": "reel", "design": {"palette": "plum", "sfx": True}}) == {
        "motion": "classic", "sfx": "True", "palette": "plum", "mode": "light", "font": "serif", "position": "center"}
    rows = [dict(r, palette=p, motion="classic", sfx="False", mode="light", font="serif", position="center", trial="no")
            for r, p in zip(ROWS, ("plum", "teal"))]
    text = insights.report(rows, 7, 3)
    assert "## By design" in text and "| palette |" in text and "| plum | 1 |" in text


def test_voice_table_counts_only_reels_required_to_be_voiced():
    new = {"kind": "reel", "publish_after": "2026-11-15T10:30:00-06:00"}
    assert insights.design(new)["voice"] == "af_heart"
    assert insights.design({**new, "voice": True})["voice"] == "af_heart"
    assert insights.design({**new, "voice": {"speed": 1.1}})["voice"] == "af_heart"
    assert insights.design({**new, "voice": {"name": "am_puck"}})["voice"] == "am_puck"
    assert "voice" not in insights.design({"kind": "reel", "publish_after": "2026-11-14T12:00:00-06:00"})
    assert "voice" not in insights.design({"kind": "reel"})  # no slot: reel.py didn't require a voice
    assert "voice" not in insights.design({**new, "kind": "carousel"})
    rows = [dict(ROWS[0], voice="af_heart"), dict(ROWS[0], stem="x", voice="am_puck"), dict(ROWS[1])]
    table = insights.report(rows, 7, None).split("| voice |")[1].split("##")[0]
    assert "| am_puck | 1 |" in table and "| af_heart | 1 |" in table and "| None |" not in table


def test_voiced_from_matches_reel_py():
    src = (Path(__file__).resolve().parents[1] / "src/social/reel.py").read_text(encoding="utf-8")
    assert f'VOICED_FROM = "{insights.VOICED_FROM}"' in src


def test_the_manifest_voice_matches_the_report():
    import importlib.util
    spec = importlib.util.spec_from_file_location("social_build", Path(__file__).resolve().parents[1] / "src/social/build.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    for post in ({"kind": "reel"}, {"kind": "reel", "voice": True}, {"kind": "reel", "voice": {"name": "am_fenrir"}}):
        assert build.voice_name(post) == insights.design({**post, "publish_after": "2026-11-20"})["voice"]
    assert build.voice_name({"kind": "carousel"}) is None
