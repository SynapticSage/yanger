"""Tier 1 #1: the unified transcript fetch+cache service + read-side helper.

fetch_and_cache_transcript single-sources the write policy that used to be hand-copied
across app.py (auto/manual) and mcp_server (get/batch); should_refetch single-sources the
read-side 'is-terminal' gate. Cache is injected, so we pass a MagicMock.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yanger.core.transcript_fetcher import fetch_and_cache_transcript, should_refetch


def _fetcher(result):
    return SimpleNamespace(fetch_transcript=lambda video_id: result)


# ----- fetch_and_cache_transcript ----------------------------------------------

def test_success_caches_and_returns_data(sample_transcript_data):
    cache = MagicMock()
    data, status = fetch_and_cache_transcript(
        _fetcher((sample_transcript_data, "SUCCESS")), cache, "vid1"
    )
    assert status == "SUCCESS" and data is sample_transcript_data
    kwargs = cache.cache_transcript.call_args.kwargs
    assert kwargs["video_id"] == "vid1"
    assert kwargs["fetch_status"] == "SUCCESS"
    assert kwargs["transcript_text"] is not None and kwargs["transcript_json"] is not None


def test_not_available_caches_status_no_body():
    cache = MagicMock()
    data, status = fetch_and_cache_transcript(_fetcher((None, "NOT_AVAILABLE")), cache, "vid1")
    assert data is None and status == "NOT_AVAILABLE"
    kwargs = cache.cache_transcript.call_args.kwargs
    assert kwargs["fetch_status"] == "NOT_AVAILABLE"
    assert kwargs["transcript_text"] is None and kwargs["transcript_json"] is None


@pytest.mark.parametrize("status", ["IP_BLOCKED", "ERROR", "ERROR: quota exceeded"])
def test_transient_failures_not_cached(status):
    cache = MagicMock()
    data, out = fetch_and_cache_transcript(_fetcher((None, status)), cache, "vid1")
    assert data is None and out == status
    cache.cache_transcript.assert_not_called()


# ----- should_refetch -----------------------------------------------------------

@pytest.mark.parametrize("status,expected", [
    (None, True),
    ("", True),
    ("SUCCESS", False),
    ("NOT_AVAILABLE", False),
    ("IP_BLOCKED", True),
    ("ERROR", True),
    ("ERROR: quota exceeded", True),
])
def test_should_refetch(status, expected):
    assert should_refetch(status) is expected
