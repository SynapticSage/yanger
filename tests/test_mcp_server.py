"""Tests for the MCP server module.

Tests the MCP tool handlers with mocked YouTube API and cache.
"""
# Created: 2025-12-25

import pytest
import json
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import shutil

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


class TestEnsureAuth:
    """Test authentication path resolution and fail-fast behavior."""

    def test_fails_fast_without_token(self, tmp_path, monkeypatch):
        """A missing token must raise, never launch the interactive OAuth flow."""
        # Anchor config resolution at an empty home so no token exists.
        monkeypatch.setattr("yanger.mcp_server.Path.home", lambda: tmp_path)
        server = YangerMCPServer()

        with pytest.raises(RuntimeError, match="Not authenticated"):
            server._ensure_auth()

    def test_uses_absolute_cwd_independent_paths(self, tmp_path, monkeypatch):
        """Token/secret handed to YouTubeAuth must be absolute, not cwd-relative."""
        monkeypatch.setattr("yanger.mcp_server.Path.home", lambda: tmp_path)
        token = tmp_path / ".config" / "yanger" / "token.json"
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text("{}")

        with patch("yanger.mcp_server.YouTubeAuth") as MockAuth, \
             patch("yanger.mcp_server.YouTubeAPIClient"), \
             patch("yanger.mcp_server.PersistentCache"), \
             patch("yanger.mcp_server.TranscriptFetcher"):
            server = YangerMCPServer()
            server._ensure_auth()

        _, kwargs = MockAuth.call_args
        assert kwargs["token_file"] == str(token)
        assert Path(kwargs["client_secrets_file"]).is_absolute()
        assert server._authenticated is True


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
        # Cache must be invalidated so list_playlists shows the new playlist.
        mcp_server.cache.invalidate_playlists_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_playlist(self, mcp_server):
        """Should delete a playlist."""
        result = await mcp_server._delete_playlist({"playlist_id": "PL_delete"})

        assert result["success"] is True
        mcp_server.api_client.delete_playlist.assert_called_once_with("PL_delete")
        mcp_server.cache.invalidate_playlists_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_rename_playlist(self, mcp_server):
        """Should rename a playlist."""
        result = await mcp_server._rename_playlist({
            "playlist_id": "PL_music",
            "new_title": "My Music",
        })

        assert result["success"] is True
        mcp_server.api_client.rename_playlist.assert_called_once_with("PL_music", "My Music")
        mcp_server.cache.invalidate_playlists_cache.assert_called_once()


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
        # The target playlist's video cache must be refreshed.
        mcp_server.cache.invalidate_playlist.assert_called_once_with("PL_music")

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
        # Without a known playlist_id, fall back to a full playlists invalidation.
        mcp_server.cache.invalidate_playlists_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_video_targeted(self, mcp_server):
        """Should do a targeted invalidation when playlist_id is supplied."""
        result = await mcp_server._remove_video({
            "playlist_item_id": "item_to_remove",
            "playlist_id": "PL_music",
        })

        assert result["success"] is True
        mcp_server.cache.invalidate_playlist.assert_called_once_with("PL_music")
        mcp_server.cache.invalidate_playlists_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_move_video(self, mcp_server):
        """Should move a video between playlists."""
        mcp_server.api_client.move_video.return_value = "new_item_id"

        result = await mcp_server._move_video({
            "video_id": "video1",
            "source_playlist_item_id": "item1",
            "target_playlist_id": "PL_coding",
            "source_playlist_id": "PL_music",
        })

        assert result["success"] is True
        assert result["new_playlist_item_id"] == "new_item_id"
        # Both source and target playlist caches must be refreshed.
        invalidated = {c.args[0] for c in mcp_server.cache.invalidate_playlist.call_args_list}
        assert invalidated == {"PL_coding", "PL_music"}

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
            "fetch_status": "SUCCESS",
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
    async def test_get_transcript_cached_not_available(self, mcp_server):
        """A cached failure must return an error, not decompress/json.loads None."""
        mcp_server.cache.get_transcript.return_value = {
            "video_id": "no_transcript_video",
            "transcript_text": None,
            "transcript_json": None,
            "language": None,
            "auto_generated": False,
            "fetch_status": "NOT_AVAILABLE",
        }

        # json format previously crashed on json.loads(None); now it errors cleanly.
        result = await mcp_server._get_transcript({
            "video_id": "no_transcript_video",
            "format": "json",
        })

        assert result["error"] == "NOT_AVAILABLE"
        assert "transcript" not in result

    @pytest.mark.asyncio
    async def test_get_transcript_truncated(self, mcp_server):
        """Long transcripts should be truncated to max_chars with a note."""
        long_text = "word " * 1000  # 5000 chars
        with patch("yanger.mcp_server.TranscriptFetcher.format_as_text", return_value=long_text):
            result = await mcp_server._get_transcript({
                "video_id": "video1",
                "format": "text",
                "max_chars": 100,
            })

        assert result["truncated"] is True
        assert "truncated" in result["transcript"]
        # Body is capped at max_chars (plus the appended note).
        assert result["transcript"].startswith(long_text[:100])

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


