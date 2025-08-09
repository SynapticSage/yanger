<div align="center">
  <img src="assets/logo.png" alt="YouTube Ranger Logo" width="200">
  
  # YouTube Ranger (yanger)
  
  **A terminal-based file manager for YouTube playlists**
  
  Navigate and manage your YouTube playlists with vim-like keybindings, persistent caching, and powerful playlist operations.
  
  ![Python](https://img.shields.io/badge/python-3.10%2B-blue)
  ![License](https://img.shields.io/badge/license-MIT-green)
  ![Status](https://img.shields.io/badge/status-active%20development-yellow)
</div>

## Features

- 📁 **Ranger-style Interface**: Three-column miller view for intuitive navigation
- ⌨️ **Vim Keybindings**: Navigate with hjkl, gg/G, and other familiar commands
- ✂️ **Cut/Copy/Paste**: Move videos between playlists as easily as moving files
- 🎯 **Visual Mode**: Range selection for bulk operations (like ranger)
- 🔍 **Search & Filter**: Find videos quickly with highlighting
- 💾 **Persistent Cache**: SQLite-based caching across sessions
- 🔄 **Smart Refresh**: Only fetch from API when needed
- ❓ **Built-in Help**: Dynamic keybinding display
- 💬 **Command Mode**: Tab-completed commands for advanced operations
- 📊 **Quota Management**: Built-in tracking to stay within YouTube API limits
- 🔢 **Sort Videos**: By title, date, views, duration, position

## Quick Start

### Prerequisites

- Python 3.10 or higher
- YouTube Data API v3 credentials

### Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/yanger.git
cd yanger
```

2. Install with pip:
```bash
pip install -e .
```

3. Set up YouTube API credentials:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing
   - Enable YouTube Data API v3
   - Create OAuth 2.0 credentials (Desktop application type)
   - Download credentials and save as `config/client_secret.json`

4. First run (will authenticate):
```bash
yanger
```

## Keybindings

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
| `uV` | Visual unmark mode |

### Operations
| Key | Action |
|-----|--------|
| `dd` | Cut selected/marked videos |
| `yy` | Copy selected/marked videos |
| `pp` | Paste videos from clipboard |
| `o` | Open sort menu |

### Search
| Key | Action |
|-----|--------|
| `/` | Search in current list |
| `n` | Next search result |
| `N` | Previous search result |
| `ESC` | Cancel search |

### Application
| Key | Action |
|-----|--------|
| `q` | Quit |
| `?` | Show help overlay |
| `:` | Enter command mode |
| `Ctrl+R` | Refresh current view |
| `Ctrl+Shift+R` | Refresh all (clear cache) |

## Command Mode

Press `:` to enter command mode with tab completion:

### Available Commands

```vim
:refresh [all]           # Refresh current view or all playlists
:cache [status|clear]    # Manage cache
:sort <field> [order]    # Sort videos
:filter <criteria>       # Filter videos (coming soon)
:clear [marks|search]    # Clear selections/search
:quota                   # Show API quota usage
:stats                   # Show playlist statistics
:help [command]          # Get help for command
```

## Persistent Cache

Yanger uses SQLite to cache playlists and videos across sessions:

### Features
- **Location**: `~/.cache/yanger/cache.db`
- **TTL**: 7 days by default
- **Auto-cleanup**: Expired entries removed automatically

### Benefits
- ⚡ Instant navigation for cached content
- 📉 Dramatic reduction in API quota usage
- 🌐 Offline browsing of previously viewed playlists
- 🚀 Fast startup - no initial API calls

### Refresh Behavior
- **Normal navigation**: Uses cache (no API calls)
- **Ctrl+R**: Refreshes only current view
- **Ctrl+Shift+R**: Clears cache and refreshes everything
- **New playlists**: Fetched automatically from API

## Configuration

Configuration files are stored in `~/.config/yanger/`:

### Cache Settings

Edit `~/.config/yanger/config.yaml`:

```yaml
cache:
  enabled: true
  persistent: true
  directory: ".cache/yanger"  # Relative to home
  ttl_days: 7
  auto_cleanup: true
  max_size_mb: 100
```

## Tips & Tricks

### Efficient Navigation
- 💡 Use the cache! Most navigation won't use any API quota
- 🎯 Mark multiple videos with `Space`, then use `dd`/`yy` for bulk operations
- 📐 Use `V` (visual mode) to quickly select a range of videos
- 🔄 Press `v` to invert selection - useful for "select all except"

### API Quota Management
- 📊 Check quota with `:quota` command
- 💾 Cache persists for 7 days - no API calls for cached content
- 🔄 Use `Ctrl+R` sparingly - only refreshes current view
- 📈 Monitor quota usage in the status bar

### Keyboard Shortcuts
- ❓ Press `?` anytime to see all available keybindings
- 🔡 Commands support tab completion - just press Tab
- 📊 Use `:cache` to see cache statistics
- 🔢 Sort videos quickly with `o` followed by sort key

## API Quota Limits

YouTube Data API has daily quota limits:
- Default quota: 10,000 units per day
- List operations: 1 unit each
- Write operations: 50 units each
- Moving a video: 100 units (add + remove)

With the default quota, you can:
- List playlists/videos: ~10,000 times
- Move videos: ~100 videos per day
- Create/update playlists: ~200 operations

## Development Status

### ✅ Completed Features
- OAuth2 authentication
- YouTube API client wrapper
- Three-column miller view UI
- Vim-style navigation
- Visual mode for bulk selection
- Search with highlighting
- Command mode with tab completion
- Persistent SQLite cache
- Help overlay system
- Sort videos by multiple criteria
- Smart refresh logic
- Cut/copy/paste operations

### 🚧 Planned Features
- [ ] Playlist creation/deletion (gn/gd commands)
- [ ] Rename operations (cw command)
- [ ] Advanced filtering
- [ ] Custom keybinding configuration
- [ ] Export/import playlists
- [ ] Playlist statistics dashboard
- [ ] Undo/redo functionality

## Project Structure

```
yanger/
├── src/yanger/         # Main package
│   ├── auth.py        # OAuth2 authentication
│   ├── api_client.py  # YouTube API wrapper
│   ├── models.py      # Data models
│   ├── cache.py       # Persistent caching
│   ├── app.py         # Main TUI application
│   ├── keybindings.py # Central keybinding registry
│   └── ui/            # UI components
│       ├── miller_view.py    # Three-column layout
│       ├── help_overlay.py   # Help system
│       ├── command_input.py  # Command mode
│       └── ...               # Other UI widgets
├── config/            # Configuration files
└── tests/            # Test suite
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

MIT License - see LICENSE file for details.

## Acknowledgments

- Inspired by [ranger](https://github.com/ranger/ranger) file manager
- Built with [Textual](https://github.com/Textualize/textual) TUI framework
- Uses YouTube Data API v3