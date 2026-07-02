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
import sys
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

from .auth import YouTubeAuth, resolve_token_file, resolve_client_secrets_file
from .api_client import YouTubeAPIClient, QuotaExceededError
from .cache import PersistentCache
from .core.transcript_fetcher import TranscriptFetcher, TERMINAL_TRANSCRIPT_STATUSES
from .core.proxy import ProxySettings as CoreProxySettings
from .config.settings import load_settings
from .models import Playlist, Video, PrivacyStatus
from .duplicates import DuplicateDetector
from .statistics import PlaylistAnalyzer


logger = logging.getLogger(__name__)

# Cap how many transcript characters a single get_transcript call returns so one
# tool call can't flood the MCP client's context window. 0 disables the cap.
DEFAULT_TRANSCRIPT_MAX_CHARS = 20000

# Max Fabric subprocesses to run at once in fabric_analyze_batch. Each Fabric call
# is a separate external process (up to 120s); bounding concurrency keeps the
# batch responsive without spawning an unbounded swarm of LLM subprocesses.
FABRIC_BATCH_CONCURRENCY = 4

# TERMINAL_TRANSCRIPT_STATUSES is imported from core.transcript_fetcher (its owner) so
# the TUI and MCP paths share one policy — see that module for the rationale.


class YangerMCPServer:
    """MCP server wrapping yanger's YouTube playlist functionality."""

    def __init__(self):
        """Initialize the MCP server with yanger components."""
        self.server = Server("yanger")
        self.api_client: Optional[YouTubeAPIClient] = None
        self.cache: Optional[PersistentCache] = None
        self.transcript_fetcher: Optional[TranscriptFetcher] = None
        self._authenticated = False
        self._settings = None
        self._proxy_settings = None

        # Register tool handlers
        self._register_tools()

    def _load_proxy_settings(self) -> Optional[CoreProxySettings]:
        """Load proxy settings from configuration."""
        if self._proxy_settings is not None:
            return self._proxy_settings

        try:
            settings = load_settings()
            self._settings = settings

            # Convert config ProxySettings to core ProxySettings
            proxy_cfg = settings.transcripts.proxy
            self._proxy_settings = CoreProxySettings(
                enabled=proxy_cfg.enabled,
                type=proxy_cfg.type,
                http_url=proxy_cfg.http_url,
                https_url=proxy_cfg.https_url,
                webshare_username=proxy_cfg.webshare_username,
                webshare_password=proxy_cfg.webshare_password,
                webshare_locations=proxy_cfg.webshare_locations,
            )

            if self._proxy_settings.enabled:
                logger.info(f"Proxy enabled: {self._proxy_settings.get_display_info()}")

            return self._proxy_settings

        except Exception as e:
            logger.warning(f"Failed to load proxy settings: {e}")
            return None

    def _ensure_auth(self) -> None:
        """Ensure YouTube API client is authenticated.

        Resolves token/secret to absolute paths so the server is cwd-independent,
        and fails fast (rather than launching an interactive browser OAuth flow,
        which would hang a headless MCP client) when no token is present.
        """
        if self._authenticated and self.api_client:
            return

        try:
            settings = self._settings or load_settings()
            self._settings = settings
            secrets_cfg = getattr(settings.youtube, "client_secrets_file", None)
            token_cfg = getattr(settings.youtube, "token_file", None)
        except Exception as e:
            logger.warning("Failed to load settings for auth paths: %s", e)
            secrets_cfg = token_cfg = None

        # Use the shared resolver so this matches exactly where `yanger auth` writes.
        client_secrets = resolve_client_secrets_file(secrets_cfg)
        token_file = resolve_token_file(token_cfg)

        # Never trigger the interactive OAuth flow from here: an MCP client has
        # no console to complete it, so a missing token must surface as an error.
        if not token_file.exists():
            raise RuntimeError(
                f"Not authenticated — run `yanger auth` first (expected token at {token_file})"
            )

        auth = YouTubeAuth(
            client_secrets_file=str(client_secrets),
            token_file=str(token_file),
        )
        auth.authenticate()
        self.api_client = YouTubeAPIClient(auth)
        self.cache = PersistentCache()

        # Load proxy settings and create transcript fetcher
        proxy_settings = self._load_proxy_settings()
        self.transcript_fetcher = TranscriptFetcher(proxy_settings=proxy_settings)
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
                            "playlist_id": {
                                "type": "string",
                                "description": "The playlist the item belongs to. Optional, but "
                                               "enables a targeted cache refresh instead of a full one.",
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
                            "source_playlist_id": {
                                "type": "string",
                                "description": "The source playlist ID. Optional, but lets the "
                                               "source playlist's cache be refreshed too.",
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
                            "max_chars": {
                                "type": "integer",
                                "description": "Max transcript characters to return (text format); "
                                               "longer transcripts are truncated with a note so the "
                                               "result can't flood context. Use 0 for no limit.",
                                "default": DEFAULT_TRANSCRIPT_MAX_CHARS,
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
            # First-call auth does a blocking OAuth token refresh + client/cache
            # construction; keep it off the event loop.
            await asyncio.to_thread(self._ensure_auth)

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

    def _invalidate_cache(self, *playlist_ids: Optional[str],
                          playlists_list: bool = False) -> None:
        """Drop stale cache entries after an API mutation.

        The SQLite cache (7-day TTL) is shared with the TUI, so a mutation that
        isn't reflected here resurfaces as stale data on the next list_* read.

        Args:
            playlist_ids: Playlists whose video lists changed. None entries are
                ignored, since some callers only know a source/target lazily.
            playlists_list: Set when the playlist collection itself changed
                (create/delete/rename); this also clears every cached video list.
        """
        if not self.cache:
            return
        if playlists_list:
            self.cache.invalidate_playlists_cache()
        for playlist_id in playlist_ids:
            if playlist_id:
                self.cache.invalidate_playlist(playlist_id)

    # --- Playlist Management ---

    async def _list_playlists(self, args: dict[str, Any]) -> dict[str, Any]:
        """List all playlists."""
        include_virtual = args.get("include_virtual", False)

        # Try cache first. SQLite/network work is offloaded to a worker thread so
        # it never blocks the asyncio event loop (stdio reads, pings, cancel).
        playlists = await asyncio.to_thread(self.cache.get_playlists)

        if playlists is None:
            # Fetch from API
            playlists = await asyncio.to_thread(self.api_client.get_playlists)
            await asyncio.to_thread(self.cache.set_playlists, playlists)

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
            virtual = await asyncio.to_thread(self.cache.get_virtual_playlists)
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
            virtual = await asyncio.to_thread(self.cache.get_virtual_playlists)
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
        playlists = await asyncio.to_thread(self.api_client.get_playlists)
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

        playlist = await asyncio.to_thread(
            self.api_client.create_playlist,
            title=title,
            description=description,
            privacy_status=privacy_status,
        )

        # The playlist collection changed; force list_playlists to refetch.
        await asyncio.to_thread(self._invalidate_cache, playlists_list=True)

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

        await asyncio.to_thread(self.api_client.delete_playlist, playlist_id)

        # Collection changed; this also clears the deleted playlist's videos.
        await asyncio.to_thread(self._invalidate_cache, playlist_id, playlists_list=True)

        return {
            "success": True,
            "message": f"Deleted playlist {playlist_id}",
        }

    async def _rename_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Rename a playlist."""
        playlist_id = args["playlist_id"]
        new_title = args["new_title"]

        await asyncio.to_thread(self.api_client.rename_playlist, playlist_id, new_title)

        # The cached title is stale; refetch the playlist collection.
        await asyncio.to_thread(self._invalidate_cache, playlist_id, playlists_list=True)

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
            videos = await asyncio.to_thread(self.cache.get_virtual_videos, playlist_id)
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
        videos = await asyncio.to_thread(self.cache.get_videos, playlist_id)

        if videos is None:
            # Fetch from API
            videos = await asyncio.to_thread(self.api_client.get_playlist_items, playlist_id)
            await asyncio.to_thread(self.cache.set_videos, playlist_id, videos)

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

        playlist_item_id = await asyncio.to_thread(
            self.api_client.add_video_to_playlist,
            video_id=video_id,
            playlist_id=playlist_id,
            position=position,
        )

        # The new video must show up in list_videos for this playlist.
        await asyncio.to_thread(self._invalidate_cache, playlist_id)

        return {
            "success": True,
            "playlist_item_id": playlist_item_id,
            "message": f"Added video {video_id} to playlist {playlist_id}",
        }

    async def _remove_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Remove a video from a playlist."""
        playlist_item_id = args["playlist_item_id"]
        playlist_id = args.get("playlist_id")

        await asyncio.to_thread(self.api_client.remove_video_from_playlist, playlist_item_id)

        # Refresh so the removed video disappears from list_videos. Without a
        # known playlist we must clear the whole playlists cache to stay correct.
        if playlist_id:
            await asyncio.to_thread(self._invalidate_cache, playlist_id)
        else:
            await asyncio.to_thread(self._invalidate_cache, playlists_list=True)

        return {
            "success": True,
            "message": f"Removed video from playlist",
        }

    async def _move_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Move a video between playlists."""
        video_id = args["video_id"]
        source_playlist_item_id = args["source_playlist_item_id"]
        target_playlist_id = args["target_playlist_id"]
        source_playlist_id = args.get("source_playlist_id")

        # Create a temporary Video object for the move
        video = Video(
            id=video_id,
            playlist_item_id=source_playlist_item_id,
            title="",  # Not needed for move
            channel_title="",
        )

        new_item_id = await asyncio.to_thread(self.api_client.move_video, video, target_playlist_id)

        # Both ends changed; source_playlist_id is optional so may be skipped.
        await asyncio.to_thread(self._invalidate_cache, target_playlist_id, source_playlist_id)

        return {
            "success": True,
            "new_playlist_item_id": new_item_id,
            "message": f"Moved video {video_id} to playlist {target_playlist_id}",
        }

    async def _search_videos(self, args: dict[str, Any]) -> dict[str, Any]:
        """Search videos across playlists."""
        query = args["query"].lower()
        limit = args.get("limit", 20)

        # The whole scan is one SQLite read per playlist; run it off the event
        # loop so a large cache can't block stdio/cancellation.
        results = await asyncio.to_thread(self._search_videos_blocking, query, limit)

        return {"results": results, "count": len(results), "query": query}

    def _search_videos_blocking(self, query: str, limit: int) -> List[dict[str, Any]]:
        """Synchronous cache scan for _search_videos (runs in a worker thread)."""
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

        return results

    # --- Transcripts ---

    @staticmethod
    def _truncate_transcript(text: str, max_chars: Optional[int]) -> tuple[str, bool]:
        """Cap transcript length so one tool call can't flood client context.

        Returns the (possibly truncated) text and whether truncation occurred.
        A falsy ``max_chars`` (None/0) means no limit.
        """
        if max_chars and len(text) > max_chars:
            omitted = len(text) - max_chars
            note = f"\n\n[... truncated {omitted} chars; pass a higher max_chars to get more]"
            return text[:max_chars] + note, True
        return text, False

    async def _get_transcript(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get video transcript."""
        video_id = args["video_id"]
        format_type = args.get("format", "text")
        max_chars = args.get("max_chars", DEFAULT_TRANSCRIPT_MAX_CHARS)

        # Initialize components if needed
        if not self.cache:
            self.cache = PersistentCache()
        if not self.transcript_fetcher:
            proxy_settings = self._load_proxy_settings()
            self.transcript_fetcher = TranscriptFetcher(proxy_settings=proxy_settings)

        # Check cache first (SQLite read offloaded off the event loop)
        cached = await asyncio.to_thread(self.cache.get_transcript, video_id)
        if cached:
            # A cached failure (e.g. NOT_AVAILABLE) stores no transcript body, so
            # surface it instead of json.loads(None) / an empty text dump.
            if cached.get("fetch_status") != "SUCCESS":
                return {
                    "video_id": video_id,
                    "error": cached.get("fetch_status") or "NOT_AVAILABLE",
                    "message": "Transcript not available",
                }
            if format_type == "json":
                return {
                    "video_id": video_id,
                    "transcript": json.loads(cached.get("transcript_json", "{}")),
                    "language": cached.get("language"),
                    "cached": True,
                }
            # Decompress text
            text = TranscriptFetcher.decompress_transcript(cached.get("transcript_text", b""))
            text, truncated = self._truncate_transcript(text, max_chars)
            return {
                "video_id": video_id,
                "transcript": text,
                "language": cached.get("language"),
                "cached": True,
                "truncated": truncated,
            }

        # Fetch fresh transcript (network I/O — must not block the event loop)
        transcript, status = await asyncio.to_thread(
            self.transcript_fetcher.fetch_transcript, video_id
        )

        if transcript is None:
            return {
                "video_id": video_id,
                "error": status,
                "message": "Transcript not available for this video",
            }

        # Cache it (SQLite write offloaded off the event loop)
        await asyncio.to_thread(
            self.cache.cache_transcript,
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
        text, truncated = self._truncate_transcript(
            TranscriptFetcher.format_as_text(transcript), max_chars
        )
        return {
            "video_id": video_id,
            "transcript": text,
            "language": transcript.language,
            "auto_generated": transcript.auto_generated,
            "truncated": truncated,
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
            videos = await asyncio.to_thread(self.cache.get_videos, playlist_id)
            if videos is None:
                videos = await asyncio.to_thread(self.api_client.get_playlist_items, playlist_id)

            return {
                "playlist_id": playlist_id,
                "video_count": len(videos),
                "channels": len(set(v.channel_title for v in videos)),
            }

        # Overall stats
        playlists = await asyncio.to_thread(self.cache.get_playlists)
        if playlists is None:
            playlists = await asyncio.to_thread(self.api_client.get_playlists)

        virtual = await asyncio.to_thread(self.cache.get_virtual_playlists)

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

        # Cache reads plus CPU-bound fuzzy matching — run off the event loop.
        results = await asyncio.to_thread(
            self._find_duplicates_blocking, playlist_id, include_fuzzy
        )

        return {
            "duplicates": results,
            "count": len(results),
            "scope": playlist_id or "all_playlists",
        }

    def _find_duplicates_blocking(self, playlist_id: Optional[str],
                                  include_fuzzy: bool) -> List[dict[str, Any]]:
        """Synchronous duplicate detection for _find_duplicates (worker thread)."""
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

        return results

    async def _analyze_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Get comprehensive playlist analytics."""
        playlist_id = args["playlist_id"]

        # Cache/API reads plus CPU-bound analysis — run off the event loop.
        return await asyncio.to_thread(self._analyze_playlist_blocking, playlist_id)

    def _analyze_playlist_blocking(self, playlist_id: str) -> dict[str, Any]:
        """Synchronous playlist analysis for _analyze_playlist (worker thread)."""
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

        # Reads, the per-video API add loop, and cache invalidation are all
        # blocking; run the whole copy off the event loop in one worker thread.
        return await asyncio.to_thread(
            self._copy_videos_blocking,
            source_playlist_id, target_playlist_id, video_ids, limit,
        )

    def _copy_videos_blocking(self, source_playlist_id: str, target_playlist_id: str,
                              video_ids: Optional[List[str]], limit: int) -> dict[str, Any]:
        """Synchronous copy loop for _copy_videos (runs in a worker thread)."""
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

        # Newly copied videos must appear in the target playlist's list_videos.
        if copied:
            self._invalidate_cache(target_playlist_id)

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

        # Cache reads plus gzip decompression of each transcript — offload it all.
        results = await asyncio.to_thread(
            self._search_transcripts_blocking, query, playlist_id, limit
        )

        return {
            "results": results,
            "count": len(results),
            "query": query,
            "scope": playlist_id or "all_cached",
        }

    def _search_transcripts_blocking(self, query: str, playlist_id: Optional[str],
                                     limit: int) -> List[dict[str, Any]]:
        """Synchronous transcript search for _search_transcripts (worker thread)."""
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

        return results

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

        # Auth, list fetch, and a per-video network fetch + SQLite write loop are
        # all blocking; run the whole batch off the event loop.
        return await asyncio.to_thread(
            self._batch_fetch_transcripts_blocking, playlist_id, limit, skip_cached
        )

    def _batch_fetch_transcripts_blocking(self, playlist_id: str, limit: int,
                                          skip_cached: bool) -> dict[str, Any]:
        """Synchronous batch transcript fetch (runs in a worker thread)."""
        # Initialize components if needed
        if not self.cache:
            self.cache = PersistentCache()
        if not self.transcript_fetcher:
            proxy_settings = self._load_proxy_settings()
            self.transcript_fetcher = TranscriptFetcher(proxy_settings=proxy_settings)

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
                # Cache ONLY terminal failures (NOT_AVAILABLE). Transient failures
                # (IP_BLOCKED / ERROR) are left uncached so a later run — e.g. after
                # the user configures a proxy — can retry them.
                if status in TERMINAL_TRANSCRIPT_STATUSES:
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

        # Get transcript (uncapped: fabric needs the full text, not a
        # context-capped excerpt, and the output goes to the subprocess).
        transcript_result = await self._get_transcript({
            "video_id": video_id,
            "format": "text",
            "max_chars": 0,
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
            # Run fabric with transcript as input. The subprocess (up to 120s)
            # runs in a worker thread so it never blocks the asyncio event loop.
            process = await asyncio.to_thread(
                subprocess.run,
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
            # Run fabric --list to get patterns (subprocess off the event loop)
            process = await asyncio.to_thread(
                subprocess.run,
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

            # Get videos from playlist (blocking reads offloaded off the loop)
            if playlist_id.startswith("virtual_"):
                videos_data = await asyncio.to_thread(self.cache.get_virtual_videos, playlist_id)
                video_ids = [v["video_id"] for v in videos_data[:limit]]
            else:
                # Need auth for real playlists
                await asyncio.to_thread(self._ensure_auth)
                videos = await asyncio.to_thread(self.cache.get_videos, playlist_id)
                if videos is None:
                    videos = await asyncio.to_thread(self.api_client.get_playlist_items, playlist_id)
                video_ids = [v.id for v in videos[:limit]]
        elif not video_ids:
            return {
                "error": "invalid_arguments",
                "message": "Must provide either playlist_id or video_ids",
            }

        # Apply limit
        video_ids = video_ids[:limit]

        # Process each video. Each _fabric_analyze is an independent ~120s Fabric
        # subprocess (offloaded internally via to_thread), so fan them out with
        # bounded concurrency rather than fully serially — a batch no longer
        # freezes the server for minutes, without spawning an unbounded swarm.
        results = []
        errors = []

        def _record(video_id: str, result: Any) -> bool:
            """Sort one analyze result into results/errors. Returns True on error."""
            if isinstance(result, Exception):
                errors.append({
                    "video_id": video_id,
                    "error": "exception",
                    "message": str(result),
                })
                return True
            if "error" in result:
                errors.append({
                    "video_id": video_id,
                    "error": result["error"],
                    "message": result.get("message", "Unknown error"),
                })
                return True
            results.append({
                "video_id": video_id,
                "pattern": pattern,
                "result": result["result"],
                "transcript_language": result.get("transcript_language"),
            })
            return False

        if skip_errors:
            # Fan out under a semaphore; gather preserves input ordering.
            semaphore = asyncio.Semaphore(FABRIC_BATCH_CONCURRENCY)

            async def analyze_one(video_id: str) -> dict[str, Any]:
                async with semaphore:
                    return await self._fabric_analyze({
                        "video_id": video_id,
                        "pattern": pattern,
                        "model": model,
                    })

            analyses = await asyncio.gather(
                *(analyze_one(vid) for vid in video_ids),
                return_exceptions=True,
            )
            for video_id, result in zip(video_ids, analyses):
                _record(video_id, result)
        else:
            # Fail-fast mode: stop at the first failure, so process serially.
            for video_id in video_ids:
                try:
                    result: Any = await self._fabric_analyze({
                        "video_id": video_id,
                        "pattern": pattern,
                        "model": model,
                    })
                except Exception as e:
                    result = e
                if _record(video_id, result):
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
        # stdout is the JSON-RPC channel for the MCP stdio server; diagnostics
        # must go to stderr to avoid corrupting it.
        print("Error: MCP package not installed.", file=sys.stderr)
        print("Install with: pip install 'yanger[mcp]'", file=sys.stderr)
        return

    logging.basicConfig(level=logging.INFO)
    server = YangerMCPServer()
    asyncio.run(server.run())


if __name__ == "__main__":
    main()
