"""Tier 1 #5: versioned cache migrations via PRAGMA user_version.

Replaces the old no-op schema_version bump + the ad-hoc ALTER that lived in a hot write path.
"""

import sqlite3

from yanger.cache import PersistentCache


def _columns(db_path, table):
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        conn.close()


def _user_version(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def test_fresh_db_migrates_to_latest_and_has_metadata_columns(tmp_path):
    cache = PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    assert _user_version(cache.db_path) == PersistentCache.SCHEMA_VERSION
    cols = _columns(cache.db_path, "virtual_videos")
    assert {"description", "thumbnail_url", "duration", "metadata_fetched_at"} <= cols


def test_migration_guard_is_idempotent_when_columns_preexist(tmp_path):
    """A legacy db whose columns were already added by the old ad-hoc ALTER (but
    user_version still 0) migrates cleanly to v1 without erroring."""
    db_path = tmp_path / "cache.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE virtual_playlists (id TEXT PRIMARY KEY);
        CREATE TABLE virtual_videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id TEXT NOT NULL,
            video_id TEXT NOT NULL,
            title TEXT, channel_title TEXT, added_at TIMESTAMP, position INTEGER,
            description TEXT, thumbnail_url TEXT, duration TEXT, metadata_fetched_at TIMESTAMP,
            UNIQUE(playlist_id, video_id)
        );
        PRAGMA user_version = 0;
        """
    )
    conn.commit()
    conn.close()

    cache = PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    assert _user_version(cache.db_path) == PersistentCache.SCHEMA_VERSION
    # No duplicate columns / no crash; still has the metadata columns.
    assert "metadata_fetched_at" in _columns(cache.db_path, "virtual_videos")


def test_reinit_does_not_rerun_migrations(tmp_path):
    PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    v1 = _user_version(tmp_path / "cache.db")
    # Second construction sees user_version already at latest -> early return, no error.
    PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    assert _user_version(tmp_path / "cache.db") == v1 == PersistentCache.SCHEMA_VERSION


def test_update_virtual_video_metadata_works_without_adhoc_alter(tmp_path):
    """The metadata write path (which no longer self-ALTERs) works because migration v1
    created the columns at init."""
    cache = PersistentCache(cache_dir=tmp_path, auto_cleanup=False)
    pid = cache.import_virtual_playlist("PL", [{"video_id": "v1", "title": "old"}], source="test")

    ok = cache.update_virtual_video_metadata("v1", {
        "title": "New Title", "channel_title": "Ch",
        "description": "desc", "thumbnail_url": "http://t", "duration": "PT5M",
    })
    assert ok is True

    # Read the migration-added columns back directly (get_virtual_videos doesn't select them).
    conn = sqlite3.connect(cache.db_path)
    try:
        row = conn.execute(
            "SELECT title, description, duration FROM virtual_videos WHERE video_id = 'v1'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("New Title", "desc", "PT5M")