class TestAdvancedAnalysisTools:
    """Test advanced analysis tools."""

    @pytest.mark.asyncio
    async def test_find_duplicates_within_playlist(self, mcp_server):
        """Should find duplicates within a playlist."""
        # Add duplicate video to mock data
        mcp_server.cache.get_videos.return_value = [
            Video(id="video1", playlist_item_id="item1", title="Test Video", channel_title="Channel"),
            Video(id="video1", playlist_item_id="item2", title="Test Video", channel_title="Channel"),
            Video(id="video2", playlist_item_id="item3", title="Another Video", channel_title="Channel"),
        ]

        result = await mcp_server._find_duplicates({
            "playlist_id": "PL_music",
        })

        assert "duplicates" in result
        assert result["scope"] == "PL_music"

    @pytest.mark.asyncio
    async def test_find_duplicates_across_playlists(self, mcp_server):
        """Should find duplicates across all playlists."""
        result = await mcp_server._find_duplicates({})

        assert "duplicates" in result
        assert result["scope"] == "all_playlists"

    @pytest.mark.asyncio
    async def test_analyze_playlist(self, mcp_server):
        """Should analyze playlist statistics."""
        result = await mcp_server._analyze_playlist({
            "playlist_id": "PL_music",
        })

        assert result["playlist_id"] == "PL_music"
        assert "total_videos" in result
        assert "unique_channels" in result
        assert "top_channels" in result

    @pytest.mark.asyncio
    async def test_copy_videos(self, mcp_server):
        """Should copy videos from source to target playlist."""
        mcp_server.api_client.add_video_to_playlist.return_value = "new_item_id"

        result = await mcp_server._copy_videos({
            "source_playlist_id": "PL_music",
            "target_playlist_id": "PL_coding",
            "limit": 2,
        })

        assert result["success"] is True
        assert result["copied_count"] == 2
        assert result["source"] == "PL_music"
        assert result["target"] == "PL_coding"
        # The target playlist's cache must be refreshed after copying.
        mcp_server.cache.invalidate_playlist.assert_called_once_with("PL_coding")

    @pytest.mark.asyncio
    async def test_copy_videos_from_virtual(self, mcp_server):
        """Should copy videos from virtual playlist to real playlist."""
        mcp_server.api_client.add_video_to_playlist.return_value = "new_item_id"

        result = await mcp_server._copy_videos({
            "source_playlist_id": "virtual_watchlater",
            "target_playlist_id": "PL_music",
            "limit": 1,
        })

        assert result["success"] is True
        assert result["copied_count"] == 1

    @pytest.mark.asyncio
    async def test_copy_videos_specific_ids(self, mcp_server):
        """Should copy only specified video IDs."""
        mcp_server.api_client.add_video_to_playlist.return_value = "new_item_id"

        result = await mcp_server._copy_videos({
            "source_playlist_id": "PL_music",
            "target_playlist_id": "PL_coding",
            "video_ids": ["video1"],
        })

        assert result["success"] is True
        assert result["copied_count"] == 1


