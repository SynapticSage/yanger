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
import re
import subprocess
import shutil
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional, List
from urllib.parse import unquote
from dataclasses import asdict

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool,
        TextContent,
        CallToolResult,
        Resource,
        Prompt,
        PromptArgument,
        PromptMessage,
        GetPromptResult,
    )
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False

# Elicitation (a server-to-client "ask the user" request) arrived in later SDKs. On older ones
# (mcp 0.9.1 is a real install of this package) the type is absent and no client can be asked.
try:
    from mcp.types import ElicitationCapability
except ImportError:
    ElicitationCapability = None

# Sampling (a server-to-client "run a completion on your model" request). These types exist on
# mcp 0.9.1 and 1.25, but stay guarded: a version pin is not a runtime guarantee.
try:
    from mcp.types import SamplingCapability, SamplingMessage, ModelPreferences, ModelHint
except ImportError:
    SamplingCapability = None

# Resource templates (resources/templates/list) and the typed read_resource return arrived after
# the first SDKs; without them templates are not advertised and a read returns bare text.
try:
    from mcp.types import ResourceTemplate
except ImportError:
    ResourceTemplate = None
try:
    from mcp.server.lowlevel.helper_types import ReadResourceContents
except ImportError:
    ReadResourceContents = None

from .auth import YouTubeAuth, resolve_token_file, resolve_client_secrets_file
from .api_client import YouTubeAPIClient, QuotaExceededError
from .cache import PersistentCache
from .core.transcript_fetcher import (
    TranscriptFetcher,
    fetch_and_cache_transcript,
    should_refetch,
)
from .core.proxy import ProxySettings as CoreProxySettings
from .config.settings import load_settings
from .core.custom_command import (
    load_command_registry, resolve_commands, run_command_capture,
)
from .models import Playlist, Video, PrivacyStatus, is_valid_video_id
from .duplicates import DuplicateDetector
from .operation_history import (
    OperationStack, Operation, PasteOperation, CreatePlaylistOperation, RenameOperation,
    DeleteVideosOperation,
)
from .statistics import PlaylistAnalyzer


logger = logging.getLogger(__name__)

# Cap how many transcript characters a single get_transcript call returns so one
# tool call can't flood the MCP client's context window. 0 disables the cap.
DEFAULT_TRANSCRIPT_MAX_CHARS = 20000

# Cap the yanger://transcripts index (one row per cached transcript) for the same reason.
TRANSCRIPT_INDEX_MAX = 1000

# Max Fabric subprocesses to run at once in fabric_analyze_batch. Each Fabric call
# is a separate external process (up to 120s); bounding concurrency keeps the
# batch responsive without spawning an unbounded swarm of LLM subprocesses.
FABRIC_BATCH_CONCURRENCY = 4

# run_custom_command fan-out cap. The TUI *confirms* before running on more than this many videos
# (app.py RUN_CONFIRM_THRESHOLD=5); over MCP we REJECT beyond the cap rather than run a large batch
# unconfirmed. Kept even for clients that support elicitation: it also bounds worst-case wall time
# (per-video mode is N sequential subprocesses x the capture timeout).
CUSTOM_COMMAND_MAX_VIDEOS = 5
CUSTOM_COMMAND_MAX_OUTPUT_CHARS = 20000  # default per-stream (stdout/stderr) truncation

# The one-field form an elicitation-capable client shows before a gated action. Requiring the box
# to be ticked (on top of the client's "accept") makes an empty or partial answer a refusal.
CONFIRM_SCHEMA = {
    "type": "object",
    "properties": {
        "confirm": {
            "type": "boolean",
            "title": "Confirm",
            "description": "Tick to approve this action",
            "default": False,
        },
    },
    "required": ["confirm"],
}
# How long to wait for the human's answer. The SDK sets no read timeout on elicit_form, so a client
# that declares elicitation but never answers would hang the tool call forever; past this, refuse.
CONFIRM_TIMEOUT_SECONDS = 300

# fabric_analyze's sampling backend (the connected client's model runs the pattern prompt).
SAMPLING_MAX_TOKENS = 4096
# The client may ask its human to approve each sampling request and the SDK sets no read timeout,
# so (as with CONFIRM_TIMEOUT_SECONDS) an unanswered request would otherwise hang the tool call.
SAMPLING_TIMEOUT_SECONDS = 300

FABRIC_NOT_INSTALLED = {
    "error": "fabric_not_installed",
    "message": "Fabric is not installed. Install from: https://github.com/danielmiessler/fabric",
    "install_instructions": "go install github.com/danielmiessler/fabric@latest",
}


BACKEND_DESCRIPTION = (
    "Runs on the connected client's own model via MCP sampling when the client supports it "
    "(no local Fabric or API key needed; uses the pattern prompts in "
    "~/.config/fabric/patterns/<name>/system.md), otherwise with local Fabric.")
BACKEND_SCHEMA = {
    "type": "string",
    "enum": ["auto", "fabric", "sampling"],
    "default": "auto",
    "description": "auto: the client's model via MCP sampling if it supports it, else local "
                   "Fabric. fabric / sampling force one (an error if it is unavailable).",
}


def _fabric_patterns_dir() -> Path:
    """Where Fabric keeps its pattern prompts (<name>/system.md); the sampling backend reads them."""
    return Path.home() / ".config" / "fabric" / "patterns"


def _one_line(text: Any, limit: int = 120) -> str:
    """Flatten untrusted text (video titles, LLM-supplied ids) for a confirmation prompt: collapse
    all whitespace so it cannot fake extra server-written lines, then truncate."""
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 3] + "..."

# Transcript fetch+cache and its terminal-vs-transient policy now live in one place:
# core.transcript_fetcher.fetch_and_cache_transcript (Tier 1 #1). Callers here just invoke it.


