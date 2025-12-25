"""MCP (Model Context Protocol) server for YouTube Ranger.

Exposes yanger's playlist management capabilities via MCP, enabling
Claude and other MCP-compatible tools to manage YouTube playlists.

This module reuses existing yanger components:
- api_client.py for YouTube API operations
- cache.py for SQLite caching
- auth.py for OAuth2 authentication
- core/transcript_fetcher.py for transcripts
"""
# Created: 2025-12-25

import asyncio
import json
import logging
from typing import Any, Optional
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
        # Most tools require authentication
        if name not in ["get_transcript"]:
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
