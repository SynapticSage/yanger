"""Tests for transcript_fetcher module.

Tests TranscriptFetcher class functionality including:
- Fetching transcripts from YouTube
- Compression and decompression
- Format conversions (text, JSON, display)
- Error handling
"""
# Created: 2025-11-07

import pytest
import gzip
import json
from unittest.mock import Mock, MagicMock, patch

from yanger.core.transcript_fetcher import (
    TranscriptFetcher,
    TranscriptData,
    TranscriptSegment
)


class TestTranscriptFetcher:
    """Test TranscriptFetcher class."""

    def test_initialization_with_default_languages(self):
        """Test fetcher initializes with default English language."""
        fetcher = TranscriptFetcher()
        assert fetcher.preferred_languages == ['en']

    def test_initialization_with_custom_languages(self):
        """Test fetcher initializes with custom language preferences."""
        fetcher = TranscriptFetcher(preferred_languages=['es', 'fr', 'en'])
        assert fetcher.preferred_languages == ['es', 'fr', 'en']

    @patch('yanger.core.transcript_fetcher.TranscriptFetcher.__init__')
    def test_initialization_handles_missing_api_library(self, mock_init):
        """Test graceful handling when youtube-transcript-api not installed."""
        # Simulate ImportError by setting api to None
        mock_init.return_value = None
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)
        fetcher.api = None
        fetcher.errors = {}
        fetcher.preferred_languages = ['en']

        result, status = fetcher.fetch_transcript("test_video")
        assert result is None
        assert status == 'ERROR'

    def test_compress_decompress_roundtrip(self):
        """Test compression and decompression preserve text."""
        original_text = "This is a test transcript with multiple words."
        compressed = TranscriptFetcher.compress_transcript(original_text)

        assert isinstance(compressed, bytes)
        assert len(compressed) < len(original_text.encode('utf-8'))  # Should be compressed

        decompressed = TranscriptFetcher.decompress_transcript(compressed)
        assert decompressed == original_text

    def test_compress_unicode_text(self):
        """Test compression handles Unicode characters."""
        unicode_text = "Hello 世界 🌍 Здравствуй"
        compressed = TranscriptFetcher.compress_transcript(unicode_text)
        decompressed = TranscriptFetcher.decompress_transcript(compressed)

        assert decompressed == unicode_text

    def test_compress_empty_string(self):
        """Test compression handles empty string."""
        compressed = TranscriptFetcher.compress_transcript("")
        decompressed = TranscriptFetcher.decompress_transcript(compressed)

        assert decompressed == ""

    def test_format_as_text(self, sample_transcript_data):
        """Test formatting transcript as plain text."""
        text = TranscriptFetcher.format_as_text(sample_transcript_data)

        assert "Hello world" in text
        assert "This is a test transcript" in text
        assert "With multiple segments" in text
        assert "\n" in text  # Should have line breaks

    def test_format_as_json(self, sample_transcript_data):
        """Test formatting transcript as JSON."""
        json_str = TranscriptFetcher.format_as_json(sample_transcript_data)

        # Parse to verify it's valid JSON
        data = json.loads(json_str)

        assert data['video_id'] == "dQw4w9WgXcQ"
        assert data['language'] == "en"
        assert data['auto_generated'] is False
        assert len(data['segments']) == 4
        assert data['segments'][0]['text'] == "Hello world"
        assert data['segments'][0]['start'] == 0.0

    def test_format_for_display_short_transcript(self, sample_transcript_data):
        """Test display formatting with short transcript."""
        display_text = TranscriptFetcher.format_for_display(sample_transcript_data, max_chars=1000)

        assert "Transcript (en, manual):" in display_text
        assert "Hello world" in display_text
        assert "..." not in display_text  # Should not be truncated

    def test_format_for_display_long_transcript(self, sample_transcript_data):
        """Test display formatting truncates long transcripts."""
        display_text = TranscriptFetcher.format_for_display(sample_transcript_data, max_chars=20)

        assert "Transcript (en, manual):" in display_text
        assert "..." in display_text  # Should be truncated

    def test_format_for_display_auto_generated(self):
        """Test display format shows auto-generated label."""
        transcript = TranscriptData(
            video_id="test",
            language="es",
            auto_generated=True,
            segments=[TranscriptSegment(0.0, 1.0, "Hola")],
            fetched_at="2024-01-15T12:00:00Z"
        )

        display_text = TranscriptFetcher.format_for_display(transcript)
        assert "auto-generated" in display_text
        assert "es" in display_text