class YangerMCPServer:
    """MCP server wrapping yanger's YouTube playlist functionality."""

    def __init__(self):
        """Initialize the MCP server with yanger components."""
        self.server = Server("yanger")
        self.api_client: Optional[YouTubeAPIClient] = None
        self.cache: Optional[PersistentCache] = None
        self._resource_only_cache: Optional[PersistentCache] = None  # see _resource_cache
        self.transcript_fetcher: Optional[TranscriptFetcher] = None
        self._authenticated = False
        self._settings = None
        self._proxy_settings = None
        # Undo/redo history (ROADMAP 2.2): in memory, for the life of this server process (one MCP
        # session over stdio), like the TUI's own stack; not shared with the TUI, not persisted.
        self._operation_stack = OperationStack()
        # Held by each recorded mutation across its API call AND its record, and by undo/redo
        # across theirs, so an undo never runs between a mutation's call and its record.
        self._history_lock = asyncio.Lock()
        # playlist_item_id -> (playlist_id, Video), from every cache read and list_videos of this
        # session: undo data that survives the cache invalidation each mutation does. A YouTube
        # playlist item id never changes playlist or video, so an entry is never wrong; at worst
        # the item is gone, and then the mutation's own API call fails and nothing is recorded.
        self._seen_items: dict[str, tuple[str, Video]] = {}

        # Register tool handlers, then the read-only resources and prompts (ROADMAP 2.3)
        self._register_tools()
        self._register_resources()
        self._register_prompts()

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
        # Cache first so it can back the shared, cross-process quota counter.
        self.cache = PersistentCache()
        self.api_client = YouTubeAPIClient(auth, quota_store=self.cache)

        # Load proxy settings and create transcript fetcher
        proxy_settings = self._load_proxy_settings()
        self.transcript_fetcher = TranscriptFetcher(proxy_settings=proxy_settings)
        self._authenticated = True

    def _register_tools(self) -> None:
        """Register all MCP tools."""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """Return list of available tools."""
            static_tools = [
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
                                "analyze_claims, extract_recommendations, create_summary. "
                                + BACKEND_DESCRIPTION,
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
                            "backend": BACKEND_SCHEMA,
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
                                "or a list of video_ids. Returns aggregated results for each video. "
                                + BACKEND_DESCRIPTION,
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
                            "backend": BACKEND_SCHEMA,
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

                # Undo / redo (ROADMAP 2.2)
                Tool(
                    name="undo",
                    description="Undo the most recent change made through THIS MCP session: "
                                "create/rename playlist, or add/remove/move/copy videos (only "
                                "those whose result said undoable: true). Not shared with the "
                                "yanger TUI's u/U. Always asks the user to confirm and is refused "
                                "when the client cannot ask. Undoing a playlist creation DELETES "
                                "that playlist.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="redo",
                    description="Redo the most recently undone change of THIS MCP session (see "
                                "undo). Always asks the user to confirm and is refused when the "
                                "client cannot ask.",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

            # Strictly additive: the gated custom-command tool is APPENDED, and any failure to
            # probe the gate/registry must never break discovery of the ~20 static tools above.
            try:
                static_tools.extend(self._custom_command_tools())
            except Exception:
                logger.warning("Failed to probe custom-command tool gate; omitting it", exc_info=True)
            return static_tools

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
            "fabric_analyze", "list_fabric_patterns", "fabric_analyze_batch",
            "run_custom_command",  # runs shell on video ids; never calls the YouTube API
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

            # Custom commands (opt-in; see MCPSettings.allow_custom_commands)
            "run_custom_command": self._run_custom_command,

            # Undo / redo of this session's recorded mutations (ROADMAP 2.2)
            "undo": self._undo,
            "redo": self._redo,
        }

        handler = handlers.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")

        return await handler(arguments)

    # ----- Resources + prompts (ROADMAP 2.3): a read-only, CACHE-ONLY surface ------------
    #
    # Nothing here calls _ensure_auth, the YouTube API or the transcript fetcher: a resource read
    # or prompt fetch never spends quota, needs a token or touches the network. What is not
    # cached is reported as such, naming the tool that would fetch it.

    def _register_resources(self) -> None:
        """Register resources/list, resources/templates/list and resources/read."""

        @self.server.list_resources()
        async def list_resources() -> list[Resource]:
            return await self._list_resources()

        if ResourceTemplate is not None and hasattr(self.server, "list_resource_templates"):
            @self.server.list_resource_templates()
            async def list_resource_templates() -> list[ResourceTemplate]:
                return self._list_resource_templates()

        @self.server.read_resource()
        async def read_resource(uri):
            text, mime = await self._read_resource(str(uri))
            if ReadResourceContents is None:  # old SDK: bare text only
                return text
            return [ReadResourceContents(content=text, mime_type=mime)]

    def _register_prompts(self) -> None:
        """Register prompts/list and prompts/get (Fabric patterns)."""

        @self.server.list_prompts()
        async def list_prompts() -> list[Prompt]:
            return await self._list_prompts()

        @self.server.get_prompt()
        async def get_prompt(name: str, arguments: Optional[dict[str, str]]) -> GetPromptResult:
            return await self._get_prompt(name, arguments)

    def _resource_cache(self) -> PersistentCache:
        """The tools' cache if one exists, else a lazily built one without the startup cleanup
        DELETE, so a resource read deletes nothing (it still inits the schema and WAL mode, and
        get_videos bumps access stats). Kept out of self.cache so a later tool path still builds
        its cleanup-on-start cache. Never authenticates."""
        if self.cache:
            return self.cache
        if not self._resource_only_cache:
            self._resource_only_cache = PersistentCache(auto_cleanup=False)
        return self._resource_only_cache

    def _commands_exposed(self) -> bool:
        """The custom-command opt-in (MCPSettings.allow_custom_commands). Raises if the settings
        cannot be loaded; callers fail closed."""
        settings = self._settings or load_settings()
        self._settings = settings
        return bool(getattr(getattr(settings, "mcp", None), "allow_custom_commands", False))

    async def _list_resources(self) -> list[Resource]:
        cache = await asyncio.to_thread(self._resource_cache)
        playlists = await asyncio.to_thread(cache.get_playlists) or []
        virtual = await asyncio.to_thread(cache.get_virtual_playlists)
        resources = [Resource(
            uri="yanger://playlists", name="Playlists", mimeType="application/json",
            description="All playlists as last cached (the list_playlists tool refreshes them)")]
        for p in playlists:
            resources.append(Resource(
                uri=f"yanger://playlists/{p.id}", name=p.title, mimeType="application/json",
                description=f"Videos of playlist '{p.title}' as last cached"))
        for vp in virtual:
            resources.append(Resource(
                uri=f"yanger://playlists/virtual_{vp['id']}", name=vp["title"],
                mimeType="application/json",
                description=f"Videos of local virtual playlist '{vp['title']}'"))
        resources.append(Resource(
            uri="yanger://transcripts", name="Cached transcripts", mimeType="application/json",
            description="Index of cached transcripts; read yanger://transcripts/{video_id}"))
        # Strictly additive, as in list_tools: a broken config drops only this entry.
        try:
            commands = await asyncio.to_thread(self._commands_exposed)
        except Exception:
            logger.warning("Failed to probe custom-command gate; omitting yanger://commands",
                           exc_info=True)
            commands = False
        if commands:
            resources.append(Resource(
                uri="yanger://commands", name="Custom commands", mimeType="application/json",
                description="Names, modes and confirm flags of the user's custom commands"))
        return resources

    @staticmethod
    def _list_resource_templates() -> list:
        return [
            ResourceTemplate(
                uriTemplate="yanger://playlists/{playlist_id}", name="Playlist videos",
                mimeType="application/json",
                description="Videos of one playlist as last cached (virtual playlists: virtual_<id>)"),
            ResourceTemplate(
                uriTemplate="yanger://transcripts/{video_id}", name="Cached transcript",
                mimeType="text/plain",
                description="One video's cached transcript text (the get_transcript tool fetches "
                            "a missing one)"),
        ]

    async def _read_resource(self, uri: str) -> tuple[str, str]:
        """``(text, mime type)`` for a yanger:// URI; ValueError for anything else."""
        scheme, sep, rest = uri.partition("://")
        kind, _, path = rest.partition("/")
        parts = [unquote(seg) for seg in path.strip("/").split("/")] if path.strip("/") else []
        if (scheme != "yanger" or not sep or kind not in ("playlists", "transcripts", "commands")
                or len(parts) > 1 or (kind == "commands" and parts)):
            raise ValueError(f"Unknown resource URI {uri!r}")
        ident = parts[0] if parts else None
        cache = await asyncio.to_thread(self._resource_cache)
        if kind == "playlists":
            if ident is None:
                data = await asyncio.to_thread(self._read_playlists, cache)
            else:
                data = await asyncio.to_thread(self._read_playlist, cache, ident)
            return json.dumps(data, indent=2, default=str), "application/json"
        if kind == "transcripts":
            if ident is None:
                data = await asyncio.to_thread(self._read_transcript_index, cache)
                return json.dumps(data, indent=2, default=str), "application/json"
            text = await asyncio.to_thread(self._cached_transcript_text, cache, ident)
            text, _ = self._truncate_transcript(
                text, DEFAULT_TRANSCRIPT_MAX_CHARS,
                more="the get_transcript tool's max_chars returns more")
            return text, "text/plain"
        # commands: the gate is re-checked on read (defense-in-depth); a load failure raises.
        if not await asyncio.to_thread(self._commands_exposed):
            raise ValueError("Custom commands are not exposed over MCP "
                             "(mcp.allow_custom_commands is off)")
        registry = await asyncio.to_thread(load_command_registry, self._settings)
        # Templates are left out: the opt-in is consent to RUN commands, and templates (notably
        # YANGER_CMD_* from the launch environment) may hold secrets.
        data = {"commands": [{"name": spec.name, "mode": spec.mode, "confirm": spec.confirm}
                             for spec in sorted(registry.values(), key=lambda c: c.name)]}
        return json.dumps(data, indent=2), "application/json"

    def _read_playlists(self, cache: PersistentCache) -> dict[str, Any]:
        playlists = cache.get_playlists()
        result = [self._playlist_dict(p) for p in playlists or []]
        for vp in cache.get_virtual_playlists():
            result.append({
                "id": f"virtual_{vp['id']}",  # the TUI's convention (app.py)
                "title": vp["title"],
                "description": vp["description"] or "",
                "video_count": vp["video_count"] or 0,
                "privacy_status": "private",
                "is_virtual": True,
                "source": vp["source"],
            })
        out: dict[str, Any] = {"playlists": result, "count": len(result),
                               "cached": playlists is not None}
        if playlists is None:
            out["note"] = ("Playlist list not in the local cache (or expired); the list_playlists "
                           "tool refreshes it (spends YouTube API quota).")
        return out

    def _read_playlist(self, cache: PersistentCache, playlist_id: str) -> dict[str, Any]:
        if playlist_id.startswith("virtual_"):
            raw_id = playlist_id[len("virtual_"):]
            if raw_id not in {vp["id"] for vp in cache.get_virtual_playlists()}:
                raise ValueError(f"No virtual playlist {playlist_id!r} in the local cache")
            videos = [{
                "video_id": v["video_id"],
                "title": v["title"],
                "channel_title": v["channel_title"],
                "added_at": v["added_at"],
                "position": v["position"],
            } for v in cache.get_virtual_videos(raw_id)]
            return {"playlist_id": playlist_id, "is_virtual": True, "cached": True,
                    "videos": videos, "count": len(videos)}
        videos = cache.get_videos(playlist_id)
        if videos is None:
            return {"playlist_id": playlist_id, "cached": False, "videos": [], "count": 0,
                    "note": ("Videos not in the local cache (or expired); the list_videos tool "
                             "fetches them (spends YouTube API quota).")}
        result = [self._video_dict(v) for v in videos]
        return {"playlist_id": playlist_id, "cached": True, "videos": result,
                "count": len(result)}

    @staticmethod
    def _read_transcript_index(cache: PersistentCache) -> dict[str, Any]:
        with closing(cache.db_connection()) as conn:
            rows = conn.execute(
                "SELECT video_id, language, fetched_at FROM video_transcripts "
                "WHERE fetch_status = 'SUCCESS' ORDER BY fetched_at DESC LIMIT ?",
                (TRANSCRIPT_INDEX_MAX + 1,)).fetchall()
        truncated = len(rows) > TRANSCRIPT_INDEX_MAX
        entries = [{"video_id": vid, "language": lang, "fetched_at": at,
                    "uri": f"yanger://transcripts/{vid}"}
                   for vid, lang, at in rows[:TRANSCRIPT_INDEX_MAX]]
        return {"transcripts": entries, "count": len(entries), "truncated": truncated}

    @staticmethod
    def _cached_transcript_text(cache: PersistentCache, video_id: str) -> str:
        """The full cached transcript text, or ValueError. Never fetches."""
        if not is_valid_video_id(video_id):
            raise ValueError(f"Invalid video id {video_id!r}")
        row = cache.get_transcript(video_id)
        if row is None or row["fetch_status"] != "SUCCESS":
            raise ValueError(f"No cached transcript for {video_id}; the get_transcript tool "
                             "fetches it (no YouTube API quota)")
        return TranscriptFetcher.decompress_transcript(row["transcript_text"])

    @staticmethod
    def _scan_patterns() -> list[str]:
        """Fabric patterns usable as prompts: a well-named dir whose system.md _load_pattern_prompt
        would accept (readable UTF-8, not blank). An unreadable entry is skipped, not fatal."""
        root = _fabric_patterns_dir()
        if not root.is_dir():
            return []
        names = []
        for d in sorted(root.iterdir()):
            if not re.fullmatch(r"[A-Za-z0-9_-]+", d.name):
                continue
            try:
                text = (d / "system.md").read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):  # missing, a directory, unreadable, not UTF-8
                continue
            if text.strip():
                names.append(d.name)
        return names

    async def _list_prompts(self) -> list[Prompt]:
        names = await asyncio.to_thread(self._scan_patterns)
        return [Prompt(
            name=name,
            description=f"Fabric pattern '{name}' (from ~/.config/fabric/patterns)",
            arguments=[PromptArgument(
                name="video_id", required=False,
                description="Optional: append this video's CACHED transcript as the input")],
        ) for name in names]

    async def _get_prompt(self, name: str,
                          arguments: Optional[dict[str, str]]) -> GetPromptResult:
        prompt, err = await self._load_pattern_prompt(name)
        if err:
            raise ValueError(err["message"])
        # Optional argument: clients often send an unfilled one as "".
        video_id = ((arguments or {}).get("video_id") or "").strip()
        text = prompt
        if video_id:
            cache = await asyncio.to_thread(self._resource_cache)
            # The whole transcript, as fabric_analyze sends it (max_chars 0).
            transcript = await asyncio.to_thread(self._cached_transcript_text, cache, video_id)
            text = prompt + "\n\n" + transcript
        return GetPromptResult(
            description=f"Fabric pattern '{name}'" + (f" on {video_id}" if video_id else ""),
            messages=[PromptMessage(role="user", content=TextContent(type="text", text=text))])

    # ----- Confirmation via MCP elicitation ----------------------------------------------

    @staticmethod
    def _supports_form_elicitation(caps: Any) -> bool:
        """True only when the client's declared capabilities say it can render a form.

        Fails closed on anything unrecognised: an SDK without the elicitation type, capabilities
        without the field (pre-elicitation SDKs), or a raw dict (an old SDK's ``extra=allow``).
        A bare ``{}`` means form mode (spec 2025-06-18 clients); a url-only client cannot do forms.
        """
        if ElicitationCapability is None or caps is None:
            return False
        # Absent is a legitimate answer here (older SDKs/clients), and means "cannot elicit".
        el = getattr(caps, "elicitation", None)
        if not isinstance(el, ElicitationCapability):
            return False
        return el.form is not None or el.url is None

    def _elicitation_context(self):
        """The current MCP request context if its client can be asked to confirm, else None.

        None also covers callers outside an MCP request (in-process/direct calls) and server
        sessions without ``elicit_form`` (older SDKs) — all of which keep pre-elicitation behaviour.
        """
        try:
            ctx = self.server.request_context
        except LookupError:
            return None
        if not callable(getattr(ctx.session, "elicit_form", None)):
            return None
        params = ctx.session.client_params
        if params is None or not self._supports_form_elicitation(params.capabilities):
            return None
        return ctx

    async def _ask_to_confirm(self, ctx, message: str) -> Optional[dict[str, Any]]:
        """Ask the client's user to approve. Returns None ONLY on an explicit accept with the
        ``confirm`` box ticked; otherwise a structured refusal. "Could not ask" is never consent."""
        try:
            r = await asyncio.wait_for(
                ctx.session.elicit_form(message, CONFIRM_SCHEMA, related_request_id=ctx.request_id),
                timeout=CONFIRM_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:  # distinct from builtin TimeoutError on Python 3.10
            logger.warning("Elicitation timed out; refusing the gated action")
            return {"error": "confirmation_failed", "message": (
                f"No answer from the user within {CONFIRM_TIMEOUT_SECONDS}s; "
                "the action was NOT performed."
            )}
        except Exception as e:  # any failure to get an answer refuses (fail closed)
            logger.warning("Elicitation failed; refusing the gated action", exc_info=True)
            return {"error": "confirmation_failed", "message": (
                f"Could not get the user's confirmation ({type(e).__name__}: {e}); "
                "the action was NOT performed."
            )}
        # Content comes from the client: a missing key must mean "not confirmed".
        if r.action == "accept" and (r.content or {}).get("confirm") is True:
            return None
        return {"error": "not_confirmed", "action": r.action, "message": (
            "The user did not approve this action; it was NOT performed."
        )}

    # ----- LLM backend: local Fabric or the client's model via MCP sampling ---------------

    @staticmethod
    def _supports_sampling(caps: Any) -> bool:
        """True only when the client declared sampling. Fails closed like
        ``_supports_form_elicitation``: no SDK type, no field, or a raw dict all mean "cannot"."""
        if SamplingCapability is None or caps is None:
            return False
        return isinstance(getattr(caps, "sampling", None), SamplingCapability)

    def _sampling_context(self):
        """The current MCP request context if its client can run a sampling request, else None
        (also None outside an MCP request and for server sessions without ``create_message``)."""
        try:
            ctx = self.server.request_context
        except LookupError:
            return None
        if not callable(getattr(ctx.session, "create_message", None)):
            return None
        # mcp 2.x: capabilities can be declared without client_params; older SDKs lack the field.
        caps = getattr(ctx.session, "client_capabilities", None)
        if caps is None:
            params = ctx.session.client_params
            caps = None if params is None else params.capabilities
        if not self._supports_sampling(caps):
            return None
        return ctx

    def _resolve_llm_backend(self, requested: str):
        """Pick the backend once, up front: ``(backend, ctx, error)``, exactly one of
        backend/error set. ``auto`` = client sampling, else local Fabric, else an error naming
        both remedies. A backend that is chosen and then fails is never swapped for the other."""
        if requested not in ("auto", "fabric", "sampling"):
            return None, None, {"error": "invalid_arguments", "message": (
                f"Unknown backend {requested!r}; use 'auto', 'fabric' or 'sampling'")}
        ctx = None if requested == "fabric" else self._sampling_context()
        if ctx is not None:
            return "sampling", ctx, None
        if requested == "sampling":
            return None, None, {"error": "sampling_unsupported", "message": (
                "This MCP client did not declare sampling support, so its model cannot run the "
                "pattern; use backend='fabric' or a client that supports MCP sampling")}
        if shutil.which("fabric"):
            return "fabric", None, None
        error = dict(FABRIC_NOT_INSTALLED)
        if requested == "auto":
            error["message"] += (
                ". This MCP client also does not support sampling, so its own model cannot run "
                "the pattern either: install Fabric, or use a client that supports MCP sampling")
        return None, None, error

    async def _load_pattern_prompt(self, pattern: str):
        """``(system_prompt, error)`` for a Fabric pattern, read from its system.md."""
        if not re.fullmatch(r"[A-Za-z0-9_-]+", pattern):
            return None, {"error": "invalid_arguments", "message": (
                f"Invalid pattern name {pattern!r}: use letters, digits, '_' or '-'")}
        path = _fabric_patterns_dir() / pattern / "system.md"
        reason = ""
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except FileNotFoundError:
            text = ""
        except (OSError, UnicodeDecodeError) as e:  # a directory, unreadable, or not UTF-8
            text, reason = "", f" (unreadable: {type(e).__name__})"
        if not text.strip():
            hint = (" Or pass backend='fabric' to let local Fabric resolve it."
                    if shutil.which("fabric") else "")
            return None, {"error": "pattern_not_found", "pattern": pattern, "message": (
                f"No usable pattern prompt at {path}{reason}. Patterns come from Fabric's repo "
                f"(https://github.com/danielmiessler/fabric) or `fabric --setup`.{hint}")}
        return text, None

    async def _sample_pattern(self, ctx, prompt: str, transcript_text: str,
                              model: Optional[str]) -> dict[str, Any]:
        """Run one pattern on the client's model: the result fields, or a structured error."""
        try:
            r = await asyncio.wait_for(
                ctx.session.create_message(
                    [SamplingMessage(role="user",
                                     content=TextContent(type="text", text=transcript_text))],
                    max_tokens=SAMPLING_MAX_TOKENS,
                    system_prompt=prompt,
                    model_preferences=(ModelPreferences(hints=[ModelHint(name=model)])
                                       if model else None),
                    related_request_id=ctx.request_id,  # route on the tool call's stream
                ),
                timeout=SAMPLING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:  # distinct from builtin TimeoutError on Python 3.10
            return {"error": "timeout", "message": (
                f"The client did not answer the sampling request within "
                f"{SAMPLING_TIMEOUT_SECONDS}s")}
        except Exception as e:  # McpError when the client declines or fails
            return {"error": "sampling_error", "message": f"{type(e).__name__}: {e}"}
        if not isinstance(r.content, TextContent):
            return {"error": "sampling_error", "message": (
                f"The client's model returned {type(r.content).__name__}, not text")}
        return {"result": r.content.text, "sampled_model": r.model, "stop_reason": r.stopReason}

    # ----- Custom commands (opt-in; MCPSettings.allow_custom_commands) -------------------

    def _custom_command_tools(self) -> list[Tool]:
        """The gated ``run_custom_command`` tool, or ``[]`` when the opt-in is off / no eligible
        commands. ``confirm: true`` commands ("always ask me") are advertised only when the
        current client supports form elicitation, so the user can be asked; otherwise they are
        left out (and refused in the handler too, as defense-in-depth). This runs inside the
        list_tools request, so the session's capabilities are available here."""
        settings = self._settings or load_settings()
        self._settings = settings
        if not getattr(getattr(settings, "mcp", None), "allow_custom_commands", False):
            return []
        registry = load_command_registry(settings)
        can_elicit = self._elicitation_context() is not None
        names = sorted(name for name, spec in registry.items() if not spec.confirm or can_elicit)
        if not names:
            return []
        return [
            Tool(
                name="run_custom_command",
                description=(
                    "Run one of the user's PRE-CONFIGURED custom shell commands (their `commands:` "
                    "registry) against one or more videos and return its captured output. You may "
                    "only select a command by name from the enum — you cannot supply an arbitrary "
                    "command. Opt-in feature (LLM-driven shell execution)."
                    + (" Commands the user marked confirm will prompt them to approve first."
                       if can_elicit else "")
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "enum": names,
                            "description": "Name of a registered custom command to run.",
                        },
                        "video_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "maxItems": CUSTOM_COMMAND_MAX_VIDEOS,
                            "description": (
                                f"1..{CUSTOM_COMMAND_MAX_VIDEOS} YouTube video IDs. A per-video "
                                "command runs once per id; a batch command runs once for all."
                            ),
                        },
                        "max_output_chars": {
                            "type": "integer",
                            "description": "Truncate each captured stdout/stderr to this many chars.",
                            "default": CUSTOM_COMMAND_MAX_OUTPUT_CHARS,
                        },
                    },
                    "required": ["command", "video_ids"],
                },
            )
        ]

    async def _run_custom_command(self, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a registered custom command against video ids; capture and return output.

        Security: opt-in gate re-checked here (defense-in-depth); the LLM only selects a
        registered command by name; every id must be a well-formed YouTube id (11-char charset,
        no shell metacharacters) BEFORE any command is built — ``shlex.quote`` alone is bypassable
        when a user's template embeds the placeholder inside quotes (``echo "got {id}"`` +
        ``$(...)``); ``confirm: true`` commands run only after the client's user explicitly approves
        via elicitation, and are refused when the client cannot be asked.

        Expected refusals return a structured ``{"error": <code>, ...}`` (like the fabric tools),
        so the LLM gets an actionable code rather than a generic exception.
        """
        settings = self._settings or load_settings()
        self._settings = settings
        if not getattr(getattr(settings, "mcp", None), "allow_custom_commands", False):
            return {"error": "disabled", "message": (
                "run_custom_command is disabled. Set `mcp.allow_custom_commands: true` in "
                "config.yaml (or YANGER_MCP_ALLOW_CUSTOM_COMMANDS=1) to enable it."
            )}

        name = str(args.get("command", "")).strip().lower()
        registry = load_command_registry(settings)
        spec = registry.get(name)
        if spec is None:
            return {"error": "unknown_command", "message": f"No custom command named '{name}'.",
                    "available": sorted(registry)}

        video_ids = args.get("video_ids") or []
        if not isinstance(video_ids, list) or not video_ids:
            return {"error": "invalid_video_ids", "message": "video_ids must be a non-empty list."}
        if len(video_ids) > CUSTOM_COMMAND_MAX_VIDEOS:
            return {"error": "too_many_videos", "max": CUSTOM_COMMAND_MAX_VIDEOS, "message": (
                f"Too many videos ({len(video_ids)} > {CUSTOM_COMMAND_MAX_VIDEOS}); MCP has no "
                "confirmation for a large batch. Call with fewer ids."
            )}
        # SECURITY (critical): ids flow into a shell=True command; shlex.quote is bypassable when a
        # template embeds the placeholder inside quotes, so a hostile id ($(...), ;, |, backticks,
        # spaces) could execute. Reject anything that isn't a well-formed YouTube id (also rejects
        # non-strings) BEFORE building any command.
        if not all(isinstance(v, str) and is_valid_video_id(v) for v in video_ids):
            return {"error": "invalid_video_ids", "message": (
                "Each video_id must be a well-formed YouTube ID (11 chars, letters/digits/_/-)."
            )}

        max_chars = args.get("max_output_chars", CUSTOM_COMMAND_MAX_OUTPUT_CHARS)
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
            # Ignore junk / 0 / negative (0 would mean "unlimited" in _truncate_transcript).
            max_chars = CUSTOM_COMMAND_MAX_OUTPUT_CHARS

        # Minimal stand-in for a Video: resolve_commands/build_* read only `.id`. NOTE: when the
        # planned {title} placeholder lands (ROADMAP), build_command will read `.title` and this
        # shim must be replaced with hydrated Video objects (a cache/metadata fetch) for MCP.
        targets = [SimpleNamespace(id=v) for v in video_ids]

        # resolve_commands is the single owner of the batch/per-video dispatch (shared with the TUI).
        runs = resolve_commands(spec, targets)

        # confirm:true ("always ask me") is checked only after every validation above, so the user
        # is never asked to approve a request that would be rejected, and approves the EXACT
        # shell strings that will run.
        if spec.confirm:
            ctx = self._elicitation_context()
            if ctx is None:
                return {"error": "requires_confirmation", "message": (
                    f"Custom command '{name}' is marked confirm:true; it needs an MCP client that "
                    "supports elicitation, or run it from the TUI (:run)."
                )}
            lines = "\n".join(f"  {cmd}" for _, cmd in runs)
            refusal = await self._ask_to_confirm(ctx, (
                f"Run custom command '{name}' on {len(video_ids)} video(s): "
                f"{', '.join(video_ids)}?\nIt will execute:\n{lines}"
            ))
            if refusal is not None:
                return refusal

        results: list[dict[str, Any]] = []
        for ids_for_run, cmd in runs:
            cr = await asyncio.to_thread(run_command_capture, cmd)
            results.append(self._custom_command_result(ids_for_run, cr, max_chars))

        return {
            "command": name,
            "mode": spec.mode,
            "results": results,
            "truncated": any(r["truncated"] for r in results),
        }

    def _custom_command_result(self, video_ids, cr, max_chars) -> dict[str, Any]:
        """Shape one CommandResult into the tool payload, truncating each captured stream."""
        stdout, t1 = self._truncate_transcript(cr.stdout, max_chars)
        stderr, t2 = self._truncate_transcript(cr.stderr, max_chars)
        return {
            "video_ids": list(video_ids),
            "returncode": cr.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": cr.timed_out,
            "truncated": t1 or t2,
        }

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

    # --- Undo / redo (ROADMAP 2.2) ---
    #
    # Each mutating handler makes its own API call (so its errors reach call_tool's error JSON and
    # quota_exceeded path unchanged), then records the matching operation_history Operation with
    # the state its execute() would have left. Undo data comes from the cache only; when it is
    # missing, the mutation is reported `undoable: false` and NOT recorded (never a wrong entry).

    def _record(self, op: Operation) -> None:
        """Record `op`; the caller holds _history_lock since before its API call."""
        assert self._history_lock.locked()
        self._operation_stack.record(op)

    def _remember_items(self, playlist_id: str, videos: Optional[List[Video]]) -> None:
        for v in videos or []:
            if v.playlist_item_id:
                self._seen_items[v.playlist_item_id] = (playlist_id, v)

    async def _seen_item(self, playlist_id: str, playlist_item_id: str) -> Optional[Video]:
        """The video yanger has seen at `playlist_item_id` in `playlist_id` (cache, no quota, or
        this session's earlier reads), else None. Undo data is best effort: a cache error is
        logged and never blocks the mutation itself."""
        try:
            self._remember_items(
                playlist_id, await asyncio.to_thread(self.cache.get_videos, playlist_id))
        except Exception:
            logger.warning("Cache read for undo data failed", exc_info=True)
        seen = self._seen_items.get(playlist_item_id)
        return seen[1] if seen is not None and seen[0] == playlist_id else None

    @staticmethod
    def _not_undoable(result: dict[str, Any], note: str) -> dict[str, Any]:
        result["undoable"] = False
        result["undo_note"] = note
        return result

    @staticmethod
    def _history_prompt(op: Operation, redo: bool) -> str:
        """The confirm text for undoing/redoing `op`: what will happen, to which playlist and video
        ids, and the estimated quota. Every id/title is flattened (_one_line): titles are
        uploader-controlled and ids LLM-supplied."""
        def videos(vs: List[Video]) -> str:
            n = len(vs)
            shown = ", ".join(_one_line(v.id, 40) for v in vs[:5])
            more = f" and {n - 5} more" if n > 5 else ""
            return f"{n} video{'' if n == 1 else 's'} ({shown}{more})"

        verb = "Redo" if redo else "Undo"
        if isinstance(op, CreatePlaylistOperation):
            label = f"'{_one_line(op.title)}'"
            if redo:
                return (f"Redo: create playlist {label} again (a new, empty playlist with a new "
                        f"id)? Estimated quota: 50 units.")
            pid = _one_line(op.created_playlist_id)
            return (f"Undo: create playlist {label} ({pid})? This DELETES playlist {label} ({pid}) "
                    f"and every video in it now. This cannot be undone. Estimated quota: 50 units.")
        if isinstance(op, RenameOperation) and op.item_type == "playlist":
            frm, to = (op.old_title, op.new_title) if redo else (op.new_title, op.old_title)
            return (f"{verb}: rename playlist {_one_line(op.item_id)} from '{_one_line(frm)}' to "
                    f"'{_one_line(to)}'? Estimated quota: 51 units.")
        if isinstance(op, PasteOperation):
            n = len(op.videos)
            tgt = _one_line(op.target_playlist_id)
            if op.is_cut:
                src = _one_line(op.source_playlist_id)
                action = (f"move {videos(op.videos)} from playlist {src} to playlist {tgt} again"
                          if redo else
                          f"move {videos(op.videos)} from playlist {tgt} back to playlist {src}")
                quota = 100 * n
            else:
                action = (f"add {videos(op.videos)} to playlist {tgt} again" if redo else
                          f"remove {videos(op.videos)} from playlist {tgt} (added earlier)")
                quota = 50 * n
            return f"{verb}: {action}? Estimated quota: {quota} units."
        if isinstance(op, DeleteVideosOperation):
            pid = _one_line(op.playlist_id)
            action = (f"remove {videos(op.videos)} from playlist {pid} again" if redo else
                      f"re-add {videos(op.videos)} to playlist {pid} (removed earlier)")
            return f"{verb}: {action}? Estimated quota: {50 * len(op.videos)} units."
        return f"{verb}: {_one_line(str(op))}?"  # not recorded by MCP today; kept generic

    def _invalidate_after_history(self, op: Operation) -> None:
        if isinstance(op, PasteOperation):
            self._invalidate_cache(op.target_playlist_id, op.source_playlist_id)
        elif isinstance(op, DeleteVideosOperation):
            self._invalidate_cache(op.playlist_id)
        else:  # create/rename playlist, or anything else: the collection may have changed
            self._invalidate_cache(playlists_list=True)

    async def _undo(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._step_history(redo=False)

    async def _redo(self, args: dict[str, Any]) -> dict[str, Any]:
        return await self._step_history(redo=True)

    async def _step_history(self, redo: bool) -> dict[str, Any]:
        """Undo or redo the top of this session's history, only after the user confirms."""
        verb = "redo" if redo else "undo"
        stack = self._operation_stack
        pending = stack.redo_stack if redo else stack.undo_stack
        if not pending:
            return {"error": f"nothing_to_{verb}",
                    "message": f"Nothing to {verb} in this MCP session's history."}
        op = pending[-1]

        # A mutation reachable by an LLM: confirmation-gated, and "could not ask" is a refusal.
        ctx = self._elicitation_context()
        if ctx is None:
            return {"error": "confirmation_unavailable", "message": (
                f"{verb} needs the user's confirmation via MCP elicitation, which this client "
                "does not support; nothing was changed.")}
        refusal = await self._ask_to_confirm(ctx, self._history_prompt(op, redo))
        if refusal is not None:
            return refusal

        detail = ""
        async with self._history_lock:
            # Identity, not ==: the Operation dataclasses compare field-wise.
            if not pending or pending[-1] is not op:
                return {"error": "history_changed", "message": (
                    f"The history changed while waiting for confirmation; the {verb} was NOT "
                    "performed. Call it again to confirm the current top entry.")}
            try:
                done = await asyncio.to_thread(stack.redo if redo else stack.undo)
            except Exception as e:  # OperationStack has put the op back; report, don't crash
                logger.warning(f"MCP {verb} failed", exc_info=True)
                done, detail = None, f" ({type(e).__name__}: {e})"
        if done is None:
            return {"error": f"{verb}_failed", "message": (
                f"The {verb} of '{_one_line(str(op))}' did not complete{detail}. It stays in the "
                f"history; YouTube may have changed since (a partial {verb} is possible).")}

        await asyncio.to_thread(self._invalidate_after_history, op)
        return {
            "success": True,
            ("redone" if redo else "undone"): _one_line(str(op)),
            "can_undo": stack.can_undo(),
            "can_redo": stack.can_redo(),
        }

    # --- Playlist Management ---

    @staticmethod
    def _playlist_dict(p: Playlist) -> dict[str, Any]:
        """One playlist as list_playlists and the yanger://playlists resource report it."""
        return {
            "id": p.id,
            "title": p.title,
            "description": p.description,
            "video_count": p.item_count,
            "privacy_status": p.privacy_status.value,
            "is_virtual": p.is_virtual,
        }

    @staticmethod
    def _video_dict(v: Video) -> dict[str, Any]:
        """One video as list_videos and the yanger://playlists/{id} resource report it."""
        return {
            "video_id": v.id,
            "playlist_item_id": v.playlist_item_id,
            "title": v.title,
            "channel_title": v.channel_title,
            "position": v.position,
            "duration": v.duration,
            "added_at": str(v.added_at) if v.added_at else None,
        }

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

        result = [self._playlist_dict(p) for p in playlists]

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

        async with self._history_lock:
            playlist = await asyncio.to_thread(
                self.api_client.create_playlist,
                title=title,
                description=description,
                privacy_status=privacy_status,
            )

            # The playlist collection changed; force list_playlists to refetch.
            await asyncio.to_thread(self._invalidate_cache, playlists_list=True)

            # The API call already ran (so its exception reaches the MCP error contract); set the
            # state execute() would have left.
            op = CreatePlaylistOperation(self.api_client, title, description, privacy_status)
            op.created_playlist_id = playlist.id
            op.executed = True
            self._record(op)

        return {
            "success": True,
            "playlist": {
                "id": playlist.id,
                "title": playlist.title,
                "description": playlist.description,
                "privacy_status": playlist.privacy_status.value,
            },
            "message": f"Created playlist '{title}'",
            "undoable": True,
        }

    async def _delete_playlist(self, args: dict[str, Any]) -> dict[str, Any]:
        """Delete a playlist (asks the client's user first when it supports elicitation)."""
        playlist_id = args["playlist_id"]

        ctx = self._elicitation_context()
        if ctx is not None:
            # Title is best effort, from the cache only (no API quota); fall back to the id.
            playlists = await asyncio.to_thread(self.cache.get_playlists)
            cached = next((p for p in playlists or [] if p.id == playlist_id), None)
            label = _one_line(playlist_id)
            warning = "This cannot be undone."
            if cached:
                label = f"'{_one_line(cached.title)}' ({label})"
                # Same warning as the TUI's gd confirm; 0 can mean "unknown" (API limits), so omit.
                if cached.item_count > 0:
                    warning = f"It contains {cached.item_count} videos. {warning}"
            refusal = await self._ask_to_confirm(
                ctx, f"Delete playlist {label}? {warning}"
            )
            if refusal is not None:
                return refusal

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

        async with self._history_lock:
            # The old title an undo restores comes from the cache only (no quota), read before
            # the rename invalidates it. Unknown (or unreadable) -> the rename runs, unrecorded.
            try:
                playlists = await asyncio.to_thread(self.cache.get_playlists)
            except Exception:
                logger.warning("Cache read for undo data failed", exc_info=True)
                playlists = None
            old_title = next((p.title for p in playlists or [] if p.id == playlist_id), None)

            await asyncio.to_thread(self.api_client.rename_playlist, playlist_id, new_title)

            # The cached title is stale; refetch the playlist collection.
            await asyncio.to_thread(self._invalidate_cache, playlist_id, playlists_list=True)

            result = {
                "success": True,
                "message": f"Renamed playlist to '{new_title}'",
            }
            if not old_title:
                return self._not_undoable(result, (
                    "The previous title is not in yanger's cache (any playlist create, rename or "
                    "delete clears it), so this rename cannot be undone. Call list_playlists "
                    "right before renaming to make it undoable."))
            # The API call already ran; set the state execute() would have left.
            op = RenameOperation(self.api_client, "playlist", playlist_id, old_title, new_title)
            op.executed = True
            self._record(op)
        result["undoable"] = True
        return result

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
        self._remember_items(playlist_id, videos)  # undo data for later removes/moves (2.2)

        result = [self._video_dict(v) for v in videos[:limit]]
        return {"videos": result, "count": len(result), "playlist_id": playlist_id}

    async def _add_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Add a video to a playlist."""
        video_id = args["video_id"]
        playlist_id = args["playlist_id"]
        position = args.get("position")

        async with self._history_lock:
            playlist_item_id = await asyncio.to_thread(
                self.api_client.add_video_to_playlist,
                video_id=video_id,
                playlist_id=playlist_id,
                position=position,
            )

            # The new video must show up in list_videos for this playlist.
            await asyncio.to_thread(self._invalidate_cache, playlist_id)

            # The API call already ran; set the state execute() would have left.
            op = PasteOperation(
                self.api_client,
                [Video(id=video_id, playlist_item_id="", title="", channel_title="")],
                playlist_id)
            op.added_item_ids = [playlist_item_id]
            op.executed = True
            self._record(op)

        return {
            "success": True,
            "playlist_item_id": playlist_item_id,
            "message": f"Added video {video_id} to playlist {playlist_id}",
            "undoable": True,
        }

    async def _remove_video(self, args: dict[str, Any]) -> dict[str, Any]:
        """Remove a video from a playlist (asks the client's user first when it supports
        elicitation)."""
        playlist_item_id = args["playlist_item_id"]
        playlist_id = args.get("playlist_id")

        # The seen video (no quota) feeds both the confirm prompt and the undo record: the removal
        # acts on playlist_item_id alone, so only yanger's own reads can tie it to a playlist and
        # a video id. Not found -> the removal runs but is not recorded.
        video = await self._seen_item(playlist_id, playlist_item_id) if playlist_id else None

        ctx = self._elicitation_context()
        if ctx is not None:
            # Title is best effort; it is uploader-controlled, so it is flattened, quoted after
            # fixed server text, and the item id is always shown. The LLM-supplied playlist_id is
            # shown only when the cache ties this item to it; otherwise it never reaches the human.
            title = video.title if video is not None else None
            where = (f" from playlist {_one_line(playlist_id)}" if title is not None
                     else " (playlist not verified)")
            what = f"video titled \"{_one_line(title)}\"" if title else "video"
            refusal = await self._ask_to_confirm(ctx, (
                f"Remove {what} (playlist item {_one_line(playlist_item_id)}){where}?"
            ))
            if refusal is not None:
                return refusal

        async with self._history_lock:
            await asyncio.to_thread(self.api_client.remove_video_from_playlist, playlist_item_id)

            # Refresh so the removed video disappears from list_videos. Without a
            # known playlist we must clear the whole playlists cache to stay correct.
            if playlist_id:
                await asyncio.to_thread(self._invalidate_cache, playlist_id)
            else:
                await asyncio.to_thread(self._invalidate_cache, playlists_list=True)

            result = {
                "success": True,
                "message": f"Removed video from playlist",
            }
            if not playlist_id:
                return self._not_undoable(result, (
                    "No playlist_id was given, so yanger cannot tie this playlist item to a "
                    "playlist and the removal cannot be undone. Pass playlist_id to make a "
                    "removal undoable."))
            if video is None:
                return self._not_undoable(result, (
                    f"yanger has not seen playlist item {playlist_item_id} in playlist "
                    f"{playlist_id} (cache or list_videos), so the removal cannot be undone. "
                    "Call list_videos for that playlist before removing to make it undoable."))
            # The API call already ran; set the state execute() would have left.
            op = DeleteVideosOperation(self.api_client, playlist_id, [video])
            op.deleted_videos_data = [{
                'video_id': video.id,
                'playlist_item_id': video.playlist_item_id,
                'position': video.position,
                'title': video.title,
            }]
            op.executed = True
            self._record(op)
        result["undoable"] = True
        return result

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

        # The move acts on source_playlist_item_id alone; an undo re-adds video_id to
        # source_playlist_id. Record it only when yanger's own reads (no quota) tie that item to
        # both, or an undo could put the wrong video into the wrong playlist.
        seen = (await self._seen_item(source_playlist_id, source_playlist_item_id)
                if source_playlist_id else None)

        async with self._history_lock:
            new_item_id = await asyncio.to_thread(
                self.api_client.move_video, video, target_playlist_id)

            # Both ends changed; source_playlist_id is optional so may be skipped.
            await asyncio.to_thread(self._invalidate_cache, target_playlist_id, source_playlist_id)

            result = {
                "success": True,
                "new_playlist_item_id": new_item_id,
                "message": f"Moved video {video_id} to playlist {target_playlist_id}",
            }
            if not source_playlist_id:
                return self._not_undoable(result, (
                    "No source_playlist_id was given, so yanger cannot tie this playlist item to "
                    "a source playlist and the move cannot be undone. Pass source_playlist_id to "
                    "make a move undoable."))
            if seen is None:
                return self._not_undoable(result, (
                    f"yanger has not seen playlist item {source_playlist_item_id} in playlist "
                    f"{source_playlist_id} (cache or list_videos), so the move cannot be undone. "
                    "Call list_videos for the source playlist before moving to make it undoable."))
            if seen.id != video_id:
                return self._not_undoable(result, (
                    f"yanger saw playlist item {source_playlist_item_id} holding video {seen.id}, "
                    f"not {video_id}, so the move cannot be undone."))
            # The API call already ran; set the state execute() would have left.
            op = PasteOperation(self.api_client, [video], target_playlist_id, source_playlist_id,
                                is_cut=True)
            op.added_item_ids = [new_item_id]
            op.executed = True
            self._record(op)
        result["undoable"] = True
        return result

    async def _search_videos(self, args: dict[str, Any]) -> dict[str, Any]:
        """Search videos across playlists."""
        query = args["query"].lower()
        limit = args.get("limit", 20)

        # The whole scan is one SQLite read per playlist; run it off the event
        # loop so a large cache can't block stdio/cancellation.
        results, cached_scanned = await asyncio.to_thread(self._search_videos_blocking, query, limit)

        return {
            "results": results,
            "count": len(results),
            "query": query,
            # Be explicit that this only sees cached data — otherwise a caller (esp. an LLM)
            # reads an empty/short result as authoritative when the playlist just isn't cached.
            "coverage": {
                "cached_playlists_searched": cached_scanned,
                "note": "Only locally-cached playlists were searched; playlists not yet cached "
                        "are not included. Run list_playlists / get_playlist to cache more.",
            },
        }

    def _search_videos_blocking(self, query: str, limit: int) -> tuple[List[dict[str, Any]], int]:
        """Synchronous cache scan for _search_videos. Returns (results, cached_playlist_count)."""
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

        return results, len(playlists)

    # --- Transcripts ---

    @staticmethod
    def _truncate_transcript(text: str, max_chars: Optional[int],
                             more: str = "pass a higher max_chars to get more") -> tuple[str, bool]:
        """Cap transcript length so one tool call can't flood client context.

        Returns the (possibly truncated) text and whether truncation occurred.
        A falsy ``max_chars`` (None/0) means no limit. ``more`` tells the reader how to get the rest.
        """
        if max_chars and len(text) > max_chars:
            omitted = len(text) - max_chars
            note = f"\n\n[... truncated {omitted} chars; {more}]"
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

        # Check cache first (SQLite read offloaded off the event loop). Serve a cached row
        # only if it is FINAL (SUCCESS or terminal); should_refetch lets a legacy poisoned
        # transient row (pre-§0 IP_BLOCKED/ERROR) fall through to a fresh fetch instead of
        # being served as a permanent error — same read policy as the TUI.
        cached = await asyncio.to_thread(self.cache.get_transcript, video_id)
        if cached and not should_refetch(cached.get("fetch_status")):
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

        # Fetch fresh + apply the shared cache-write policy off the event loop. Unlike the
        # old inline code, this also caches a terminal NOT_AVAILABLE (no more refetch-forever)
        # while still leaving transient IP_BLOCKED/ERROR uncached for retry.
        transcript, status = await asyncio.to_thread(
            fetch_and_cache_transcript, self.transcript_fetcher, self.cache, video_id
        )

        if transcript is None:
            return {
                "video_id": video_id,
                "error": status,
                "message": "Transcript not available for this video",
            }

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
        results, scanned = await asyncio.to_thread(
            self._find_duplicates_blocking, playlist_id, include_fuzzy
        )

        resp = {
            "duplicates": results,
            "count": len(results),
            "scope": playlist_id or "all_playlists",
        }
        if not playlist_id:
            # Cross-playlist dedup only sees cached playlists — say so, or an empty result
            # reads as "no duplicates" when it's really "nothing cached to compare".
            resp["coverage"] = {
                "cached_playlists_scanned": scanned,
                "note": "Only locally-cached playlists were compared; uncached playlists are not "
                        "included. Run list_playlists / get_playlist to cache more.",
            }
        return resp

    def _find_duplicates_blocking(self, playlist_id: Optional[str],
                                  include_fuzzy: bool) -> tuple[List[dict[str, Any]], int]:
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
            scanned = 1
        else:
            # Find duplicates across all playlists
            playlists = self.cache.get_playlists() or []
            playlist_videos = []

            for playlist in playlists:
                videos = self.cache.get_videos(playlist.id)
                if videos:
                    playlist_videos.append((playlist, videos))

            duplicates = detector.find_duplicates_across(playlist_videos)
            scanned = len(playlists)

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

        return results, scanned

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
        async with self._history_lock:
            result, added = await asyncio.to_thread(
                self._copy_videos_blocking,
                source_playlist_id, target_playlist_id, video_ids, limit,
            )
            if not added:
                return self._not_undoable(
                    result, "Nothing was copied, so there is nothing to undo.")
            # The API calls already ran; record only the copies that were made (a partial or
            # quota failure is reported as before), with the state execute() would have left.
            op = PasteOperation(
                self.api_client,
                [Video(id=vid, playlist_item_id="", title="", channel_title="")
                 for vid, _ in added],
                target_playlist_id)
            op.added_item_ids = [item_id for _, item_id in added]
            op.executed = True
            self._record(op)
        result["undoable"] = True
        return result

    def _copy_videos_blocking(self, source_playlist_id: str, target_playlist_id: str,
                              video_ids: Optional[List[str]], limit: int
                              ) -> tuple[dict[str, Any], List[tuple[str, str]]]:
        """Synchronous copy loop for _copy_videos (runs in a worker thread). Returns the result
        and the (video_id, new playlist_item_id) of each copy made, for the undo record."""
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
        added = []  # (video_id, new playlist_item_id)
        failed = []
        quota_cost = 0

        for video in source_videos:
            try:
                item_id = self.api_client.add_video_to_playlist(
                    video_id=video["video_id"],
                    playlist_id=target_playlist_id,
                )
                copied.append(video)
                added.append((video["video_id"], item_id))
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
        }, added

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
            # Check cache if skip_cached is enabled. Skip only FINAL rows; a legacy
            # poisoned transient row is refetched (same read policy as should_refetch).
            if skip_cached:
                cached = self.cache.get_transcript(video_id)
                if cached and not should_refetch(cached.get("fetch_status")):
                    skipped.append(video_id)
                    continue

            # Fetch + apply the shared cache-write policy (SUCCESS cached; terminal
            # NOT_AVAILABLE cached; transient IP_BLOCKED/ERROR left uncached for retry).
            transcript, status = fetch_and_cache_transcript(
                self.transcript_fetcher, self.cache, video_id
            )

            if transcript:
                fetched.append({
                    "video_id": video_id,
                    "language": transcript.language,
                })
            else:
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
        """Apply a Fabric pattern to a video transcript, on local Fabric or the client's model."""
        video_id = args["video_id"]
        pattern = args["pattern"]
        model = args.get("model")

        backend, ctx, error = self._resolve_llm_backend(args.get("backend", "auto"))
        if error:
            return error
        if backend == "sampling":
            # Fail fast on a bad pattern, before fetching the transcript.
            prompt, error = await self._load_pattern_prompt(pattern)
            if error:
                return error

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

        if backend == "sampling":
            sampled = await self._sample_pattern(ctx, prompt, transcript_text, model)
            if "error" in sampled:
                return {**sampled, "pattern": pattern, "video_id": video_id}
            return {
                "video_id": video_id,
                "pattern": pattern,
                "model": model,
                **sampled,
                "transcript_language": transcript_result.get("language"),
                "backend": "sampling",
            }

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
                "backend": "fabric",
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
            # No binary, but the prompts on disk are what the sampling backend runs.
            patterns_dir = _fabric_patterns_dir()
            if patterns_dir.is_dir():
                patterns = sorted(p.name for p in patterns_dir.iterdir()
                                  if (p / "system.md").is_file())
                out = {"patterns": patterns, "count": len(patterns), "source": "filesystem"}
                if self._sampling_context() is None:
                    out["note"] = ("Fabric is not installed and this MCP client does not support "
                                   "sampling, so fabric_analyze cannot run these patterns here.")
                return out
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
                patterns_dir = _fabric_patterns_dir()
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
        """Apply a Fabric pattern to multiple video transcripts (local Fabric or client model)."""
        pattern = args["pattern"]
        playlist_id = args.get("playlist_id")
        video_ids = args.get("video_ids", [])
        model = args.get("model")
        limit = args.get("limit", 10)
        skip_errors = args.get("skip_errors", True)

        # Resolve once, before any auth/playlist (quota) work; each video then uses this backend.
        # request_ctx is a ContextVar, so the gather tasks below inherit the sampling context.
        backend, _ctx, error = self._resolve_llm_backend(args.get("backend", "auto"))
        if error:
            return error
        if backend == "sampling":
            _prompt, error = await self._load_pattern_prompt(pattern)
            if error:
                return error

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
            entry = {
                "video_id": video_id,
                "pattern": pattern,
                "result": result["result"],
                "transcript_language": result.get("transcript_language"),
            }
            # Sampling-only fields: keep maxTokens truncation visible per video.
            for key in ("sampled_model", "stop_reason"):
                if key in result:
                    entry[key] = result[key]
            results.append(entry)
            return False

        if skip_errors:
            # Fan out under a semaphore; gather preserves input ordering. Sampling runs one at a
            # time: the client may ask its user to approve each request.
            semaphore = asyncio.Semaphore(1 if backend == "sampling" else FABRIC_BATCH_CONCURRENCY)

            async def analyze_one(video_id: str) -> dict[str, Any]:
                async with semaphore:
                    return await self._fabric_analyze({
                        "video_id": video_id,
                        "pattern": pattern,
                        "model": model,
                        "backend": backend,
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
                        "backend": backend,
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
            "backend": backend,
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
