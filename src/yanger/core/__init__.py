"""Core functionality for YouTube Ranger.

Includes transcript fetching and proxy configuration.
"""
# Created: 2025-12-30

from .transcript_fetcher import TranscriptFetcher, TranscriptData, TranscriptSegment
from .proxy import ProxySettings, ProxyConfigBuilder, create_transcript_api, test_proxy_connection

__all__ = [
    'TranscriptFetcher',
    'TranscriptData',
    'TranscriptSegment',
    'ProxySettings',
    'ProxyConfigBuilder',
    'create_transcript_api',
    'test_proxy_connection',
]
