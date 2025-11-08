<div align="center">
  <img src="assets/logo.png" alt="YouTube Ranger Logo" width="200">

  # YouTube Ranger (yanger)

  **A terminal-based file manager for YouTube playlists**

  Navigate and manage your YouTube playlists with vim-like keybindings and ranger-style interface.

  ![Python](https://img.shields.io/badge/python-3.10%2B-blue)
  ![License](https://img.shields.io/badge/license-MIT-green)
  ![Status](https://img.shields.io/badge/status-alpha-red)

  ---

  > 🚨 **ALPHA SOFTWARE** 🚨
  >
  > YouTube API operations are **irreversible** (deletions, moves, etc.).
  > Consider backing up your playlists before use.

  ---
</div>

## Features

- **Ranger-style Interface**: Three-column miller view for intuitive navigation
- **Vim Keybindings**: Navigate with hjkl, gg/G, visual mode, and familiar commands
- **Cut/Copy/Paste**: Move videos between playlists as easily as files
- **Persistent Cache**: SQLite-based caching reduces API calls by ~95%
- **Undo/Redo**: Full operation history with u/U commands
- **Command Mode**: Tab-completed commands for advanced operations
- **Google Takeout Import**: Access Watch Later and History (unavailable via API)
- **Transcript Caching**: Fetch, cache, and display video transcripts
- **Bulk Edit**: Edit playlists and videos in external text editor
- **Smart Refresh**: Only fetch from API when needed
- **Quota Management**: Built-in tracking for YouTube API limits
- **Search & Filter**: Find videos quickly with highlighting

## ⚡ Quick Start

### Prerequisites

- Python 3.10+
- YouTube Data API v3 credentials

### Installation

```bash
# Clone and install
git clone https://github.com/yourusername/yanger.git
cd yanger
pip install -e .
```

### API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create project and enable YouTube Data API v3
3. Create OAuth 2.0 credentials (Desktop application)
4. Download and save as `config/client_secret.json`

### First Run

```bash
yanger  # Will authenticate on first run
```

## 🔑 Keybindings

### Navigation
| Key | Action |
|-----|--------|
| `h/j/k/l` | Navigate left/down/up/right |
| `gg/G` | Jump to top/bottom |
| `Enter` | Select playlist/video |

### Selection & Marking
| Key | Action |
|-----|--------|
| `Space` | Mark/unmark current video |
| `V` | Visual mode (range selection) |
| `v` | Invert selection |
| `uv` | Unmark all videos |

### Operations
| Key | Action |
|-----|--------|
| `dd` | Cut selected/marked videos |
| `yy` | Copy selected/marked videos |
| `pp` | Paste videos from clipboard |
| `dD` | Delete selected/marked videos |
| `u/U` | Undo/redo last operation |
| `gn` | Create new playlist |
| `gd` | Delete current playlist |
| `cw` | Rename playlist/video |
| `o` | Open sort menu |
| `r` | Open in browser |
| `B` | Bulk edit in text editor |
| `gt` | Fetch transcript |
| `gT` | Toggle auto-fetch transcripts |
| `ge` | Export transcript |

### Search
| Key | Action |
|-----|--------|
| `/` | Search in current list |
| `n/N` | Next/previous result |
| `ESC` | Cancel search |

### Application
| Key | Action |
|-----|--------|
| `q` | Quit |
| `?` | Show help overlay |
| `:` | Enter command mode |
| `Ctrl+R` | Refresh current view |
| `Ctrl+Shift+R` | Refresh all (clear cache) |

## 💬 Command Mode

Press `:` for tab-completed commands:

```vim
:refresh [all]           # Refresh view or all playlists
:cache [status|clear]    # Manage cache
:sort <field> [order]    # Sort videos by title/date/views/duration
:filter <criteria>       # Filter videos
:export [filename]       # Export to JSON/YAML/CSV
:quota                   # Show API quota usage
:stats                   # Playlist statistics
:bulkedit [--dry-run]    # Bulk edit in external editor
:transcript <action>     # Manage transcripts
```

## ⚙️ Configuration

Configuration: `~/.config/yanger/config.yaml`

```yaml
cache:
  enabled: true
  ttl_days: 7                        # Cache duration
  auto_cleanup: true
  show_all_virtual_playlists: false  # Show only Watch Later/History
  auto_fetch_metadata: true          # Auto-fetch missing titles
  auto_fetch_batch_size: 20

transcripts:
  enabled: true
  auto_fetch: false                  # Auto-fetch on navigation
  store_in_db: true                  # Cache in SQLite
  store_compressed: true             # Compress with gzip (~70% smaller)
  export_directory: ~/.cache/yanger/transcripts
  export_txt: true
  export_json: true
  languages: ["en"]                  # Preferred languages
```

### Cache Behavior

- **Normal navigation**: Uses cache (no API calls)
- **Ctrl+R**: Refreshes current view only
- **Ctrl+Shift+R**: Clears cache and refreshes all
- **Location**: `~/.cache/yanger/cache.db`
- **Benefits**: Instant startup, offline browsing, 95% reduction in API usage

## 📜 Advanced Features

### Video Transcript Caching

Fetch and display video transcripts with automatic caching.

**Fetch Transcripts**:
- Press `gt` to fetch transcript for current video
- Press `gT` to toggle auto-fetch mode
- Use `:transcript fetch` in command mode

**View Transcripts**:
- Automatically displayed at bottom of preview pane
- Shows language and type (auto-generated vs manual)
- Truncated to 1000 characters for readability

**Export Transcripts**:
```bash
# Export current video
Press 'ge'

# Command mode
:transcript export ~/my-transcripts
```

**Formats**:
- **Plain text** (.txt): Transcript as continuous text
- **JSON** (.json): Segments with timestamps and metadata

**Notes**:
- Uses `youtube-transcript-api` library (no YouTube API quota)
- Compressed storage saves ~70% space
- Some videos don't have transcripts (cached as "NOT_AVAILABLE")

### Google Takeout Import

Import Watch Later and History playlists unavailable via API.

**Export from Google**:
1. Go to [Google Takeout](https://takeout.google.com/)
2. Select "YouTube and YouTube Music"
3. Choose JSON format
4. Download and extract

**Import into Yanger**:
```bash
# Import from folder
yanger takeout ~/Downloads/Takeout/

# Fetch metadata for imported videos
yanger fetch-metadata
```

**Features**:
- Auto-fetch metadata for video titles
- Deduplication of imports
- Pagination for large playlists (7000+ videos)
- Works offline with cached content

### Bulk Edit Mode

Edit playlists and videos in your text editor.

```bash
# Press 'B' in app or use command mode
:bulkedit

# Dry run to preview changes
:bulkedit --dry-run
```

Opens markdown format in `$EDITOR`:
```markdown
- Playlist Name <!-- id:PLxxxxx -->
  - Video Title <!-- id:videoId,item:itemId -->
  - Another Video <!-- id:videoId2,item:itemId2 -->
```

**Supported operations**:
- Reorder videos (move lines)
- Move videos between playlists (cut/paste)
- Delete videos (remove lines)
- Rename items (edit text before `<!--`)

### Command Logging

Log all operations for debugging and auditing.

```bash
# Enable logging
yanger run --log session.json

# With verbosity level
yanger run --log debug.json --log-level DEBUG
```

**Log levels**: DEBUG (every keystroke), INFO (commands), WARNING, ERROR

**Output**: Line-delimited JSON for easy parsing

## 📊 API Quota Limits

YouTube Data API daily quota: **10,000 units**

**Operation costs**:
- List operations: 1 unit
- Write operations: 50 units
- Move video: 100 units (add + remove)

**With default quota**:
- List playlists/videos: ~10,000 times
- Move videos: ~100 per day
- Create/update playlists: ~200 operations

**Tips**:
- Cache persists 7 days (no API calls for cached content)
- Check quota with `:quota` command
- Monitor usage in status bar

## Contributing

Contributions welcome! Submit a Pull Request.

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- Inspired by [ranger](https://github.com/ranger/ranger)
- Built with [Textual](https://github.com/Textualize/textual)