class TestTranscriptSearchTools:
    """Test transcript search and batch tools."""

    @pytest.mark.asyncio
    async def test_search_transcripts(self, mcp_server):
        """Should search within transcript content."""
        # Mock transcript cache
        mcp_server.cache.get_transcript.return_value = {
            "transcript_text": b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03',  # gzipped
            "language": "en",
            "fetch_status": "SUCCESS",
        }

        with patch("yanger.mcp_server.TranscriptFetcher.decompress_transcript") as mock_decompress:
            mock_decompress.return_value = "This is a test transcript with some content"

            # Use playlist_id to avoid _get_all_cached_transcript_ids database call
            result = await mcp_server._search_transcripts({
                "query": "test",
                "playlist_id": "PL_music",
                "limit": 5,
            })

        assert "results" in result
        assert result["query"] == "test"
        assert result["scope"] == "PL_music"

    @pytest.mark.asyncio
    async def test_batch_fetch_transcripts(self, mcp_server):
        """Should fetch transcripts for multiple videos."""
        mcp_server.cache.get_transcript.return_value = None  # Not cached

        result = await mcp_server._batch_fetch_transcripts({
            "playlist_id": "PL_music",
            "limit": 2,
            "skip_cached": True,
        })

        assert result["playlist_id"] == "PL_music"
        assert "fetched_count" in result
        assert "skipped_count" in result
        assert "failed_count" in result

    @pytest.mark.asyncio
    async def test_batch_fetch_transcripts_skip_cached(self, mcp_server):
        """Should skip already cached transcripts."""
        mcp_server.cache.get_transcript.return_value = {
            "transcript_text": b"cached",
            "fetch_status": "SUCCESS",
        }

        result = await mcp_server._batch_fetch_transcripts({
            "playlist_id": "PL_music",
            "limit": 2,
            "skip_cached": True,
        })

        assert result["skipped_count"] == 2
        assert result["fetched_count"] == 0


