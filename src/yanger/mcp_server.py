"""MCP (Model Context Protocol) server for YouTube Ranger.

Exposes yanger's playlist management capabilities via MCP, enabling
Claude and other MCP-compatible tools to manage YouTube playlists.

This module reuses existing yanger components:
- api_client.py for YouTube API operations
- cache.py for SQLite caching
- auth.py for OAuth2 authentication
- core/transcript_fetcher.py for transcripts
- duplicates.py for duplicate detection
- statistics.py for playlist analytics
"""
# Created: 2025-12-25

import asyncio
import json
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Any, Optional, List
from dataclasses import asdict

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool,
        TextContent,
        CallToolResult,
    )
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

from .auth import YouTubeAuth
from .api_client import YouTubeAPIClient, QuotaExceededError
from .cache import PersistentCache
from .core.transcript_fetcher import TranscriptFetcher
from .models import Playlist, Video, PrivacyStatus
from .duplicates import DuplicateDetector
from .statistics import PlaylistAnalyzer


logger = logging.getLogger(__name__)


class YangerMCPServer:
    """MCP server wrapping yanger's YouTube playlist functionality."""

    def __init__(self):
        """Initialize the MCP server with yanger components."""
        self.server = Server("yanger")
        self.api_client: Optional[YouTubeAPIClient] = None
        self.cache: Optional[PersistentCache] = None
        self.transcript_fetcher: Optional[TranscriptFetcher] = None
        self._authenticated = False

        # Register tool handlers
        self._register_tools()

    def _ensure_auth(self) -> None:
        """Ensure YouTube API client is authenticated."""
        if self._authenticated and self.api_client:
            return

        auth = YouTubeAuth()
        auth.authenticate()
        self.api_client = YouTubeAPIClient(auth)
        self.cache = PersistentCache()
        self.transcript_fetcher = TranscriptFetcher()
        self._authenticated = True

    def _register_tools(self) -> None:
        """Register all MCP tools."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """Return list of available tools."""
            return [
                # Playlist Management
                Tool(
                    name="list_playlists",
                    description="List all YouTube playlists for the authenticated user. "
                                "Returns playlist ID, title, description, and video count.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "include_virtual": {
                                "type": "boolean",
                                "description": "Include virtual (imported) playlists from Takeout",
                                "default": False,
                            },
                        },
                    },
                ),
                Tool(
                    name="get_playlist",
                    description="Get details of a specific playlist by ID.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "The YouTube playlist ID",
                            },
                        },
                        "required": ["playlist_id"],
                    },
                ),
                Tool(
                    name="create_playlist",
                    description="Create a new YouTube playlist.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "Title of the playlist",
                            },
                            "description": {
                                "type": "string",
                                "description": "Description of the playlist",
                                "default": "",
                            },
                            "privacy_status": {
                                "type": "string",
                                "enum": ["public", "private", "unlisted"],
                                "description": "Privacy setting for the playlist",
                                "default": "private",
                            },
                        },
                        "required": ["title"],
                    },
                ),
                Tool(
                    name="delete_playlist",
                    description="Delete a YouTube playlist. This action cannot be undone.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "The YouTube playlist ID to delete",
                            },
                        },
                        "required": ["playlist_id"],
                    },
                ),
                Tool(
                    name="rename_playlist",
                    description="Rename a YouTube playlist.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "The YouTube playlist ID",
                            },
                            "new_title": {
                                "type": "string",
                                "description": "New title for the playlist",
                            },
                        },
                        "required": ["playlist_id", "new_title"],
                    },
                ),

                # Video Management
                Tool(
                    name="list_videos",
                    description="List all videos in a playlist.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "The YouTube playlist ID",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of videos to return",
                                "default": 50,
                            },
                        },
                        "required": ["playlist_id"],
                    },
                ),
                Tool(
                    name="add_video",
                    description="Add a video to a playlist.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "video_id": {
                                "type": "string",
                                "description": "The YouTube video ID (from the video URL)",
                            },
                            "playlist_id": {
                                "type": "string",
                                "description": "The target playlist ID",
                            },
                            "position": {
                                "type": "integer",
                                "description": "Position in the playlist (0-indexed, optional)",
                            },
                        },
                        "required": ["video_id", "playlist_id"],
                    },
                ),
                Tool(
                    name="remove_video",
                    description="Remove a video from a playlist.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_item_id": {
                                "type": "string",
                                "description": "The playlist item ID (not the video ID)",
                            },
                        },
                        "required": ["playlist_item_id"],
                    },
                ),
                Tool(
                    name="move_video",
                    description="Move a video from one playlist to another.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "video_id": {
                                "type": "string",
                                "description": "The YouTube video ID",
                            },
                            "source_playlist_item_id": {
                                "type": "string",
                                "description": "The playlist item ID in the source playlist",
                            },
                            "target_playlist_id": {
                                "type": "string",
                                "description": "The target playlist ID",
                            },
                        },
                        "required": ["video_id", "source_playlist_item_id", "target_playlist_id"],
                    },
                ),
                Tool(
                    name="search_videos",
                    description="Search for videos across all playlists by title.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query to match against video titles",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results",
                                "default": 20,
                            },
                        },
                        "required": ["query"],
                    },
                ),

                # Transcript Tools
                Tool(
                    name="get_transcript",
                    description="Get the transcript of a YouTube video. Does not use YouTube API quota.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "video_id": {
                                "type": "string",
                                "description": "The YouTube video ID",
                            },
                            "format": {
                                "type": "string",
                                "enum": ["text", "json"],
                                "description": "Output format: 'text' for plain text, 'json' for timestamps",
                                "default": "text",
                            },
                        },
                        "required": ["video_id"],
                    },
                ),

                # Utility Tools
                Tool(
                    name="check_quota",
                    description="Check remaining YouTube API quota for today.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                Tool(
                    name="get_statistics",
                    description="Get statistics about playlists and videos.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "Optional: Get stats for a specific playlist",
                            },
                        },
                    },
                ),

                # Advanced Analysis Tools
                Tool(
                    name="find_duplicates",
                    description="Find duplicate videos within a playlist or across all playlists. "
                                "Detects exact matches (same video ID) and fuzzy matches (similar titles).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "Check duplicates within this playlist. If omitted, checks across all playlists.",
                            },
                            "include_fuzzy": {
                                "type": "boolean",
                                "description": "Include fuzzy title matches (similar but not identical)",
                                "default": False,
                            },
                        },
                    },
                ),
                Tool(
                    name="analyze_playlist",
                    description="Get comprehensive analytics for a playlist including duration stats, "
                                "channel distribution, temporal patterns, and more.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "The playlist ID to analyze",
                            },
                        },
                        "required": ["playlist_id"],
                    },
                ),
                Tool(
                    name="copy_videos",
                    description="Copy videos from one playlist to another. Works with virtual playlists "
                                "(imported Watch Later/History) as source, enabling transfer to real YouTube playlists.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source_playlist_id": {
                                "type": "string",
                                "description": "Source playlist ID (can be virtual like 'virtual_watchlater')",
                            },
                            "target_playlist_id": {
                                "type": "string",
                                "description": "Target YouTube playlist ID",
                            },
                            "video_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Optional: specific video IDs to copy. If omitted, copies all.",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of videos to copy",
                                "default": 50,
                            },
                        },
                        "required": ["source_playlist_id", "target_playlist_id"],
                    },
                ),
                Tool(
                    name="search_transcripts",
                    description="Search within video transcripts for specific text. "
                                "Finds videos where the spoken content matches your query.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Text to search for within transcripts",
                            },
                            "playlist_id": {
                                "type": "string",
                                "description": "Optional: limit search to videos in this playlist",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results",
                                "default": 10,
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="batch_fetch_transcripts",
                    description="Fetch transcripts for all videos in a playlist. "
                                "Does not use YouTube API quota. Results are cached for future use.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "playlist_id": {
                                "type": "string",
                                "description": "The playlist ID to fetch transcripts for",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of videos to process",
                                "default": 50,
                            },
                            "skip_cached": {
                                "type": "boolean",
                                "description": "Skip videos that already have cached transcripts",
                                "default": True,
                            },
                        },
                        "required": ["playlist_id"],
                    },
                ),

                # Fabric Integration
                Tool(
                    name="fabric_analyze",
                    description="Apply a Fabric pattern to analyze a YouTube video transcript. "
                                "Fabric provides curated AI prompts for tasks like summarization, "
                                "extracting wisdom, finding insights, and more. "
                                "Common patterns: extract_wisdom, summarize, extract_insights, "
                                "analyze_claims, extract_recommendations, create_summary.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "video_id": {
                                "type": "string",
                                "description": "The YouTube video ID to analyze",
                            },
                            "pattern": {
                                "type": "string",
                                "description": "The Fabric pattern to apply (e.g., 'extract_wisdom', 'summarize')",
                            },
                            "model": {
                                "type": "string",
                                "description": "Optional: specific model to use with Fabric",
                            },
                        },
                        "required": ["video_id", "pattern"],
                    },
                ),
                Tool(
                    name="list_fabric_patterns",
                    description="List available Fabric patterns that can be used with fabric_analyze.",
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
                ),
                Tool(
                    name="fabric_analyze_batch",
                    description="Apply a Fabric pattern to analyze multiple YouTube video transcripts. "
                                "Provide either a playlist_id to analyze all videos in a playlist, "
                                "or a list of video_ids. Returns aggregated results for each video.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "pattern": {
                                "type": "string",
                                "description": "The Fabric pattern to apply (e.g., 'extract_wisdom', 'summarize')",
                            },
                            "playlist_id": {
                                "type": "string",
                                "description": "Analyze all videos in this playlist",
                            },
                            "video_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of specific video IDs to analyze",
                            },
                            "model": {
                                "type": "string",
                                "description": "Optional: specific model to use with Fabric",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of videos to analyze",
                                "default": 10,
                            },
                            "skip_errors": {
                                "type": "boolean",
                                "description": "Continue processing if individual videos fail",
                                "default": True,
                            },
                        },
                        "required": ["pattern"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Handle tool calls."""
            try:
                result = await self._handle_tool(name, arguments)
                return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
            except QuotaExceededError as e:
                return [TextContent(type="text", text=json.dumps({
                    "error": "quota_exceeded",
                    "message": str(e),
                }, indent=2))]
            except Exception as e:
                logger.exception(f"Error handling tool {name}")
                return [TextContent(type="text", text=json.dumps({
                    "error": type(e).__name__,
                    "message": str(e),
                }, indent=2))]

    async def _handle_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Route tool calls to handlers."""
        # Tools that don't require YouTube API authentication
        no_auth_tools = [
            "get_transcript", "search_transcripts", "batch_fetch_transcripts",
            "fabric_analyze", "list_fabric_patterns", "fabric_analyze_batch"
        ]

        if name not in no_auth_tools:
            self._ensure_auth()

        handlers = {
            # Playlist Management
            "list_playlists": self._list_playlists,
            "get_playlist": self._get_playlist,
            "create_playlist": self._create_playlist,
            "delete_playlist": self._delete_playlist,
            "rename_playlist": self._rename_playlist,

            # Video Management
            "list_videos": self._list_videos,
            "add_video": self._add_video,
            "remove_video": self._remove_video,
            "move_video": self._move_video,
            "search_videos": self._search_videos,

            # Transcripts
            "get_transcript": self._get_transcript,

            # Utilities
            "check_quota": self._check_quota,
            "get_statistics": self._get_statistics,

            # Advanced Analysis
            "find_duplicates": self._find_duplicates,
            "analyze_playlist": self._analyze_playlist,
            "copy_videos": self._copy_videos,
            "search_transcripts": self._search_transcripts,
            "batch_fetch_transcripts": self._batch_fetch_transcripts,

            # Fabric Integration
            "fabric_analyze": self._fabric_analyze,
            "list_fabric_patterns": self._list_fabric_patterns,
            "fabric_analyze_batch": self._fabric_analyze_batch,
        }

        handler = handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")

        return await handler(arguments)

    # --- Playlist Management ---

    async def _list_playlists(self, args: dict[str, Any]) -> dict[str, Any]:
        """List all playlists."""
        include_virtual = args.get("include_virtual", False)

        # Try cache first
        playlists = self.cache.get_playlists()

        if playlists is None:
            # Fetch from API
            playlists = self.api_client.get_playlists()
            self.cache.set_playlists(playlists)

        result = []
        for p in playlists:
            result.append({
                "id": p.id,
                "title": p.title,
                "description": p.description,
                "video_count": p.item_count,
                "privacy_status": p.privacy_status.value,
                "is_virtual": p.is_virtual,
            })

        # Add virtual playlists if requested
        if include_virtual:
            virtual = self.cache.get_virtual_playlists()
            for vp in virtual:
                result.append({
                    "id": vp["id"],
                    "title": vp["title"],
                    "description": vp.get("description", ""),
                    "video_count": vp.get("video_count", 0),
                    "privacy_status": "private",
                    "is_virtual": True,
                    "source": vp.get("source"),
                })

        return {"playlists": result, "count": len(result)}

    async def _get_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get a specific playlist."""
        playlist_id = args["playlist_id"]

        # Check if virtual playlist
        if playlist_id.startswith("virtual_"):
            virtual = self.cache.get_virtual_playlists()
            for vp in virtual:
                if vp["id"] == playlist_id:
                    return {
                        "id": vp["id"],
                        "title": vp["title"],
                        "description": vp.get("description", ""),
                        "video_count": vp.get("video_count", 0),
                        "is_virtual": True,
                        "source": vp.get("source"),
                    }
            raise ValueError(f"Virtual playlist not found: {playlist_id}")

        # Fetch from API (playlists.list with id parameter)
        playlists = self.api_client.get_playlists()
        for p in playlists:
            if p.id == playlist_id:
                return {
                    "id": p.id,
                    "title": p.title,
                    "description": p.description,
                    "video_count": p.item_count,
                    "privacy_status": p.privacy_status.value,
                    "channel_title": p.channel_title,
                }

        raise ValueError(f"Playlist not found: {playlist_id}")

    async def _create_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Create a new playlist."""
        title = args["title"]
        description = args.get("description", "")
        privacy_status = args.get("privacy_status", "private")

        playlist = self.api_client.create_playlist(
            title=title,
            description=description,
            privacy_status=privacy_status,
        )

        return {
            "success": True,
            "playlist": {
                "id": playlist.id,
                "title": playlist.title,
                "description": playlist.description,
                "privacy_status": playlist.privacy_status.value,
            },
            "message": f"Created playlist '{title}'",
        }

    async def _delete_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Delete a playlist."""
        playlist_id = args["playlist_id"]

        self.api_client.delete_playlist(playlist_id)

        return {
            "success": True,
            "message": f"Deleted playlist {playlist_id}",
        }

    async def _rename_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Rename a playlist."""
        playlist_id = args["playlist_id"]
        new_title = args["new_title"]

        self.api_client.rename_playlist(playlist_id, new_title)

        return {
            "success": True,
            "message": f"Renamed playlist to '{new_title}'",
        }

    # --- Video Management ---

    async def _list_videos(self, args: dict[str, Any]) -> dict[str, Any]:
        """List videos in a playlist."""
        playlist_id = args["playlist_id"]
        limit = args.get("limit", 50)

        # Check if virtual playlist
        if playlist_id.startswith("virtual_"):
            videos = self.cache.get_virtual_videos(playlist_id)
            result = []
            for v in videos[:limit]:
                result.append({
                    "video_id": v.get("video_id"),
                    "title": v.get("title", "Unknown"),
                    "channel_title": v.get("channel_title", "Unknown"),
                    "added_at": v.get("added_at"),
                    "position": v.get("position", 0),
                })
            return {"videos": result, "count": len(result), "playlist_id": playlist_id}

        # Try cache first
        videos = self.cache.get_videos(playlist_id)

        if videos is None:
            # Fetch from API
            videos = self.api_client.get_playlist_items(playlist_id)
            self.cache.set_videos(playlist_id, videos)

        result = []
        for v in videos[:limit]:
            result.append({
                "video_id": v.id,
                "playlist_item_id": v.playlist_item_id,
                "title": v.title,
                "channel_title": v.channel_title,
                "position": v.position,
                "duration": v.duration,
                "added_at": str(v.added_at) if v.added_at else None,
            })

        return {"videos": result, "count": len(result), "playlist_id": playlist_id}

    async def _add_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Add a video to a playlist."""
        video_id = args["video_id"]
        playlist_id = args["playlist_id"]
        position = args.get("position")

        playlist_item_id = self.api_client.add_video_to_playlist(
            video_id=video_id,
            playlist_id=playlist_id,
            position=position,
        )

        return {
            "success": True,
            "playlist_item_id": playlist_item_id,
            "message": f"Added video {video_id} to playlist {playlist_id}",
        }

    async def _remove_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Remove a video from a playlist."""
        playlist_item_id = args["playlist_item_id"]

        self.api_client.remove_video_from_playlist(playlist_item_id)

        return {
            "success": True,
            "message": f"Removed video from playlist",
        }

    async def _move_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Move a video between playlists."""
        video_id = args["video_id"]
        source_playlist_item_id = args["source_playlist_item_id"]
        target_playlist_id = args["target_playlist_id"]

        # Create a temporary Video object for the move
        video = Video(
            id=video_id,
            playlist_item_id=source_playlist_item_id,
            title="",  # Not needed for move
            channel_title="",
        )

        new_item_id = self.api_client.move_video(video, target_playlist_id)

        return {
            "success": True,
            "new_playlist_item_id": new_item_id,
            "message": f"Moved video {video_id} to playlist {target_playlist_id}",
        }

    async def _search_videos(self, args: dict[str, Any]) -> dict[str, Any]:
        """Search videos across playlists."""
        query = args["query"].lower()
        limit = args.get("limit", 20)

        results = []

        # Search in cached playlists
        playlists = self.cache.get_playlists() or []

        for playlist in playlists:
            videos = self.cache.get_videos(playlist.id)
            if not videos:
                continue

            for video in videos:
                if query in video.title.lower():
                    results.append({
                        "video_id": video.id,
                        "playlist_item_id": video.playlist_item_id,
                        "title": video.title,
                        "playlist_id": playlist.id,
                        "playlist_title": playlist.title,
                    })

                    if len(results) >= limit:
                        break

            if len(results) >= limit:
                break

        return {"results": results, "count": len(results), "query": query}

    # --- Transcripts ---

    async def _get_transcript(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get video transcript."""
        video_id = args["video_id"]
        format_type = args.get("format", "text")

        # Initialize components if needed
        if not self.cache:
            self.cache = PersistentCache()
        if not self.transcript_fetcher:
            self.transcript_fetcher = TranscriptFetcher()

        # Check cache first
        cached = self.cache.get_transcript(video_id)
        if cached:
            if format_type == "json":
                return {
                    "video_id": video_id,
                    "transcript": json.loads(cached.get("transcript_json", "{}")),
                    "language": cached.get("language"),
                    "cached": True,
                }
            else:
                # Decompress text
                text = TranscriptFetcher.decompress_transcript(cached.get("transcript_text", b""))
                return {
                    "video_id": video_id,
                    "transcript": text,
                    "language": cached.get("language"),
                    "cached": True,
                }

        # Fetch fresh transcript
        transcript, status = self.transcript_fetcher.fetch_transcript(video_id)

        if transcript is None:
            return {
                "video_id": video_id,
                "error": status,
                "message": "Transcript not available for this video",
            }

        # Cache it
        self.cache.cache_transcript(
            video_id=video_id,
            transcript_text=TranscriptFetcher.compress_transcript(
                TranscriptFetcher.format_as_text(transcript)
            ),
            transcript_json=TranscriptFetcher.format_as_json(transcript),
            language=transcript.language,
            auto_generated=transcript.auto_generated,
            fetch_status="SUCCESS",
        )

        if format_type == "json":
            return {
                "video_id": video_id,
                "transcript": transcript.to_dict(),
                "language": transcript.language,
                "auto_generated": transcript.auto_generated,
            }
        else:
            return {
                "video_id": video_id,
                "transcript": TranscriptFetcher.format_as_text(transcript),
                "language": transcript.language,
                "auto_generated": transcript.auto_generated,
            }

    # --- Utilities ---

    async def _check_quota(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check API quota status."""
        return {
            "daily_limit": self.api_client.daily_quota,
            "used": self.api_client.quota_used,
            "remaining": self.api_client.get_quota_remaining(),
            "percentage_used": round(self.api_client.quota_used / self.api_client.daily_quota * 100, 1),
        }

    async def _get_statistics(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get statistics about playlists."""
        playlist_id = args.get("playlist_id")

        if playlist_id:
            # Stats for specific playlist
            videos = self.cache.get_videos(playlist_id)
            if videos is None:
                videos = self.api_client.get_playlist_items(playlist_id)

            return {
                "playlist_id": playlist_id,
                "video_count": len(videos),
                "channels": len(set(v.channel_title for v in videos)),
            }

        # Overall stats
        playlists = self.cache.get_playlists()
        if playlists is None:
            playlists = self.api_client.get_playlists()

        virtual = self.cache.get_virtual_playlists()

        total_videos = sum(p.item_count for p in playlists)
        total_virtual_videos = sum(vp.get("video_count", 0) for vp in virtual)

        return {
            "playlist_count": len(playlists),
            "virtual_playlist_count": len(virtual),
            "total_videos": total_videos,
            "total_virtual_videos": total_virtual_videos,
            "quota_remaining": self.api_client.get_quota_remaining(),
        }

    # --- Advanced Analysis ---

    async def _find_duplicates(self, args: dict[str, Any]) -> dict[str, Any]:
        """Find duplicate videos within or across playlists."""
        playlist_id = args.get("playlist_id")
        include_fuzzy = args.get("include_fuzzy", False)

        detector = DuplicateDetector(fuzzy_threshold=0.85 if include_fuzzy else 1.0)

        if playlist_id:
            # Find duplicates within a specific playlist
            if playlist_id.startswith("virtual_"):
                videos_data = self.cache.get_virtual_videos(playlist_id)
                videos = [
                    Video(
                        id=v["video_id"],
                        playlist_item_id=f"virtual_{v['video_id']}",
                        title=v.get("title", ""),
                        channel_title=v.get("channel_title", ""),
                    )
                    for v in videos_data
                ]
            else:
                videos = self.cache.get_videos(playlist_id)
                if videos is None:
                    videos = self.api_client.get_playlist_items(playlist_id)

            duplicates = detector.find_duplicates(videos, playlist_id)
        else:
            # Find duplicates across all playlists
            playlists = self.cache.get_playlists() or []
            playlist_videos = []

            for playlist in playlists:
                videos = self.cache.get_videos(playlist.id)
                if videos:
                    playlist_videos.append((playlist, videos))

            duplicates = detector.find_duplicates_across(playlist_videos)

        # Format results
        results = []
        for dup in duplicates:
            results.append({
                "video_id": dup.video_id,
                "match_type": dup.match_type,
                "similarity_score": dup.similarity_score,
                "occurrences": [
                    {
                        "title": v.title,
                        "playlist": playlist_name,
                        "playlist_item_id": v.playlist_item_id,
                    }
                    for v, playlist_name in dup.videos
                ],
            })

        return {
            "duplicates": results,
            "count": len(results),
            "scope": playlist_id or "all_playlists",
        }

    async def _analyze_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get comprehensive playlist analytics."""
        playlist_id = args["playlist_id"]

        # Get videos
        if playlist_id.startswith("virtual_"):
            videos_data = self.cache.get_virtual_videos(playlist_id)
            videos = [
                Video(
                    id=v["video_id"],
                    playlist_item_id=f"virtual_{v['video_id']}",
                    title=v.get("title", ""),
                    channel_title=v.get("channel_title", ""),
                    duration=v.get("duration"),
                )
                for v in videos_data
            ]
            playlist_name = playlist_id
        else:
            videos = self.cache.get_videos(playlist_id)
            if videos is None:
                videos = self.api_client.get_playlist_items(playlist_id)
            playlist_name = playlist_id

        analyzer = PlaylistAnalyzer()
        stats = analyzer.analyze(videos, playlist_name)

        return {
            "playlist_id": playlist_id,
            "total_videos": stats.total_videos,
            "total_duration_seconds": stats.total_duration_seconds,
            "total_duration_formatted": self._format_duration(stats.total_duration_seconds),
            "average_duration_seconds": round(stats.average_duration_seconds, 1),
            "unique_channels": stats.unique_channels,
            "top_channels": stats.top_channels[:10],
            "videos_by_year": stats.videos_by_year,
            "duration_distribution": stats.duration_buckets,
            "oldest_video": stats.oldest_video.title if stats.oldest_video else None,
            "newest_video": stats.newest_video.title if stats.newest_video else None,
        }

    def _format_duration(self, seconds: int) -> str:
        """Format seconds to human readable duration."""
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"

    async def _copy_videos(self, args: dict[str, Any]) -> dict[str, Any]:
        """Copy videos from one playlist to another."""
        source_playlist_id = args["source_playlist_id"]
        target_playlist_id = args["target_playlist_id"]
        video_ids = args.get("video_ids")
        limit = args.get("limit", 50)

        # Get source videos
        if source_playlist_id.startswith("virtual_"):
            videos_data = self.cache.get_virtual_videos(source_playlist_id)
            source_videos = [
                {"video_id": v["video_id"], "title": v.get("title", "")}
                for v in videos_data
            ]
        else:
            videos = self.cache.get_videos(source_playlist_id)
            if videos is None:
                videos = self.api_client.get_playlist_items(source_playlist_id)
            source_videos = [
                {"video_id": v.id, "title": v.title}
                for v in videos
            ]

        # Filter by video_ids if specified
        if video_ids:
            source_videos = [v for v in source_videos if v["video_id"] in video_ids]

        # Limit the number of videos
        source_videos = source_videos[:limit]

        # Copy to target playlist
        copied = []
        failed = []
        quota_cost = 0

        for video in source_videos:
            try:
                self.api_client.add_video_to_playlist(
                    video_id=video["video_id"],
                    playlist_id=target_playlist_id,
                )
                copied.append(video)
                quota_cost += 50  # Each add costs 50 quota units
            except QuotaExceededError:
                failed.append({"video": video, "reason": "quota_exceeded"})
                break
            except Exception as e:
                failed.append({"video": video, "reason": str(e)})

        return {
            "success": True,
            "copied_count": len(copied),
            "failed_count": len(failed),
            "copied_videos": [v["title"] for v in copied],
            "failed_videos": failed,
            "quota_used": quota_cost,
            "source": source_playlist_id,
            "target": target_playlist_id,
        }

    async def _search_transcripts(self, args: dict[str, Any]) -> dict[str, Any]:
        """Search within transcript content."""
        query = args["query"].lower()
        playlist_id = args.get("playlist_id")
        limit = args.get("limit", 10)

        # Initialize components if needed
        if not self.cache:
            self.cache = PersistentCache()

        results = []

        # Get list of videos to search
        if playlist_id:
            if playlist_id.startswith("virtual_"):
                videos_data = self.cache.get_virtual_videos(playlist_id)
                video_ids = [v["video_id"] for v in videos_data]
            else:
                videos = self.cache.get_videos(playlist_id)
                if videos:
                    video_ids = [v.id for v in videos]
                else:
                    video_ids = []
        else:
            # Search all cached transcripts
            video_ids = self._get_all_cached_transcript_ids()

        # Search in transcripts
        for video_id in video_ids:
            if len(results) >= limit:
                break

            cached = self.cache.get_transcript(video_id)
            if not cached or cached.get("fetch_status") != "SUCCESS":
                continue

            try:
                text = TranscriptFetcher.decompress_transcript(
                    cached.get("transcript_text", b"")
                ).lower()

                if query in text:
                    # Find context around the match
                    idx = text.find(query)
                    start = max(0, idx - 50)
                    end = min(len(text), idx + len(query) + 50)
                    context = text[start:end]

                    results.append({
                        "video_id": video_id,
                        "context": f"...{context}...",
                        "language": cached.get("language"),
                    })
            except Exception as e:
                logger.debug(f"Error searching transcript {video_id}: {e}")
                continue

        return {
            "results": results,
            "count": len(results),
            "query": query,
            "scope": playlist_id or "all_cached",
        }

    def _get_all_cached_transcript_ids(self) -> List[str]:
        """Get all video IDs that have cached transcripts."""
        import sqlite3
        with sqlite3.connect(self.cache.db_path) as conn:
            cursor = conn.execute(
                "SELECT video_id FROM video_transcripts WHERE fetch_status = 'SUCCESS'"
            )
            return [row[0] for row in cursor.fetchall()]

    async def _batch_fetch_transcripts(self, args: dict[str, Any]) -> dict[str, Any]:
        """Fetch transcripts for all videos in a playlist."""
        playlist_id = args["playlist_id"]
        limit = args.get("limit", 50)
        skip_cached = args.get("skip_cached", True)

        # Initialize components if needed
        if not self.cache:
            self.cache = PersistentCache()
        if not self.transcript_fetcher:
            self.transcript_fetcher = TranscriptFetcher()

        # Get videos
        if playlist_id.startswith("virtual_"):
            videos_data = self.cache.get_virtual_videos(playlist_id)
            video_ids = [v["video_id"] for v in videos_data[:limit]]
        else:
            self._ensure_auth()
            videos = self.cache.get_videos(playlist_id)
            if videos is None:
                videos = self.api_client.get_playlist_items(playlist_id)
            video_ids = [v.id for v in videos[:limit]]

        # Fetch transcripts
        fetched = []
        skipped = []
        failed = []

        for video_id in video_ids:
            # Check cache if skip_cached is enabled
            if skip_cached:
                cached = self.cache.get_transcript(video_id)
                if cached:
                    skipped.append(video_id)
                    continue

            # Fetch transcript
            transcript, status = self.transcript_fetcher.fetch_transcript(video_id)

            if transcript:
                # Cache it
                self.cache.cache_transcript(
                    video_id=video_id,
                    transcript_text=TranscriptFetcher.compress_transcript(
                        TranscriptFetcher.format_as_text(transcript)
                    ),
                    transcript_json=TranscriptFetcher.format_as_json(transcript),
                    language=transcript.language,
                    auto_generated=transcript.auto_generated,
                    fetch_status="SUCCESS",
                )
                fetched.append({
                    "video_id": video_id,
                    "language": transcript.language,
                })
            else:
                # Cache the failure status to avoid retrying
                self.cache.cache_transcript(
                    video_id=video_id,
                    transcript_text=None,
                    transcript_json=None,
                    language=None,
                    auto_generated=False,
                    fetch_status=status,
                )
                failed.append({
                    "video_id": video_id,
                    "reason": status,
                })

        return {
            "playlist_id": playlist_id,
            "fetched_count": len(fetched),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
            "fetched": fetched,
            "failed": failed,
            "message": f"Fetched {len(fetched)} transcripts, skipped {len(skipped)} cached, {len(failed)} unavailable",
        }

    # --- Fabric Integration ---

    async def _fabric_analyze(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply a Fabric pattern to analyze a video transcript."""
        video_id = args["video_id"]
        pattern = args["pattern"]
        model = args.get("model")

        # Check if fabric is installed
        fabric_path = shutil.which("fabric")
        if not fabric_path:
            return {
                "error": "fabric_not_installed",
                "message": "Fabric is not installed. Install from: https://github.com/danielmiessler/fabric",
                "install_instructions": "go install github.com/danielmiessler/fabric@latest",
            }

        # Get transcript
        transcript_result = await self._get_transcript({
            "video_id": video_id,
            "format": "text",
        })

        if "error" in transcript_result:
            return {
                "error": "transcript_unavailable",
                "message": transcript_result.get("message", "Could not get transcript"),
                "video_id": video_id,
            }

        transcript_text = transcript_result["transcript"]

        # Build fabric command
        cmd = ["fabric", "--pattern", pattern]
        if model:
            cmd.extend(["--model", model])

        try:
            # Run fabric with transcript as input
            process = subprocess.run(
                cmd,
                input=transcript_text,
                capture_output=True,
                text=True,
                timeout=120,  # 2 minute timeout
            )

            if process.returncode != 0:
                return {
                    "error": "fabric_error",
                    "message": process.stderr or "Fabric command failed",
                    "pattern": pattern,
                    "video_id": video_id,
                }

            return {
                "video_id": video_id,
                "pattern": pattern,
                "model": model,
                "result": process.stdout,
                "transcript_language": transcript_result.get("language"),
            }

        except subprocess.TimeoutExpired:
            return {
                "error": "timeout",
                "message": "Fabric analysis timed out after 2 minutes",
                "pattern": pattern,
                "video_id": video_id,
            }
        except Exception as e:
            return {
                "error": "execution_error",
                "message": str(e),
                "pattern": pattern,
                "video_id": video_id,
            }

    async def _list_fabric_patterns(self, args: dict[str, Any]) -> dict[str, Any]:
        """List available Fabric patterns."""
        # Check if fabric is installed
        fabric_path = shutil.which("fabric")
        if not fabric_path:
            return {
                "error": "fabric_not_installed",
                "message": "Fabric is not installed. Install from: https://github.com/danielmiessler/fabric",
                "common_patterns": [
                    "extract_wisdom",
                    "summarize",
                    "extract_insights",
                    "analyze_claims",
                    "extract_recommendations",
                    "create_summary",
                    "extract_ideas",
                    "analyze_paper",
                    "create_quiz",
                ],
            }

        try:
            # Run fabric --list to get patterns
            process = subprocess.run(
                ["fabric", "--list"],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if process.returncode != 0:
                # Fallback: try to list patterns from ~/.config/fabric/patterns
                patterns_dir = Path.home() / ".config" / "fabric" / "patterns"
                if patterns_dir.exists():
                    patterns = [p.name for p in patterns_dir.iterdir() if p.is_dir()]
                    return {
                        "patterns": sorted(patterns),
                        "count": len(patterns),
                        "source": "filesystem",
                    }
                return {
                    "error": "list_failed",
                    "message": process.stderr or "Could not list patterns",
                }

            # Parse output
            patterns = [
                line.strip()
                for line in process.stdout.split("\n")
                if line.strip() and not line.startswith("#")
            ]

            return {
                "patterns": patterns,
                "count": len(patterns),
                "source": "fabric --list",
            }

        except subprocess.TimeoutExpired:
            return {
                "error": "timeout",
                "message": "Listing patterns timed out",
            }
        except Exception as e:
            return {
                "error": "execution_error",
                "message": str(e),
            }

    async def _fabric_analyze_batch(self, args: dict[str, Any]) -> dict[str, Any]:
        """Apply a Fabric pattern to analyze multiple video transcripts."""
        pattern = args["pattern"]
        playlist_id = args.get("playlist_id")
        video_ids = args.get("video_ids", [])
        model = args.get("model")
        limit = args.get("limit", 10)
        skip_errors = args.get("skip_errors", True)

        # Check if fabric is installed
        fabric_path = shutil.which("fabric")
        if not fabric_path:
            return {
                "error": "fabric_not_installed",
                "message": "Fabric is not installed. Install from: https://github.com/danielmiessler/fabric",
                "install_instructions": "go install github.com/danielmiessler/fabric@latest",
            }

        # Get video IDs to analyze
        if playlist_id:
            # Initialize components if needed
            if not self.cache:
                self.cache = PersistentCache()

            # Get videos from playlist
            if playlist_id.startswith("virtual_"):
                videos_data = self.cache.get_virtual_videos(playlist_id)
                video_ids = [v["video_id"] for v in videos_data[:limit]]
            else:
                # Need auth for real playlists
                self._ensure_auth()
                videos = self.cache.get_videos(playlist_id)
                if videos is None:
                    videos = self.api_client.get_playlist_items(playlist_id)
                video_ids = [v.id for v in videos[:limit]]
        elif not video_ids:
            return {
                "error": "invalid_arguments",
                "message": "Must provide either playlist_id or video_ids",
            }

        # Apply limit
        video_ids = video_ids[:limit]

        # Process each video
        results = []
        errors = []

        for video_id in video_ids:
            try:
                result = await self._fabric_analyze({
                    "video_id": video_id,
                    "pattern": pattern,
                    "model": model,
                })

                if "error" in result:
                    errors.append({
                        "video_id": video_id,
                        "error": result["error"],
                        "message": result.get("message", "Unknown error"),
                    })
                    if not skip_errors:
                        break
                else:
                    results.append({
                        "video_id": video_id,
                        "pattern": pattern,
                        "result": result["result"],
                        "transcript_language": result.get("transcript_language"),
                    })
            except Exception as e:
                errors.append({
                    "video_id": video_id,
                    "error": "exception",
                    "message": str(e),
                })
                if not skip_errors:
                    break

        return {
            "pattern": pattern,
            "model": model,
            "total_videos": len(video_ids),
            "successful_count": len(results),
            "error_count": len(errors),
            "results": results,
            "errors": errors,
            "source": playlist_id or "video_ids",
        }

    async def run(self) -> None:
        """Run the MCP server."""
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(
                read_stream,
                write_stream,
                self.server.create_initialization_options(),
            )


def main() -> None:
    """Entry point for MCP server."""
    if not MCP_AVAILABLE:
        print("Error: MCP package not installed.")
        print("Install with: pip install 'yanger[mcp]'")
        return

    logging.basicConfig(level=logging.INFO)
    server = YangerMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