class TestTranscriptFetcherWithMockAPI:
    """Test TranscriptFetcher with mocked YouTube API."""

    @patch('yanger.core.transcript_fetcher.TranscriptFetcher.__init__')
    def setup_mock_fetcher(self, mock_api, mock_errors=None):
        """Helper to create fetcher with mocked API."""
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)
        fetcher.api = mock_api
        fetcher.errors = mock_errors or {}
        fetcher.preferred_languages = ['en']
        return fetcher

    def test_fetch_transcript_success(self, mock_youtube_transcript_api):
        """Test successful transcript fetch."""
        fetcher = self.setup_mock_fetcher(mock_youtube_transcript_api)

        result, status = fetcher.fetch_transcript("test_video_id")

        assert status == 'SUCCESS'
        assert result is not None
        assert result.video_id == "test_video_id"
        assert result.language == "en"
        assert result.auto_generated is False
        assert len(result.segments) == 2
        assert result.segments[0].text == "Hello world"

    def test_fetch_transcript_with_preferred_language(self):
        """Test fetcher tries preferred languages in order."""
        mock_api = MagicMock()
        mock_transcript_list = MagicMock()

        # Mock Spanish transcript
        mock_es_transcript = MagicMock()
        mock_es_transcript.language_code = "es"
        mock_es_transcript.is_generated = False
        mock_es_transcript.fetch.return_value = [
            {"start": 0.0, "duration": 1.0, "text": "Hola mundo"}
        ]

        mock_transcript_list.find_transcript.return_value = mock_es_transcript
        mock_api.list_transcripts.return_value = mock_transcript_list

        fetcher = self.setup_mock_fetcher(mock_api)
        fetcher.preferred_languages = ['es', 'en']

        result, status = fetcher.fetch_transcript("test_video")

        assert status == 'SUCCESS'
        assert result.language == "es"
        assert result.segments[0].text == "Hola mundo"

    def test_fetch_transcript_no_transcript_found(self, mock_transcript_errors):
        """Test handling when no transcript is found."""
        mock_api = MagicMock()
        mock_api.list_transcripts.side_effect = mock_transcript_errors['NoTranscriptFound']()

        fetcher = self.setup_mock_fetcher(mock_api, mock_transcript_errors)

        result, status = fetcher.fetch_transcript("no_transcript_video")

        assert result is None
        assert status == 'NOT_AVAILABLE'

    def test_fetch_transcript_disabled(self, mock_transcript_errors):
        """Test handling when transcripts are disabled."""
        mock_api = MagicMock()
        mock_api.list_transcripts.side_effect = mock_transcript_errors['TranscriptsDisabled']()

        fetcher = self.setup_mock_fetcher(mock_api, mock_transcript_errors)

        result, status = fetcher.fetch_transcript("disabled_video")

        assert result is None
        assert status == 'NOT_AVAILABLE'

    def test_fetch_transcript_video_unavailable(self, mock_transcript_errors):
        """Test handling when video is unavailable."""
        mock_api = MagicMock()
        mock_api.list_transcripts.side_effect = mock_transcript_errors['VideoUnavailable']()

        fetcher = self.setup_mock_fetcher(mock_api, mock_transcript_errors)

        result, status = fetcher.fetch_transcript("unavailable_video")

        assert result is None
        assert status == 'NOT_AVAILABLE'

    def test_fetch_transcript_generic_error(self):
        """Test handling of unexpected errors."""
        mock_api = MagicMock()
        mock_api.list_transcripts.side_effect = Exception("Network error")

        fetcher = self.setup_mock_fetcher(mock_api)

        result, status = fetcher.fetch_transcript("error_video")

        assert result is None
        assert status == 'ERROR'


class TestTranscriptData:
    """Test TranscriptData dataclass."""

    def test_to_dict(self, sample_transcript_data):
        """Test converting TranscriptData to dictionary."""
        data_dict = sample_transcript_data.to_dict()

        assert data_dict['video_id'] == "dQw4w9WgXcQ"
        assert data_dict['language'] == "en"
        assert data_dict['auto_generated'] is False
        assert data_dict['fetched_at'] == "2024-01-15T12:00:00Z"
        assert len(data_dict['segments']) == 4

        # Check first segment
        assert data_dict['segments'][0]['start'] == 0.0
        assert data_dict['segments'][0]['duration'] == 2.5
        assert data_dict['segments'][0]['text'] == "Hello world"

    def test_to_dict_with_empty_segments(self):
        """Test to_dict with no segments."""
        transcript = TranscriptData(
            video_id="empty",
            language="en",
            auto_generated=False,
            segments=[],
            fetched_at="2024-01-15T12:00:00Z"
        )

        data_dict = transcript.to_dict()
        assert data_dict['segments'] == []


class TestTranscriptSegment:
    """Test TranscriptSegment dataclass."""

    def test_segment_creation(self):
        """Test creating a transcript segment."""
        segment = TranscriptSegment(
            start=1.5,
            duration=2.0,
            text="Test segment"
        )

        assert segment.start == 1.5
        assert segment.duration == 2.0
        assert segment.text == "Test segment"

    def test_segment_with_long_text(self):
        """Test segment with very long text."""
        long_text = "A" * 10000
        segment = TranscriptSegment(0.0, 1.0, long_text)

        assert len(segment.text) == 10000
        assert segment.text == long_text