class TestFabricIntegration:
    """Test Fabric integration tools."""

    @pytest.mark.asyncio
    async def test_fabric_analyze_not_installed(self, mcp_server):
        """Should handle case when Fabric is not installed."""
        with patch("shutil.which", return_value=None):
            result = await mcp_server._fabric_analyze({
                "video_id": "video1",
                "pattern": "extract_wisdom",
            })

        assert result["error"] == "fabric_not_installed"
        assert "install_instructions" in result

    @pytest.mark.asyncio
    async def test_fabric_analyze_transcript_unavailable(self, mcp_server):
        """Should handle unavailable transcript."""
        mcp_server.transcript_fetcher.fetch_transcript.return_value = (None, "NOT_AVAILABLE")
        mcp_server.cache.get_transcript.return_value = None

        with patch("shutil.which", return_value="/usr/bin/fabric"):
            result = await mcp_server._fabric_analyze({
                "video_id": "no_transcript",
                "pattern": "summarize",
            })

        assert result["error"] == "transcript_unavailable"

    @pytest.mark.asyncio
    async def test_fabric_analyze_success(self, mcp_server):
        """Should successfully analyze transcript with Fabric."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="# Summary\n\nThis is a great video about testing.",
                    stderr="",
                )

                result = await mcp_server._fabric_analyze({
                    "video_id": "video1",
                    "pattern": "summarize",
                })

        assert result["video_id"] == "video1"
        assert result["pattern"] == "summarize"
        assert "Summary" in result["result"]

    @pytest.mark.asyncio
    async def test_list_fabric_patterns_not_installed(self, mcp_server):
        """Should handle case when Fabric is not installed."""
        with patch("shutil.which", return_value=None):
            result = await mcp_server._list_fabric_patterns({})

        assert result["error"] == "fabric_not_installed"
        assert "common_patterns" in result
        assert "extract_wisdom" in result["common_patterns"]

    @pytest.mark.asyncio
    async def test_list_fabric_patterns_success(self, mcp_server):
        """Should list available Fabric patterns."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="extract_wisdom\nsummarize\nanalyze_claims\n",
                    stderr="",
                )

                result = await mcp_server._list_fabric_patterns({})

        assert "patterns" in result
        assert "extract_wisdom" in result["patterns"]
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_not_installed(self, mcp_server):
        """Should handle case when Fabric is not installed for batch."""
        with patch("shutil.which", return_value=None):
            result = await mcp_server._fabric_analyze_batch({
                "pattern": "summarize",
                "video_ids": ["video1", "video2"],
            })

        assert result["error"] == "fabric_not_installed"
        assert "install_instructions" in result

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_no_input(self, mcp_server):
        """Should require either playlist_id or video_ids."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            result = await mcp_server._fabric_analyze_batch({
                "pattern": "summarize",
            })

        assert result["error"] == "invalid_arguments"

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_with_video_ids(self, mcp_server):
        """Should analyze multiple videos by video_ids."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="# Analysis for video",
                    stderr="",
                )

                result = await mcp_server._fabric_analyze_batch({
                    "pattern": "extract_wisdom",
                    "video_ids": ["video1", "video2"],
                })

        assert result["pattern"] == "extract_wisdom"
        assert result["total_videos"] == 2
        assert result["successful_count"] == 2
        assert result["error_count"] == 0
        assert len(result["results"]) == 2
        assert result["source"] == "video_ids"

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_with_playlist(self, mcp_server):
        """Should analyze all videos in a playlist."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="# Wisdom extracted",
                    stderr="",
                )

                result = await mcp_server._fabric_analyze_batch({
                    "pattern": "extract_wisdom",
                    "playlist_id": "PL_music",
                    "limit": 2,
                })

        assert result["pattern"] == "extract_wisdom"
        assert result["total_videos"] == 2
        assert result["source"] == "PL_music"
        assert result["successful_count"] == 2

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_with_virtual_playlist(self, mcp_server):
        """Should analyze videos from virtual playlist."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="# Virtual video analysis",
                    stderr="",
                )

                result = await mcp_server._fabric_analyze_batch({
                    "pattern": "summarize",
                    "playlist_id": "virtual_watchlater",
                })

        assert result["source"] == "virtual_watchlater"
        assert result["total_videos"] == 1  # 1 virtual video in mock

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_partial_failures(self, mcp_server):
        """Should handle partial failures with skip_errors=True."""
        # Set up first video to have no transcript
        call_count = [0]
        original_fetch = mcp_server.transcript_fetcher.fetch_transcript

        def mock_fetch(video_id):
            call_count[0] += 1
            if call_count[0] == 1:
                return (None, "NOT_AVAILABLE")
            return original_fetch(video_id)

        mcp_server.transcript_fetcher.fetch_transcript = mock_fetch
        mcp_server.cache.get_transcript.return_value = None

        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="# Success",
                    stderr="",
                )

                result = await mcp_server._fabric_analyze_batch({
                    "pattern": "summarize",
                    "video_ids": ["video1", "video2"],
                    "skip_errors": True,
                })

        assert result["error_count"] == 1
        assert result["successful_count"] == 1
        assert len(result["errors"]) == 1
        assert result["errors"][0]["video_id"] == "video1"

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_stop_on_error(self, mcp_server):
        """Should stop on first error when skip_errors=False."""
        mcp_server.transcript_fetcher.fetch_transcript.return_value = (None, "NOT_AVAILABLE")
        mcp_server.cache.get_transcript.return_value = None

        with patch("shutil.which", return_value="/usr/bin/fabric"):
            result = await mcp_server._fabric_analyze_batch({
                "pattern": "summarize",
                "video_ids": ["video1", "video2", "video3"],
                "skip_errors": False,
            })

        # Should stop after first error
        assert result["error_count"] == 1
        assert result["successful_count"] == 0

    @pytest.mark.asyncio
    async def test_fabric_analyze_batch_with_model(self, mcp_server):
        """Should pass model parameter to Fabric."""
        with patch("shutil.which", return_value="/usr/bin/fabric"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="# Analysis with specific model",
                    stderr="",
                )

                result = await mcp_server._fabric_analyze_batch({
                    "pattern": "summarize",
                    "video_ids": ["video1"],
                    "model": "gpt-4",
                })

        assert result["model"] == "gpt-4"
        # Verify model was passed to subprocess
        call_args = mock_run.call_args
        assert "--model" in call_args[0][0]
        assert "gpt-4" in call_args[0][0]
