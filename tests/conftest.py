"""Shared pytest fixtures for yanger tests.

Provides common fixtures and test utilities.
"""
# Created: 2025-11-07

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any
from unittest.mock import Mock, MagicMock

# Import models from yanger package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from yanger.models import Video, Playlist, PrivacyStatus
from yanger.cache import PersistentCache
from yanger.config.settings import Settings, TranscriptSettings


@pytest.fixture
def tmp_cache_dir(tmp_path):
    """Provide a temporary cache directory for tests."""
    cache_dir = tmp_path / "test_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.fixture
def test_cache(tmp_cache_dir):
    """Provide a PersistentCache instance with temporary database."""
    cache = PersistentCache(
        cache_dir=tmp_cache_dir,
        ttl_days=7,
        auto_cleanup=False
    )
    yield cache
    # Cleanup is automatic when tmp_path is removed


@pytest.fixture
def sample_video():
    """Provide a sample Video object for testing."""
    return Video(
        id="dQw4w9WgXcQ",
        playlist_id="PLtest123",
        playlist_item_id="PLItest456",
        title="Never Gonna Give You Up",
        channel_title="Rick Astley",
        description="Official music video",
        position=0,
        duration="PT3M33S",
        view_count=1234567890,
        added_at=datetime(2024, 1, 15, 12, 0, 0),
        published_at=datetime(2009, 10, 24, 0, 0, 0)
    )


@pytest.fixture
def sample_playlist():
    """Provide a sample Playlist object for testing."""
    return Playlist(
        id="PLtest123",
        title="Test Playlist",
        description="A playlist for testing",
        item_count=10,
        privacy_status=PrivacyStatus.PUBLIC,
        channel_id="UCtest789",
        channel_title="Test Channel"
    )


@pytest.fixture
def sample_transcript_segments():
    """Provide sample transcript segments."""
    return [
        {"start": 0.0, "duration": 2.5, "text": "Hello world"},
        {"start": 2.5, "duration": 3.0, "text": "This is a test transcript"},
        {"start": 5.5, "duration": 2.0, "text": "With multiple segments"},
        {"start": 7.5, "duration": 1.5, "text": "Thank you"},
    ]


@pytest.fixture
def sample_transcript_data(sample_transcript_segments):
    """Provide sample TranscriptData object."""
    from yanger.core.transcript_fetcher import TranscriptData, TranscriptSegment

    segments = [
        TranscriptSegment(
            start=seg["start"],
            duration=seg["duration"],
            text=seg["text"]
        )
        for seg in sample_transcript_segments
    ]

    return TranscriptData(
        video_id="dQw4w9WgXcQ",
        language="en",
        auto_generated=False,
        segments=segments,
        fetched_at="2024-01-15T12:00:00Z"
    )


@pytest.fixture
def mock_youtube_transcript_api():
    """Provide a mock YouTubeTranscriptApi."""
    mock_api = MagicMock()

    # Mock transcript list
    mock_transcript_list = MagicMock()
    mock_transcript = MagicMock()
    mock_transcript.language_code = "en"
    mock_transcript.is_generated = False
    mock_transcript.fetch.return_value = [
        {"start": 0.0, "duration": 2.5, "text": "Hello world"},
        {"start": 2.5, "duration": 3.0, "text": "This is a test"},
    ]

    mock_transcript_list.find_transcript.return_value = mock_transcript
    mock_api.list_transcripts.return_value = mock_transcript_list

    return mock_api


@pytest.fixture
def mock_transcript_errors():
    """Provide mock transcript error classes."""
    class TranscriptsDisabled(Exception):
        pass

    class NoTranscriptFound(Exception):
        pass

    class VideoUnavailable(Exception):
        pass

    return {
        'TranscriptsDisabled': TranscriptsDisabled,
        'NoTranscriptFound': NoTranscriptFound,
        'VideoUnavailable': VideoUnavailable
    }


@pytest.fixture
def transcript_settings():
    """Provide test TranscriptSettings."""
    return TranscriptSettings(
        enabled=True,
        auto_fetch=False,
        store_in_db=True,
        store_compressed=True,
        export_directory=None,
        export_txt=True,
        export_json=True,
        languages=["en"]
    )


@pytest.fixture
def full_settings(transcript_settings):
    """Provide complete Settings object with transcript settings."""
    settings = Settings()
    settings.transcripts = transcript_settings
    return settings


def assert_db_table_exists(db_path: Path, table_name: str) -> bool:
    """Helper to check if a table exists in SQLite database."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,)
        )
        return cursor.fetchone() is not None


def get_db_row_count(db_path: Path, table_name: str) -> int:
    """Helper to get row count from a table."""
    with sqlite3.connect(db_path) as conn:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
        return cursor.fetchone()[0]


def get_transcript_from_db(db_path: Path, video_id: str) -> Dict[str, Any]:
    """Helper to get transcript data from database."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT * FROM video_transcripts WHERE video_id = ?",
            (video_id,)
        )
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None
