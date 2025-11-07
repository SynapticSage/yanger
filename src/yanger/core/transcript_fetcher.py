"""Transcript fetching and processing for YouTube videos.

Handles fetching, compression, and formatting of video transcripts.
"""
# Created: 2025-11-07

import gzip
import json
import logging
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    """A single segment of transcript with timing."""
    start: float
    duration: float
    text: str


@dataclass
class TranscriptData:
    """Complete transcript data with metadata."""
    video_id: str
    language: str
    auto_generated: bool
    segments: List[TranscriptSegment]
    fetched_at: str  # ISO 8601 timestamp

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            'video_id': self.video_id,
            'language': self.language,
            'auto_generated': self.auto_generated,
            'fetched_at': self.fetched_at,
            'segments': [
                {'start': seg.start, 'duration': seg.duration, 'text': seg.text}
                for seg in self.segments
            ]
        }


class TranscriptFetcher:
    """Fetches and processes YouTube video transcripts."""

    def __init__(self, preferred_languages: Optional[List[str]] = None):
        """Initialize transcript fetcher.

        Args:
            preferred_languages: List of preferred language codes (e.g., ['en', 'es'])
        """
        self.preferred_languages = preferred_languages or ['en']

        # Import youtube_transcript_api here to fail gracefully
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            from youtube_transcript_api._errors import (
                TranscriptsDisabled,
                NoTranscriptFound,
                VideoUnavailable
            )
            self.api = YouTubeTranscriptApi
            self.errors = {
                'TranscriptsDisabled': TranscriptsDisabled,
                'NoTranscriptFound': NoTranscriptFound,
                'VideoUnavailable': VideoUnavailable
            }
        except ImportError:
            logger.error("youtube-transcript-api not installed. Install with: pip install youtube-transcript-api")
            self.api = None
            self.errors = {}

    def fetch_transcript(self, video_id: str) -> Tuple[Optional[TranscriptData], str]:
        """Fetch transcript for a video.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (TranscriptData, status) where status is:
                'SUCCESS', 'NOT_AVAILABLE', 'ERROR'
        """
        if not self.api:
            return None, 'ERROR'

        try:
            # Try to get transcript in preferred languages
            transcript_list = self.api.list_transcripts(video_id)

            # Try preferred languages first
            transcript = None
            for lang in self.preferred_languages:
                try:
                    transcript = transcript_list.find_transcript([lang])
                    break
                except:
                    continue

            # If no preferred language found, get any available transcript
            if not transcript:
                try:
                    transcript = transcript_list.find_generated_transcript(['en'])
                except:
                    # Get first available transcript
                    available = list(transcript_list)
                    if available:
                        transcript = available[0]

            if not transcript:
                return None, 'NOT_AVAILABLE'

            # Fetch the transcript data
            transcript_data = transcript.fetch()

            # Convert to our format
            segments = [
                TranscriptSegment(
                    start=entry['start'],
                    duration=entry['duration'],
                    text=entry['text']
                )
                for entry in transcript_data
            ]

            result = TranscriptData(
                video_id=video_id,
                language=transcript.language_code,
                auto_generated=transcript.is_generated,
                segments=segments,
                fetched_at=datetime.utcnow().isoformat() + 'Z'
            )

            logger.info(f"Successfully fetched transcript for {video_id} ({transcript.language_code})")
            return result, 'SUCCESS'

        except Exception as e:
            error_name = type(e).__name__

            if error_name == 'TranscriptsDisabled':
                logger.info(f"Transcripts disabled for video {video_id}")
                return None, 'NOT_AVAILABLE'
            elif error_name == 'NoTranscriptFound':
                logger.info(f"No transcript found for video {video_id}")
                return None, 'NOT_AVAILABLE'
            elif error_name == 'VideoUnavailable':
                logger.warning(f"Video {video_id} unavailable")
                return None, 'NOT_AVAILABLE'
            else:
                logger.error(f"Error fetching transcript for {video_id}: {e}")
                return None, 'ERROR'

    @staticmethod
    def compress_transcript(text: str) -> bytes:
        """Compress transcript text using gzip.

        Args:
            text: Plain text transcript

        Returns:
            Compressed bytes
        """
        return gzip.compress(text.encode('utf-8'))

    @staticmethod
    def decompress_transcript(data: bytes) -> str:
        """Decompress transcript text.

        Args:
            data: Compressed transcript data

        Returns:
            Plain text transcript
        """
        return gzip.decompress(data).decode('utf-8')

    @staticmethod
    def format_as_text(transcript: TranscriptData) -> str:
        """Format transcript as plain text.

        Args:
            transcript: TranscriptData object

        Returns:
            Plain text transcript
        """
        return '\n'.join(seg.text for seg in transcript.segments)

    @staticmethod
    def format_as_json(transcript: TranscriptData) -> str:
        """Format transcript as JSON with timestamps.

        Args:
            transcript: TranscriptData object

        Returns:
            JSON string
        """
        return json.dumps(transcript.to_dict(), indent=2)

    @staticmethod
    def format_for_display(transcript: TranscriptData, max_chars: int = 1000) -> str:
        """Format transcript for display in preview pane.

        Args:
            transcript: TranscriptData object
            max_chars: Maximum characters to display

        Returns:
            Formatted text for display
        """
        text = TranscriptFetcher.format_as_text(transcript)

        if len(text) > max_chars:
            text = text[:max_chars] + "..."

        type_str = "auto-generated" if transcript.auto_generated else "manual"
        header = f"Transcript ({transcript.language}, {type_str}):\n"

        return header + text
