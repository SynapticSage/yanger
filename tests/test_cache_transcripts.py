"""Tests for transcript caching functionality in cache.py.

Tests PersistentCache transcript methods including:
- Database schema creation
- Caching transcripts (compressed and uncompressed)
- Retrieving cached transcripts
- Exporting transcripts to files
- Clearing transcript cache
"""
# Created: 2025-11-07

import pytest
import sqlite3
import json
from pathlib import Path
from datetime import datetime

from yanger.core.transcript_fetcher import TranscriptFetcher
from conftest import assert_db_table_exists, get_db_row_count, get_transcript_from_db


class TestTranscriptDatabaseSchema:
    """Test database schema for transcripts."""

    def test_video_transcripts_table_created(self, test_cache):
        """Test that video_transcripts table is created."""
        db_path = test_cache.db_path
        assert assert_db_table_exists(db_path, "video_transcripts")

    def test_video_transcripts_schema(self, test_cache):
        """Test video_transcripts table has correct columns."""
        with sqlite3.connect(test_cache.db_path) as conn:
            cursor = conn.execute("PRAGMA table_info(video_transcripts)")
            columns = {row[1]: row[2] for row in cursor.fetchall()}

            assert 'video_id' in columns
            assert 'transcript_text' in columns
            assert 'transcript_json' in columns
            assert 'language' in columns
            assert 'fetched_at' in columns
            assert 'auto_generated' in columns
            assert 'fetch_status' in columns

            # video_id should be PRIMARY KEY
            cursor = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='video_transcripts'"
            )
            schema = cursor.fetchone()[0]
            assert 'PRIMARY KEY' in schema

    def test_transcript_index_created(self, test_cache):
        """Test that transcript index is created."""
        with sqlite3.connect(test_cache.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_transcripts_video'"
            )
            assert cursor.fetchone() is not None


