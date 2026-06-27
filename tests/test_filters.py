"""Regression tests for the advanced video filtering system.

Focus: date range filters must compare tz-aware video dates (models.py parses
published_at with +00:00) against naive filter targets (parsed via strptime /
datetime.now) without raising and silently excluding everything.
"""

from datetime import datetime, timezone

import pytest

from yanger.filters import VideoFilter
from yanger.models import Video


def _video(vid: str, published_at) -> Video:
    """Minimal Video with a (possibly tz-aware) published_at."""
    return Video(
        id=vid,
        playlist_item_id=f"item-{vid}",
        title=f"Title {vid}",
        channel_title="Test Channel",
        published_at=published_at,
    )


@pytest.fixture
def videos():
    return [
        _video("2024", datetime(2024, 6, 1, tzinfo=timezone.utc)),  # tz-aware
        _video("2019", datetime(2019, 6, 1, tzinfo=timezone.utc)),  # tz-aware
    ]


def test_date_greater_than_includes_recent_excludes_old(videos):
    result = VideoFilter().filter(videos, "date>2020-01-01")
    ids = {v.id for v in result}
    assert ids == {"2024"}


def test_date_less_than_includes_old_excludes_recent(videos):
    result = VideoFilter().filter(videos, "date<2020-01-01")
    ids = {v.id for v in result}
    assert ids == {"2019"}


def test_date_relative_days_includes_recent_only():
    now = datetime.now(timezone.utc)
    recent = _video("recent", now)
    old = _video("old", datetime(2000, 1, 1, tzinfo=timezone.utc))
    # date>30d => published within the last 30 days
    result = VideoFilter().filter([recent, old], "date>30d")
    assert {v.id for v in result} == {"recent"}


def test_naive_video_date_does_not_raise():
    """Guard: a video with a naive published_at must still compare cleanly."""
    naive = _video("naive", datetime(2024, 6, 1))  # no tzinfo
    result = VideoFilter().filter([naive], "date>2020-01-01")
    assert {v.id for v in result} == {"naive"}
