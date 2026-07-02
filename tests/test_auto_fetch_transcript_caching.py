"""Regression tests for the §0 live bug: TUI auto-fetch caching transient failures.

`YouTubeRangerApp._auto_fetch_transcript` used to cache *every* fetch status,
including transient `IP_BLOCKED` / `ERROR`. That permanently poisoned the cache so a
proxy configured after a block could never recover the video. The fix caches only
`TERMINAL_TRANSCRIPT_STATUSES` (NOT_AVAILABLE), matching the MCP path.

We drive the coroutine directly against a lightweight fake `self` — the failure
branch touches only `self._cache` and `self.settings`, so a full Textual app is
unnecessary. `TranscriptFetcher.fetch_transcript` is monkeypatched to return a chosen
`(data, status)` without any network call.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yanger.app import YouTubeRangerApp
from yanger.core import transcript_fetcher as tf


def _fake_app(cache):
    """Minimal stand-in exposing just what the failure path of the method reads."""
    return SimpleNamespace(
        settings=SimpleNamespace(transcripts=SimpleNamespace(languages=["en"])),
        _cache=cache,
        current_video=None,          # skips the success-branch UI refresh
        status_bar=None,
        miller_view=None,
    )


def _patch_fetch(monkeypatch, result):
    """Make every TranscriptFetcher.fetch_transcript return `result` (no network)."""
    monkeypatch.setattr(tf.TranscriptFetcher, "fetch_transcript", lambda self, vid: result)


@pytest.mark.parametrize("status", ["IP_BLOCKED", "ERROR", "ERROR: quota exceeded"])
async def test_transient_failures_are_not_cached(monkeypatch, status):
    """IP_BLOCKED / ERROR must NOT be written to the cache (so a retry can recover)."""
    _patch_fetch(monkeypatch, (None, status))
    cache = MagicMock()
    video = SimpleNamespace(id="vid123")

    await YouTubeRangerApp._auto_fetch_transcript(_fake_app(cache), video)

    cache.cache_transcript.assert_not_called()


async def test_not_available_is_cached(monkeypatch):
    """NOT_AVAILABLE is a permanent status and SHOULD be cached to avoid refetching."""
    _patch_fetch(monkeypatch, (None, "NOT_AVAILABLE"))
    cache = MagicMock()
    video = SimpleNamespace(id="vid123")

    await YouTubeRangerApp._auto_fetch_transcript(_fake_app(cache), video)

    cache.cache_transcript.assert_called_once()
    # video_id positional first, status positional last
    args = cache.cache_transcript.call_args.args
    assert args[0] == "vid123"
    assert args[-1] == "NOT_AVAILABLE"


async def test_success_is_cached(monkeypatch, sample_transcript_data):
    """A successful fetch still caches the transcript (guards against over-eager fix)."""
    _patch_fetch(monkeypatch, (sample_transcript_data, "SUCCESS"))
    cache = MagicMock()
    video = SimpleNamespace(id="dQw4w9WgXcQ")

    await YouTubeRangerApp._auto_fetch_transcript(_fake_app(cache), video)

    cache.cache_transcript.assert_called_once()
    assert cache.cache_transcript.call_args.args[-1] == "SUCCESS"
