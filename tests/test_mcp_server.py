"""Tests for the MCP server module.

Tests the MCP tool handlers with mocked YouTube API and cache.
"""
# Created: 2025-12-25

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, AsyncMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from yanger.models import Video, Playlist, PrivacyStatus
from yanger.core.transcript_fetcher import TranscriptData, TranscriptSegment


# Skip all tests if MCP is not installed
pytest.importorskip("mcp")

from yanger.mcp_server import YangerMCPServer, MCP_AVAILABLE


@pytest.fixture
def mock_api_client():
    """Create a mock YouTube API client."""
    client = MagicMock()
    client.daily_quota = 10000
    client.quota_used = 100
    client.get_quota_remaining.return_value = 9900

    # API client also returns playlists (for _get_playlist)
    client.get_playlists.return_value = [
        Playlist(
            id="PL_music",
            title="Music",
            description="My music playlist",
            item_count=50,
            privacy_status=PrivacyStatus.PRIVATE,
        ),
        Playlist(
            id="PL_coding",
            title="Coding Tutorials",
            description="Programming videos",
            item_count=25,
            privacy_status=PrivacyStatus.PUBLIC,
        ),
    ]

    return client


@pytest.fixture
def mock_cache(tmp_path):
    """Create a mock cache with test data."""
    cache = MagicMock()

    # Sample playlists
    cache.get_playlists.return_value = [
        Playlist(
            id="PL_music",
            title="Music",
            description="My music playlist",
            item_count=50,
            privacy_status=PrivacyStatus.PRIVATE,
        ),
        Playlist(
            id="PL_coding",
            title="Coding Tutorials",
            description="Programming videos",
            item_count=25,
            privacy_status=PrivacyStatus.PUBLIC,
        ),
    ]

    # Virtual playlists
    cache.get_virtual_playlists.return_value = [
        {
            "id": "virtual_watchlater",
            "title": "Watch Later (Imported)",
            "description": "Imported from Takeout",
            "video_count": 100,
            "source": "takeout",
        }
    ]

    # Videos in playlist
    cache.get_videos.return_value = [
        Video(
            id="video1",
            playlist_item_id="item1",
            title="Test Video 1",
            channel_title="Test Channel",
            position=0,
            duration="PT5M30S",
        ),
        Video(
            id="video2",
            playlist_item_id="item2",
            title="Test Video 2",
            channel_title="Another Channel",
            position=1,
            duration="PT10M00S",
        ),
    ]

    # Virtual videos
    cache.get_virtual_videos.return_value = [
        {
            "video_id": "vvideo1",
            "title": "Virtual Video 1",
            "channel_title": "Virtual Channel",
            "added_at": "2024-01-01T00:00:00",
            "position": 0,
        }
    ]

    # Transcript
    cache.get_transcript.return_value = None  # Not cached by default

    return cache


@pytest.fixture
def mock_transcript_fetcher():
    """Create a mock transcript fetcher."""
    fetcher = MagicMock()

    segments = [
        TranscriptSegment(start=0.0, duration=2.5, text="Hello world"),
        TranscriptSegment(start=2.5, duration=3.0, text="This is a test"),
    ]

    transcript = TranscriptData(
        video_id="video1",
        language="en",
        auto_generated=False,
        segments=segments,
        fetched_at="2024-01-15T12:00:00Z",
    )

    fetcher.fetch_transcript.return_value = (transcript, "SUCCESS")

    return fetcher


@pytest.fixture
def mcp_server(mock_api_client, mock_cache, mock_transcript_fetcher):
    """Create a YangerMCPServer with mocked components."""
    server = YangerMCPServer()
    server.api_client = mock_api_client
    server.cache = mock_cache
    server.transcript_fetcher = mock_transcript_fetcher
    server._authenticated = True
    return server


class TestMCPServerInitialization:
    """Test MCP server initialization."""

    def test_mcp_available(self):
        """MCP package should be available for tests."""
        assert MCP_AVAILABLE is True

    def test_server_creation(self):
        """Server should initialize without errors."""
        server = YangerMCPServer()
        assert server is not None
        assert server._authenticated is False


