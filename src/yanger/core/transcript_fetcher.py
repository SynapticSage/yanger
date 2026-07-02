"""Transcript fetching and processing for YouTube videos.

Handles fetching, compression, and formatting of video transcripts.
Supports proxy configuration to work around YouTube IP blocking.
"""
# Created: 2025-11-07
# Modified: 2025-12-30 - Added proxy support

import gzip
import json
import logging
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING
from dataclasses import dataclass, asdict
from datetime import datetime

if TYPE_CHECKING:
    from .proxy import ProxySettings

logger = logging.getLogger(__name__)

# Only these transcript failure statuses are permanent and safe to cache. Transient
# failures (IP_BLOCKED, ERROR/ERROR:...) must NOT be cached: caching them would make
# skip_cached / get_transcript / the TUI auto-fetch skip the video forever, so a proxy
# configured AFTER a block could never recover it. Leaving them uncached lets a later
# run retry. Owned here (where fetch_transcript emits the statuses) so every caller —
# app.py, mcp_server.py — shares one definition instead of hand-copying it.
TERMINAL_TRANSCRIPT_STATUSES = frozenset({"NOT_AVAILABLE"})


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

    def __init__(
        self,
        preferred_languages: Optional[List[str]] = None,
        proxy_settings: Optional["ProxySettings"] = None,
    ):
        """Initialize transcript fetcher.

        Args:
            preferred_languages: List of preferred language codes (e.g., ['en', 'es'])
            proxy_settings: Optional proxy configuration for bypassing IP blocks
        """
        self.preferred_languages = preferred_languages or ['en']
        self.proxy_settings = proxy_settings
        self._api_instance = None

        # Import youtube_transcript_api here to fail gracefully
        try:
            from youtube_transcript_api._errors import (
                TranscriptsDisabled,
                NoTranscriptFound,
                VideoUnavailable
            )
            self.errors = {
                'TranscriptsDisabled': TranscriptsDisabled,
                'NoTranscriptFound': NoTranscriptFound,
                'VideoUnavailable': VideoUnavailable
            }
            self._api_available = True
        except ImportError:
            logger.error("youtube-transcript-api not installed. Install with: pip install youtube-transcript-api")
            self._api_available = False
            self.errors = {}

    @property
    def api(self):
        """Lazy-load the API instance with proxy configuration."""
        if not self._api_available:
            return None

        if self._api_instance is None:
            from .proxy import create_transcript_api
            self._api_instance = create_transcript_api(self.proxy_settings)
            if self.proxy_settings and self.proxy_settings.enabled:
                logger.info(f"Transcript API initialized with proxy: {self.proxy_settings.get_display_info()}")

        return self._api_instance

    def update_proxy_settings(self, proxy_settings: Optional["ProxySettings"]) -> None:
        """Update proxy settings and recreate API instance.

        Args:
            proxy_settings: New proxy configuration
        """
        self.proxy_settings = proxy_settings
        self._api_instance = None  # Force recreation on next access

    def fetch_transcript(self, video_id: str) -> Tuple[Optional[TranscriptData], str]:
        """Fetch transcript for a video.

        Args:
            video_id: YouTube video ID

        Returns:
            Tuple of (TranscriptData, status) where status is:
                'SUCCESS', 'NOT_AVAILABLE', 'ERROR', 'IP_BLOCKED'
        """
        if not self.api:
            return None, 'ERROR'

        try:
            # Try to get transcript in preferred languages
            # 1.x API: instance.list(video_id) (was list_transcripts in 0.x)
            transcript_list = self.api.list(video_id)

            # Try preferred languages first
            transcript = None
            NoTranscriptFound = self.errors.get('NoTranscriptFound', Exception)

            for lang in self.preferred_languages:
                try:
                    transcript = transcript_list.find_transcript([lang])
                    break
                except NoTranscriptFound:
                    # Language not available, try next
                    continue

            # If no preferred language found, get any available transcript
            if not transcript:
                try:
                    transcript = transcript_list.find_generated_transcript(['en'])
                except NoTranscriptFound:
                    # Get first available transcript
                    available = list(transcript_list)
                    if available:
                        transcript = available[0]

            if not transcript:
                return None, 'NOT_AVAILABLE'

            # Fetch the transcript data (1.x returns a FetchedTranscript of snippet objects)
            transcript_data = transcript.fetch()

            # 1.x snippets expose .start/.duration/.text as attributes, not dict keys
            segments = [
                TranscriptSegment(
                    start=snippet.start,
                    duration=snippet.duration,
                    text=snippet.text
                )
                for snippet in transcript_data
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
            error_str = str(e).lower()

            # IP blocking detection (RequestBlocked, IpBlocked)
            if error_name in ['RequestBlocked', 'IpBlocked'] or 'blocking requests' in error_str:
                logger.warning(f"YouTube is blocking requests for video {video_id}. Consider using a proxy.")
                return None, 'IP_BLOCKED'

            # Known "not available" cases
            if error_name == 'TranscriptsDisabled':
                logger.info(f"Transcripts disabled for video {video_id}")
                return None, 'NOT_AVAILABLE'
            elif error_name == 'NoTranscriptFound':
                logger.info(f"No transcript found for video {video_id}")
                return None, 'NOT_AVAILABLE'
            elif error_name == 'VideoUnavailable':
                logger.warning(f"Video {video_id} unavailable")
                return None, 'NOT_AVAILABLE'
            # XML parsing errors (empty response, video deleted, etc.)
            elif error_name in ['ParseError', 'XMLSyntaxError'] or 'no element found' in error_str:
                logger.info(f"Video {video_id} unavailable or has no transcript (parse error)")
                return None, 'NOT_AVAILABLE'
            # HTTP errors (403, 404, etc.)
            elif 'http' in error_str or error_name == 'HTTPError':
                logger.warning(f"HTTP error for video {video_id}: {error_name}")
                return None, 'NOT_AVAILABLE'
            else:
                error_msg = f"Error fetching transcript for {video_id}: {error_name}: {str(e)}"
                logger.error(error_msg)
                return None, f'ERROR: {str(e)}'

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
