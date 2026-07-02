"""Tier 1 #4: persist + share API quota across processes (TUI + MCP).

quota_used was in-memory per process, so it reset every launch and the two processes never
agreed. Now it lives in the SQLite cache keyed to the Pacific reset window.
"""

import re
from unittest.mock import MagicMock

import pytest

from yanger.cache import PersistentCache
from yanger.api_client import YouTubeAPIClient, QuotaExceededError, current_quota_reset_key


def _client(quota_store=None):
    return YouTubeAPIClient(MagicMock(), quota_store=quota_store)


_COST = YouTubeAPIClient.QUOTA_COSTS.get("playlists.list", 1)


# ----- cache-level quota store --------------------------------------------------

def test_cache_quota_upsert_increment(tmp_path):
    cache = PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    assert cache.get_quota_used("2026-07-02") == 0
    assert cache.add_quota_used(50, "2026-07-02") == 50
    assert cache.add_quota_used(30, "2026-07-02") == 80
    assert cache.get_quota_used("2026-07-02") == 80


def test_cache_quota_windows_are_independent(tmp_path):
    cache = PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    cache.add_quota_used(100, "2026-07-02")
    assert cache.get_quota_used("2026-07-03") == 0  # next window starts fresh


# ----- api_client integration ---------------------------------------------------

def test_inmemory_quota_when_no_store():
    c = _client()
    assert c.quota_used == 0
    c._track_quota("playlists.list")
    assert c.quota_used == _COST
    assert c.get_quota_remaining() == c.daily_quota - _COST


def test_quota_shared_across_clients_via_store(tmp_path):
    """Two clients on the same cache db (i.e. two processes) share one running count."""
    cache = PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    tui = _client(quota_store=cache)
    mcp = _client(quota_store=cache)

    tui._track_quota("playlists.list")
    # The MCP-side client immediately sees the TUI-side usage.
    assert mcp.quota_used == _COST
    assert tui.quota_used == cache.get_quota_used(current_quota_reset_key())

    mcp._track_quota("playlists.list")
    assert tui.quota_used == 2 * _COST  # both increments visible to both


def test_quota_exceeded_raises_before_tracking():
    c = _client()
    c.daily_quota = 5
    c.quota_used = 5  # setter -> in-memory
    with pytest.raises(QuotaExceededError):
        c._track_quota("playlists.list")


def test_reset_key_is_pacific_iso_date():
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", current_quota_reset_key())


def test_reset_key_degrades_without_tz_database(monkeypatch):
    """No IANA tz db (Windows/slim containers) must NOT crash — falls back to UTC-8."""
    import yanger.api_client as apimod

    def _raise(_name):
        raise apimod.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(apimod, "ZoneInfo", _raise)
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", apimod.current_quota_reset_key())
