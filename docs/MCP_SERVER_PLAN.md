# MCP Server Implementation Plan for Yanger

## Overview

This document outlines the plan to add a Model Context Protocol (MCP) server to yanger, enabling Claude and other MCP-compatible tools to programmatically manage YouTube playlists.

## Implementation Status: COMPLETE

The MCP server has been implemented and is ready for use.

### Files Created/Modified

- `src/yanger/mcp_server.py` - Main MCP server implementation (~700 lines)
- `src/yanger/cli.py` - Added `yanger mcp` command
- `pyproject.toml` - Added `mcp` optional dependency

## Implementation Tasks

### Phase 1: Foundation - COMPLETE

- [x] Add `mcp` package to dependencies in `pyproject.toml`
- [x] Create `src/yanger/mcp_server.py` - main MCP server module
- [x] Add `yanger mcp` CLI command in `cli.py`

### Phase 2: Core Tools - COMPLETE

#### Playlist Management Tools
- [x] `list_playlists` - List all user playlists (with virtual playlist support)
- [x] `get_playlist` - Get playlist details by ID
- [x] `create_playlist` - Create a new playlist
- [x] `delete_playlist` - Delete a playlist
- [x] `rename_playlist` - Rename a playlist

#### Video Management Tools
- [x] `list_videos` - List videos in a playlist
- [x] `add_video` - Add video to playlist
- [x] `remove_video` - Remove video from playlist
- [x] `move_video` - Move video between playlists
- [x] `search_videos` - Search videos across playlists

### Phase 3: Advanced Tools - COMPLETE

#### Transcript Tools
- [x] `get_transcript` - Fetch video transcript (text or JSON format)

#### Utility Tools
- [x] `check_quota` - Check remaining API quota
- [x] `get_statistics` - Get playlist/video statistics

### Phase 4: Resources (Future Enhancement)

- [ ] Expose playlists as MCP resources
- [ ] Expose cached transcripts as resources

## Architecture

The MCP server reuses existing yanger components:

```
┌─────────────────────────────────────────────────────────────┐
│                      MCP Server                              │
│                   (mcp_server.py)                            │
├─────────────────────────────────────────────────────────────┤
│  Tools:                                                      │
│  - list_playlists, get_playlist, create_playlist, ...       │
│  - list_videos, add_video, remove_video, move_video, ...    │
│  - get_transcript                                            │
│  - check_quota, get_statistics                               │
├─────────────────────────────────────────────────────────────┤
│                   Reused Components                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐       │
│  │ api_client.py│  │   cache.py   │  │   auth.py    │       │
│  │  (YouTube    │  │  (SQLite     │  │  (OAuth2     │       │
│  │   API)       │  │   caching)   │  │   flow)      │       │
│  └──────────────┘  └──────────────┘  └──────────────┘       │
│  ┌──────────────┐  ┌──────────────┐                          │
│  │ transcript   │  │  models.py   │                          │
│  │ _fetcher.py  │  │  (Playlist,  │                          │
│  │              │  │   Video)     │                          │
│  └──────────────┘  └──────────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

## Usage

### Installation

```bash
# Install yanger with MCP support
pip install 'yanger[mcp]'

# Or install from source
pip install -e '.[mcp]'
```

### Running the MCP Server

```bash
# Start MCP server (stdio transport)
yanger mcp

# With verbose logging
yanger mcp --verbose
```

### Claude Code Configuration

Add to your Claude Code MCP settings (`~/.claude/claude_desktop_config.json`):

```json
{
    "mcpServers": {
        "yanger": {
            "command": "yanger",
            "args": ["mcp"]
        }
    }
}
```

### Prerequisites

Before using the MCP server, complete YouTube API authentication:

```bash
yanger auth
```

This will open a browser for OAuth2 authentication.

## Available Tools

### Playlist Management

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_playlists` | List all playlists | `include_virtual` (bool) |
| `get_playlist` | Get playlist details | `playlist_id` (required) |
| `create_playlist` | Create new playlist | `title` (required), `description`, `privacy_status` |
| `delete_playlist` | Delete a playlist | `playlist_id` (required) |
| `rename_playlist` | Rename a playlist | `playlist_id`, `new_title` (required) |

### Video Management

| Tool | Description | Parameters |
|------|-------------|------------|
| `list_videos` | List videos in playlist | `playlist_id` (required), `limit` |
| `add_video` | Add video to playlist | `video_id`, `playlist_id` (required), `position` |
| `remove_video` | Remove from playlist | `playlist_item_id` (required) |
| `move_video` | Move between playlists | `video_id`, `source_playlist_item_id`, `target_playlist_id` (required) |
| `search_videos` | Search by title | `query` (required), `limit` |

### Transcripts

| Tool | Description | Parameters |
|------|-------------|------------|
| `get_transcript` | Get video transcript | `video_id` (required), `format` (text/json) |

### Utilities

| Tool | Description | Parameters |
|------|-------------|------------|
| `check_quota` | Check API quota | none |
| `get_statistics` | Get playlist stats | `playlist_id` (optional) |

## Example Usage with Claude

```
User: "List my YouTube playlists"
Claude: [Uses list_playlists tool]

User: "Add this video to my Music playlist: https://youtube.com/watch?v=dQw4w9WgXcQ"
Claude: [Uses add_video tool with video_id="dQw4w9WgXcQ"]

User: "Get the transcript of that video"
Claude: [Uses get_transcript tool - no API quota used!]

User: "How much quota do I have left?"
Claude: [Uses check_quota tool]
```

## Testing

```bash
# Install dev dependencies
pip install -e '.[dev,mcp]'

# Run tests
pytest tests/
```

## Future Enhancements

1. **MCP Resources**: Expose playlists/transcripts as browsable resources
2. **Batch Operations**: Add bulk add/remove tools
3. **Playlist Sync**: Tool to sync between playlists
4. **Smart Search**: Search within transcript content

## References

- [MCP Specification](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Yanger README](../README.md)
