# YouTube Ranger (yanger)

A terminal-based file manager for YouTube playlists, inspired by ranger. Navigate, organize, and manage YouTube playlists with vim-like keybindings and a multi-pane interface.

## Features

- 📁 **Ranger-style Interface**: Three-column miller view for intuitive navigation
- ⌨️ **Vim Keybindings**: Navigate with hjkl, gg/G, and other familiar commands
- ✂️ **Cut/Copy/Paste**: Move videos between playlists as easily as moving files
- 🎯 **Bulk Operations**: Select multiple videos and perform batch operations
- 🔍 **Search & Filter**: Find videos quickly within playlists
- 📊 **Quota Management**: Built-in tracking to stay within YouTube API limits

## Installation

### Prerequisites

- Python 3.10 or higher
- YouTube Data API v3 credentials

### Setup

1. Clone the repository:
```bash
git clone https://github.com/yourusername/yanger.git
cd yanger
```

2. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -e .
```

4. Set up YouTube API credentials:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project or select existing
   - Enable YouTube Data API v3
   - Create OAuth 2.0 credentials (Desktop application type)
   - Download the credentials and save as `config/client_secret.json`

5. Run the authentication test:
```bash
python -m yanger.auth
```

## Project Status

🚧 **Currently in Development** 

This project is in early development. The following components are ready:
- ✅ OAuth2 authentication
- ✅ YouTube API client wrapper
- ✅ Data models for playlists and videos
- ✅ Quota management system

Coming soon:
- [ ] Terminal UI with Textual
- [ ] Vim-style navigation
- [ ] Copy/paste operations
- [ ] Search functionality

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

## Development

See [PLAN.md](PLAN.md) for the detailed development roadmap.

### Project Structure

```
yanger/
├── src/yanger/         # Main package
│   ├── auth.py        # OAuth2 authentication
│   ├── api_client.py  # YouTube API wrapper
│   ├── models.py      # Data models
│   └── ...           # More modules coming
├── config/            # Configuration files
├── tests/            # Test suite
└── PLAN.md          # Development plan
```

## License

MIT License - see LICENSE file for details.