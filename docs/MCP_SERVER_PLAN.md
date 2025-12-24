# MCP Server Implementation Plan for Yanger

## Overview

This document outlines the plan to add a Model Context Protocol (MCP) server to yanger, enabling Claude and other MCP-compatible tools to programmatically manage YouTube playlists.

## Current State

- **No MCP implementation exists** in the codebase
- Core functionality is well-structured and ready for exposure:
  - `api_client.py` - YouTube API operations
  - `models.py` - Data structures (Playlist, Video, etc.)
  - `auth.py` - OAuth2 authentication
  - `cache.py` - SQLite caching layer
  - `core/transcript_fetcher.py` - Transcript fetching

## Implementation Tasks

### Phase 1: Foundation

- [ ] Add `mcp` package to dependencies in `pyproject.toml`
- [ ] Create `src/yanger/mcp_server.py` - main MCP server module
- [ ] Add `yanger mcp` CLI command in `cli.py`

### Phase 2: Core Tools

#### Playlist Management Tools
- [ ] `list_playlists` - List all user playlists
- [ ] `get_playlist` - Get playlist details by ID
- [ ] `create_playlist` - Create a new playlist
- [ ] `delete_playlist` - Delete a playlist
- [ ] `rename_playlist` - Rename a playlist

#### Video Management Tools
- [ ] `list_videos` - List videos in a playlist
- [ ] `add_video` - Add video to playlist
- [ ] `remove_video` - Remove video from playlist
- [ ] `move_video` - Move video between playlists
- [ ] `search_videos` - Search videos across playlists

### Phase 3: Advanced Tools

#### Transcript Tools
- [ ] `get_transcript` - Fetch video transcript
- [ ] `search_transcripts` - Search within transcripts

#### Utility Tools
- [ ] `check_quota` - Check remaining API quota
- [ ] `get_statistics` - Get playlist/video statistics
- [ ] `export_playlist` - Export playlist data (JSON/CSV)

### Phase 4: Resources (Optional)

- [ ] Expose playlists as MCP resources
- [ ] Expose cached transcripts as resources

## Technical Design

### File Structure

```
src/yanger/
├── mcp_server.py          # Main MCP server implementation
├── mcp/
│   ├── __init__.py
│   ├── tools.py           # Tool definitions and handlers
│   ├── resources.py       # Resource definitions (optional)
│   └── schemas.py         # Input/output schemas
```

### Tool Schema Example

```python
@server.tool()
async def list_playlists(
    include_virtual: bool = False,
    sort_by: str = "title"
) -> list[dict]:
    """List all YouTube playlists for the authenticated user.

    Args:
        include_virtual: Include virtual (imported) playlists
        sort_by: Sort order - "title", "date", or "count"

    Returns:
        List of playlist objects with id, title, video_count, etc.
    """
```

### Authentication Handling

The MCP server will:
1. Use existing `auth.py` credential management
2. Require OAuth setup before first use (`yanger auth`)
3. Store credentials in `~/.config/yanger/credentials.json`

### Caching Strategy

- Leverage existing `cache.py` SQLite cache
- Cached responses reduce API quota usage
- Transcripts cached with gzip compression

## CLI Integration

```bash
# Start MCP server (stdio transport)
yanger mcp

# Start with SSE transport
yanger mcp --transport sse --port 8080

# With verbose logging
yanger mcp --verbose
```

## Dependencies to Add

```toml
[project.optional-dependencies]
mcp = [
    "mcp>=1.0.0",
]
```

## Testing Plan

- [ ] Unit tests for each tool handler
- [ ] Integration tests with mock YouTube API
- [ ] Manual testing with Claude Code

## Documentation

- [ ] Update README.md with MCP server section
- [ ] Add MCP usage examples
- [ ] Document available tools and their parameters

## Success Criteria

1. `yanger mcp` starts a functioning MCP server
2. All core playlist/video operations work via MCP
3. Transcript fetching works without API quota cost
4. Proper error handling and informative messages
5. Works with Claude Code and other MCP clients

## Estimated Effort

| Component | Lines of Code | Complexity |
|-----------|---------------|------------|
| MCP server setup | ~50 | Low |
| Tool definitions | ~150 | Medium |
| Tool handlers | ~200 | Medium |
| CLI integration | ~30 | Low |
| Tests | ~150 | Medium |
| **Total** | **~580** | **Medium** |

## References

- [MCP Specification](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Yanger README](../README.md)