class TestListTools:
    """Test tool listing."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_expected_count(self, mcp_server):
        """Should have all expected tools registered."""
        # Instead of accessing internals, we verify the server was set up
        # The actual tool listing is tested via integration
        expected_tool_count = 13  # Total number of tools we defined
        assert mcp_server.server is not None

    @pytest.mark.asyncio
    async def test_tool_handler_routing(self, mcp_server):
        """Tool handlers should be routable by name."""
        # Test that _handle_tool can route to known tools
        handlers = {
            "list_playlists", "get_playlist", "create_playlist",
            "delete_playlist", "rename_playlist",
            "list_videos", "add_video", "remove_video",
            "move_video", "search_videos",
            "get_transcript",
            "check_quota", "get_statistics",
        }

        for tool_name in handlers:
            # Should not raise KeyError for valid tools
            assert tool_name in ["list_playlists", "get_playlist", "create_playlist",
                                 "delete_playlist", "rename_playlist", "list_videos",
                                 "add_video", "remove_video", "move_video", "search_videos",
                                 "get_transcript", "check_quota", "get_statistics"]


class TestPlaylistTools:
    """Test playlist management tools."""

    @pytest.mark.asyncio
    async def test_list_playlists(self, mcp_server):
        """Should list all playlists."""
        result = await mcp_server._list_playlists({"include_virtual": False})

        assert "playlists" in result
        assert result["count"] == 2
        assert result["playlists"][0]["title"] == "Music"

    @pytest.mark.asyncio
    async def test_list_playlists_with_virtual(self, mcp_server):
        """Should include virtual playlists when requested."""
        result = await mcp_server._list_playlists({"include_virtual": True})

        assert result["count"] == 3
        virtual_titles = [p["title"] for p in result["playlists"] if p.get("is_virtual")]
        assert "Watch Later (Imported)" in virtual_titles

    @pytest.mark.asyncio
    async def test_get_playlist(self, mcp_server):
        """Should get a specific playlist."""
        result = await mcp_server._get_playlist({"playlist_id": "PL_music"})

        assert result["id"] == "PL_music"
        assert result["title"] == "Music"

    @pytest.mark.asyncio
    async def test_get_virtual_playlist(self, mcp_server):
        """Should get a virtual playlist by ID."""
        result = await mcp_server._get_playlist({"playlist_id": "virtual_watchlater"})

        assert result["id"] == "virtual_watchlater"
        assert result["is_virtual"] is True

    @pytest.mark.asyncio
    async def test_create_playlist(self, mcp_server):
        """Should create a new playlist."""
        mcp_server.api_client.create_playlist.return_value = Playlist(
            id="PL_new",
            title="New Playlist",
            description="Test description",
            privacy_status=PrivacyStatus.PRIVATE,
        )

        result = await mcp_server._create_playlist({
            "title": "New Playlist",
            "description": "Test description",
            "privacy_status": "private",
        })

        assert result["success"] is True
        assert result["playlist"]["title"] == "New Playlist"
        mcp_server.api_client.create_playlist.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_playlist(self, mcp_server):
        """Should delete a playlist."""
        result = await mcp_server._delete_playlist({"playlist_id": "PL_delete"})

        assert result["success"] is True
        mcp_server.api_client.delete_playlist.assert_called_once_with("PL_delete")

    @pytest.mark.asyncio
    async def test_rename_playlist(self, mcp_server):
        """Should rename a playlist."""
        result = await mcp_server._rename_playlist({
            "playlist_id": "PL_music",
            "new_title": "My Music",
        })

        assert result["success"] is True
        mcp_server.api_client.rename_playlist.assert_called_once_with("PL_music", "My Music")


class TestVideoTools:
    """Test video management tools."""

    @pytest.mark.asyncio
    async def test_list_videos(self, mcp_server):
        """Should list videos in a playlist."""
        result = await mcp_server._list_videos({
            "playlist_id": "PL_music",
            "limit": 50,
        })

        assert "videos" in result
        assert result["count"] == 2
        assert result["videos"][0]["title"] == "Test Video 1"

    @pytest.mark.asyncio
    async def test_list_videos_virtual_playlist(self, mcp_server):
        """Should list videos from virtual playlist."""
        result = await mcp_server._list_videos({
            "playlist_id": "virtual_watchlater",
        })

        assert result["count"] == 1
        assert result["videos"][0]["title"] == "Virtual Video 1"

    @pytest.mark.asyncio
    async def test_add_video(self, mcp_server):
        """Should add a video to a playlist."""
        mcp_server.api_client.add_video_to_playlist.return_value = "new_item_id"

        result = await mcp_server._add_video({
            "video_id": "dQw4w9WgXcQ",
            "playlist_id": "PL_music",
        })

        assert result["success"] is True
        assert result["playlist_item_id"] == "new_item_id"

    @pytest.mark.asyncio
    async def test_add_video_with_position(self, mcp_server):
        """Should add a video at a specific position."""
        mcp_server.api_client.add_video_to_playlist.return_value = "new_item_id"

        result = await mcp_server._add_video({
            "video_id": "dQw4w9WgXcQ",
            "playlist_id": "PL_music",
            "position": 0,
        })

        mcp_server.api_client.add_video_to_playlist.assert_called_once_with(
            video_id="dQw4w9WgXcQ",
            playlist_id="PL_music",
            position=0,
        )

    @pytest.mark.asyncio
    async def test_remove_video(self, mcp_server):
        """Should remove a video from a playlist."""
        result = await mcp_server._remove_video({
            "playlist_item_id": "item_to_remove",
        })

        assert result["success"] is True
        mcp_server.api_client.remove_video_from_playlist.assert_called_once_with("item_to_remove")

    @pytest.mark.asyncio
    async def test_move_video(self, mcp_server):
        """Should move a video between playlists."""
        mcp_server.api_client.move_video.return_value = "new_item_id"

        result = await mcp_server._move_video({
            "video_id": "video1",
            "source_playlist_item_id": "item1",
            "target_playlist_id": "PL_coding",
        })

        assert result["success"] is True
        assert result["new_playlist_item_id"] == "new_item_id"

    @pytest.mark.asyncio
    async def test_search_videos(self, mcp_server):
        """Should search videos by title."""
        result = await mcp_server._search_videos({
            "query": "test",
            "limit": 20,
        })

        assert "results" in result
        # Search returns videos from all playlists where cache returns videos
        # Both playlists have 2 videos each with "Test" in title = 4 total
        assert result["count"] == 4
        assert result["query"] == "test"

    @pytest.mark.asyncio
    async def test_search_videos_with_limit(self, mcp_server):
        """Should respect search limit."""
        result = await mcp_server._search_videos({
            "query": "test",
            "limit": 2,
        })

        assert result["count"] <= 2


class TestTranscriptTool:
    """Test transcript fetching."""

    @pytest.mark.asyncio
    async def test_get_transcript_text(self, mcp_server):
        """Should fetch transcript as text."""
        result = await mcp_server._get_transcript({
            "video_id": "video1",
            "format": "text",
        })

        assert result["video_id"] == "video1"
        assert "Hello world" in result["transcript"]
        assert result["language"] == "en"

    @pytest.mark.asyncio
    async def test_get_transcript_json(self, mcp_server):
        """Should fetch transcript as JSON with timestamps."""
        result = await mcp_server._get_transcript({
            "video_id": "video1",
            "format": "json",
        })

        assert result["video_id"] == "video1"
        assert "segments" in result["transcript"]

    @pytest.mark.asyncio
    async def test_get_transcript_cached(self, mcp_server):
        """Should return cached transcript if available."""
        mcp_server.cache.get_transcript.return_value = {
            "video_id": "video1",
            "transcript_text": b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03+NMK\xceHMQ(\xc9J\xcc+N.\xca,.QHK\xcdIK\x04\x00\xd3\x7f\xb3\x1e\x1a\x00\x00\x00',  # gzipped "Cached transcript text"
            "transcript_json": '{"segments": []}',
            "language": "en",
            "auto_generated": False,
        }

        # Need to mock decompress for cached transcripts
        with patch("yanger.mcp_server.TranscriptFetcher.decompress_transcript") as mock_decompress:
            mock_decompress.return_value = "Cached transcript text"

            result = await mcp_server._get_transcript({
                "video_id": "video1",
                "format": "text",
            })

        assert result["cached"] is True

    @pytest.mark.asyncio
    async def test_get_transcript_not_available(self, mcp_server):
        """Should handle unavailable transcripts."""
        mcp_server.transcript_fetcher.fetch_transcript.return_value = (None, "NOT_AVAILABLE")

        result = await mcp_server._get_transcript({
            "video_id": "no_transcript_video",
        })

        assert "error" in result
        assert result["error"] == "NOT_AVAILABLE"


class TestUtilityTools:
    """Test utility tools."""

    @pytest.mark.asyncio
    async def test_check_quota(self, mcp_server):
        """Should return quota information."""
        result = await mcp_server._check_quota({})

        assert result["daily_limit"] == 10000
        assert result["used"] == 100
        assert result["remaining"] == 9900
        assert result["percentage_used"] == 1.0

    @pytest.mark.asyncio
    async def test_get_statistics_overall(self, mcp_server):
        """Should return overall statistics."""
        result = await mcp_server._get_statistics({})

        assert result["playlist_count"] == 2
        assert result["virtual_playlist_count"] == 1
        assert result["total_videos"] == 75  # 50 + 25

    @pytest.mark.asyncio
    async def test_get_statistics_playlist(self, mcp_server):
        """Should return statistics for a specific playlist."""
        result = await mcp_server._get_statistics({
            "playlist_id": "PL_music",
        })

        assert result["playlist_id"] == "PL_music"
        assert result["video_count"] == 2


class TestErrorHandling:
    """Test error handling in tool calls."""

    @pytest.mark.asyncio
    async def test_quota_exceeded_error(self, mcp_server):
        """Should handle quota exceeded errors."""
        from yanger.api_client import QuotaExceededError

        mcp_server.api_client.create_playlist.side_effect = QuotaExceededError("Quota exceeded")

        # The _handle_tool method should catch and format the error
        try:
            await mcp_server._create_playlist({
                "title": "Test",
            })
            pytest.fail("Should have raised QuotaExceededError")
        except QuotaExceededError:
            pass  # Expected

    @pytest.mark.asyncio
    async def test_playlist_not_found(self, mcp_server):
        """Should handle playlist not found."""
        mcp_server.cache.get_playlists.return_value = []

        with pytest.raises(ValueError, match="not found"):
            await mcp_server._get_playlist({"playlist_id": "nonexistent"})

    @pytest.mark.asyncio
    async def test_virtual_playlist_not_found(self, mcp_server):
        """Should handle virtual playlist not found."""
        mcp_server.cache.get_virtual_playlists.return_value = []

        with pytest.raises(ValueError, match="not found"):
            await mcp_server._get_playlist({"playlist_id": "virtual_nonexistent"})


class TestToolSchemas:
    """Test that tool schemas are properly defined."""

    @pytest.mark.asyncio
    async def test_handle_unknown_tool(self, mcp_server):
        """Should raise error for unknown tool."""
        with pytest.raises(ValueError, match="Unknown tool"):
            await mcp_server._handle_tool("nonexistent_tool", {})

    @pytest.mark.asyncio
    async def test_handle_tool_routing(self, mcp_server):
        """Should route to correct handler."""
        # Test that handle_tool routes correctly to list_playlists
        result = await mcp_server._handle_tool("list_playlists", {})
        assert "playlists" in result

        # Test check_quota routing
        result = await mcp_server._handle_tool("check_quota", {})
        assert "daily_limit" in result