class TestCacheTranscript:
    """Test cache_transcript method."""

    def test_cache_transcript_with_compressed_data(self, test_cache, sample_transcript_data):
        """Test caching transcript with compressed text."""
        video_id = "test_video_123"
        text = TranscriptFetcher.format_as_text(sample_transcript_data)
        compressed = TranscriptFetcher.compress_transcript(text)
        json_data = TranscriptFetcher.format_as_json(sample_transcript_data)

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=compressed,
            transcript_json=json_data,
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        # Verify in database
        row = get_transcript_from_db(test_cache.db_path, video_id)
        assert row is not None
        assert row['video_id'] == video_id
        assert row['language'] == "en"
        assert row['auto_generated'] == 0  # SQLite stores as integer
        assert row['fetch_status'] == "SUCCESS"
        assert row['transcript_text'] is not None
        assert row['transcript_json'] == json_data

    def test_cache_transcript_without_data(self, test_cache):
        """Test caching transcript status without actual transcript."""
        video_id = "no_transcript_video"

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=None,
            transcript_json=None,
            language=None,
            auto_generated=False,
            fetch_status="NOT_AVAILABLE"
        )

        row = get_transcript_from_db(test_cache.db_path, video_id)
        assert row is not None
        assert row['fetch_status'] == "NOT_AVAILABLE"
        assert row['transcript_text'] is None
        assert row['transcript_json'] is None

    def test_cache_transcript_replaces_existing(self, test_cache):
        """Test that caching same video_id replaces existing entry."""
        video_id = "duplicate_video"

        # Cache first version
        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=b"first",
            transcript_json='{"version": 1}',
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        # Cache updated version
        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=b"second",
            transcript_json='{"version": 2}',
            language="es",
            auto_generated=True,
            fetch_status="SUCCESS"
        )

        # Should only have one row
        count = get_db_row_count(test_cache.db_path, "video_transcripts")
        assert count == 1

        # Should have latest data
        row = get_transcript_from_db(test_cache.db_path, video_id)
        assert row['language'] == "es"
        assert row['auto_generated'] == 1
        assert row['transcript_json'] == '{"version": 2}'

    def test_cache_transcript_auto_generated_flag(self, test_cache):
        """Test auto_generated flag is stored correctly."""
        test_cache.cache_transcript(
            video_id="auto_gen",
            transcript_text=b"test",
            transcript_json="{}",
            language="en",
            auto_generated=True,
            fetch_status="SUCCESS"
        )

        row = get_transcript_from_db(test_cache.db_path, "auto_gen")
        assert row['auto_generated'] == 1  # True stored as 1

        test_cache.cache_transcript(
            video_id="manual",
            transcript_text=b"test",
            transcript_json="{}",
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        row = get_transcript_from_db(test_cache.db_path, "manual")
        assert row['auto_generated'] == 0  # False stored as 0


class TestGetTranscript:
    """Test get_transcript method."""

    def test_get_transcript_exists(self, test_cache, sample_transcript_data):
        """Test retrieving existing transcript."""
        video_id = "exists_video"
        text = TranscriptFetcher.format_as_text(sample_transcript_data)
        compressed = TranscriptFetcher.compress_transcript(text)

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=compressed,
            transcript_json='{"test": true}',
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        result = test_cache.get_transcript(video_id)

        assert result is not None
        assert result['video_id'] == video_id
        assert result['language'] == "en"
        assert result['auto_generated'] is False  # Converted to bool
        assert result['fetch_status'] == "SUCCESS"
        assert result['transcript_text'] == compressed
        assert result['transcript_json'] == '{"test": true}'

    def test_get_transcript_not_exists(self, test_cache):
        """Test retrieving non-existent transcript returns None."""
        result = test_cache.get_transcript("nonexistent_video")
        assert result is None

    def test_get_transcript_decompression_works(self, test_cache):
        """Test that retrieved compressed text can be decompressed."""
        video_id = "compress_test"
        original_text = "This is test transcript text for decompression."
        compressed = TranscriptFetcher.compress_transcript(original_text)

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=compressed,
            transcript_json=None,
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        result = test_cache.get_transcript(video_id)
        decompressed = TranscriptFetcher.decompress_transcript(result['transcript_text'])

        assert decompressed == original_text


class TestGetTranscriptStatus:
    """Test get_transcript_status method."""

    def test_get_status_success(self, test_cache):
        """Test getting status of successful transcript."""
        test_cache.cache_transcript(
            video_id="success_video",
            transcript_text=b"data",
            transcript_json="{}",
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        status = test_cache.get_transcript_status("success_video")
        assert status == "SUCCESS"

    def test_get_status_not_available(self, test_cache):
        """Test getting status when transcript not available."""
        test_cache.cache_transcript(
            video_id="no_transcript",
            transcript_text=None,
            transcript_json=None,
            language=None,
            auto_generated=False,
            fetch_status="NOT_AVAILABLE"
        )

        status = test_cache.get_transcript_status("no_transcript")
        assert status == "NOT_AVAILABLE"

    def test_get_status_error(self, test_cache):
        """Test getting status when fetch had error."""
        test_cache.cache_transcript(
            video_id="error_video",
            transcript_text=None,
            transcript_json=None,
            language=None,
            auto_generated=False,
            fetch_status="ERROR"
        )

        status = test_cache.get_transcript_status("error_video")
        assert status == "ERROR"

    def test_get_status_not_cached(self, test_cache):
        """Test getting status for video not in cache."""
        status = test_cache.get_transcript_status("never_cached")
        assert status is None


class TestExportTranscript:
    """Test export_transcript method."""

    def test_export_transcript_success(self, test_cache, tmp_path, sample_transcript_data):
        """Test successful transcript export to files."""
        video_id = "export_test"
        export_dir = tmp_path / "exports"

        # Cache transcript
        text = TranscriptFetcher.format_as_text(sample_transcript_data)
        compressed = TranscriptFetcher.compress_transcript(text)
        json_data = TranscriptFetcher.format_as_json(sample_transcript_data)

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=compressed,
            transcript_json=json_data,
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        # Export
        success, error = test_cache.export_transcript(video_id, export_dir)

        assert success is True
        assert error is None
        assert export_dir.exists()

        # Check txt file
        txt_file = export_dir / f"{video_id}.txt"
        assert txt_file.exists()
        assert txt_file.read_text() == text

        # Check json file
        json_file = export_dir / f"{video_id}.json"
        assert json_file.exists()
        assert json_file.read_text() == json_data

    def test_export_transcript_not_found(self, test_cache, tmp_path):
        """Test export fails when transcript not in cache."""
        export_dir = tmp_path / "exports"

        success, error = test_cache.export_transcript("nonexistent", export_dir)

        assert success is False
        assert error == "Transcript not available"

    def test_export_transcript_not_available(self, test_cache, tmp_path):
        """Test export fails when transcript status is NOT_AVAILABLE."""
        video_id = "no_transcript"
        export_dir = tmp_path / "exports"

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=None,
            transcript_json=None,
            language=None,
            auto_generated=False,
            fetch_status="NOT_AVAILABLE"
        )

        success, error = test_cache.export_transcript(video_id, export_dir)

        assert success is False
        assert error == "Transcript not available"

    def test_export_creates_directory(self, test_cache, tmp_path, sample_transcript_data):
        """Test export creates directory if it doesn't exist."""
        video_id = "create_dir_test"
        export_dir = tmp_path / "deeply" / "nested" / "path"

        assert not export_dir.exists()

        text = TranscriptFetcher.format_as_text(sample_transcript_data)
        compressed = TranscriptFetcher.compress_transcript(text)

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=compressed,
            transcript_json='{}',
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        success, error = test_cache.export_transcript(video_id, export_dir)

        assert success is True
        assert export_dir.exists()


class TestClearTranscriptCache:
    """Test clear_transcript_cache method."""

    def test_clear_empty_cache(self, test_cache):
        """Test clearing empty transcript cache."""
        count = test_cache.clear_transcript_cache()
        assert count == 0

    def test_clear_cache_with_transcripts(self, test_cache):
        """Test clearing cache removes all transcripts."""
        # Add multiple transcripts
        for i in range(5):
            test_cache.cache_transcript(
                video_id=f"video_{i}",
                transcript_text=b"test",
                transcript_json="{}",
                language="en",
                auto_generated=False,
                fetch_status="SUCCESS"
            )

        # Verify they exist
        assert get_db_row_count(test_cache.db_path, "video_transcripts") == 5

        # Clear
        count = test_cache.clear_transcript_cache()

        assert count == 5
        assert get_db_row_count(test_cache.db_path, "video_transcripts") == 0

    def test_clear_cache_idempotent(self, test_cache):
        """Test clearing cache multiple times is safe."""
        test_cache.cache_transcript(
            video_id="test",
            transcript_text=b"data",
            transcript_json="{}",
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        count1 = test_cache.clear_transcript_cache()
        assert count1 == 1

        count2 = test_cache.clear_transcript_cache()
        assert count2 == 0


class TestTranscriptCacheIntegration:
    """Integration tests for transcript caching workflow."""

    def test_full_workflow(self, test_cache, tmp_path, sample_transcript_data):
        """Test complete workflow: cache, retrieve, export, clear."""
        video_id = "workflow_test"

        # 1. Cache transcript
        text = TranscriptFetcher.format_as_text(sample_transcript_data)
        compressed = TranscriptFetcher.compress_transcript(text)
        json_data = TranscriptFetcher.format_as_json(sample_transcript_data)

        test_cache.cache_transcript(
            video_id=video_id,
            transcript_text=compressed,
            transcript_json=json_data,
            language="en",
            auto_generated=False,
            fetch_status="SUCCESS"
        )

        # 2. Check status
        status = test_cache.get_transcript_status(video_id)
        assert status == "SUCCESS"

        # 3. Retrieve
        cached = test_cache.get_transcript(video_id)
        assert cached is not None
        assert cached['language'] == "en"

        # 4. Export
        export_dir = tmp_path / "exports"
        success, _ = test_cache.export_transcript(video_id, export_dir)
        assert success is True
        assert (export_dir / f"{video_id}.txt").exists()

        # 5. Clear
        count = test_cache.clear_transcript_cache()
        assert count == 1
        assert test_cache.get_transcript_status(video_id) is None

    def test_multiple_videos_independent(self, test_cache):
        """Test that multiple videos can be cached independently."""
        videos = ["video_1", "video_2", "video_3"]

        for video_id in videos:
            test_cache.cache_transcript(
                video_id=video_id,
                transcript_text=f"text_{video_id}".encode(),
                transcript_json=f'{{"id": "{video_id}"}}',
                language="en",
                auto_generated=False,
                fetch_status="SUCCESS"
            )

        # Verify all are cached independently
        for video_id in videos:
            cached = test_cache.get_transcript(video_id)
            assert cached is not None
            assert cached['video_id'] == video_id
            assert cached['transcript_text'] == f"text_{video_id}".encode()

        # Clear one shouldn't affect others
        # (Note: current clear_transcript_cache clears all, but this tests independence of get)
        assert test_cache.get_transcript("video_1") is not None
        assert test_cache.get_transcript("video_2") is not None
        assert test_cache.get_transcript("video_3") is not None
