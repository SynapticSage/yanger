"""Settings management for YouTube Ranger.

Handles loading and merging configuration from multiple sources.
"""
# Created: 2025-08-03

import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class UISettings:
    """UI-related settings."""
    colorscheme: str = "default"
    show_hidden: bool = False  # Show private playlists
    preview_lines: int = 10
    column_ratios: list = field(default_factory=lambda: [0.3, 0.4, 0.3])
    show_icons: bool = True
    confirm_delete: bool = True
    auto_refresh: bool = False
    refresh_interval: int = 300  # seconds


@dataclass
class KeybindingSettings:
    """Keybinding configuration."""
    quit: list = field(default_factory=lambda: ['q', 'Q'])
    help: list = field(default_factory=lambda: ['?'])
    command: list = field(default_factory=lambda: [':'])
    search: list = field(default_factory=lambda: ['/'])
    
    # Navigation
    up: list = field(default_factory=lambda: ['k'])
    down: list = field(default_factory=lambda: ['j'])
    left: list = field(default_factory=lambda: ['h'])
    right: list = field(default_factory=lambda: ['l'])
    top: list = field(default_factory=lambda: ['g', 'g'])
    bottom: list = field(default_factory=lambda: ['G'])
    
    # Selection
    mark: list = field(default_factory=lambda: [' '])  # space
    visual: list = field(default_factory=lambda: ['v'])
    unselect_all: list = field(default_factory=lambda: ['u', 'v'])
    invert_selection: list = field(default_factory=lambda: ['V'])
    
    # Operations
    copy: list = field(default_factory=lambda: ['y', 'y'])
    cut: list = field(default_factory=lambda: ['d', 'd'])
    paste: list = field(default_factory=lambda: ['p', 'p'])
    delete: list = field(default_factory=lambda: ['d', 'D'])
    rename: list = field(default_factory=lambda: ['c', 'w'])
    
    # Playlist operations
    new_playlist: list = field(default_factory=lambda: ['g', 'n'])
    delete_playlist: list = field(default_factory=lambda: ['g', 'd'])
    refresh: list = field(default_factory=lambda: ['g', 'r'])
    refresh_all: list = field(default_factory=lambda: ['g', 'R'])


@dataclass
class CacheSettings:
    """Cache-related settings."""
    enabled: bool = True
    persistent: bool = True  # Use persistent SQLite cache
    directory: str = ".cache/yanger"  # Relative to home directory
    ttl_days: int = 7  # Time-to-live in days
    auto_cleanup: bool = True  # Automatically clean expired entries
    max_size_mb: int = 100
    load_on_startup: bool = False  # Load playlists automatically on startup
    show_all_virtual_playlists: bool = False  # Show all virtual playlists (not just Watch Later/History)
    auto_fetch_metadata: bool = True  # Auto-fetch metadata for videos without titles
    auto_fetch_batch_size: int = 20  # Number of videos to auto-fetch at once (max 50)


@dataclass
class ProxySettings:
    """Proxy settings for transcript fetching."""
    enabled: bool = False
    type: str = "generic"  # "generic" or "webshare"
    http_url: str = ""  # For generic: http://user:pass@proxy:8080
    https_url: str = ""  # For generic: https://user:pass@proxy:8080
    webshare_username: str = ""  # For webshare
    webshare_password: str = ""  # For webshare
    webshare_locations: list = field(default_factory=list)  # e.g., ["us", "de"]


@dataclass
class TranscriptSettings:
    """Transcript caching settings."""
    enabled: bool = True
    auto_fetch: bool = False  # Auto-fetch on hover vs manual
    store_in_db: bool = True  # Store in SQLite database
    store_compressed: bool = True  # Compress transcripts in DB (gzip)
    export_directory: Optional[str] = None  # External folder for transcript files
    export_txt: bool = True  # Export plain text files
    export_json: bool = True  # Export JSON files with timestamps
    languages: list = field(default_factory=lambda: ["en"])  # Preferred languages
    proxy: ProxySettings = field(default_factory=ProxySettings)  # Proxy configuration
    # External shell command run by `:transcript` against the current video.
    # Supports {url}/{id} placeholders; empty = unset (see resolve_transcript_command).
    transcript_command: str = ""


@dataclass
class YouTubeSettings:
    """YouTube API settings."""
    client_secrets_file: str = "config/client_secret.json"
    token_file: str = "token.json"
    max_results_per_page: int = 50
    quota_warning_threshold: int = 7500  # Show warning at 75%
    quota_critical_threshold: int = 9000  # Show critical at 90%


@dataclass
class Settings:
    """Main settings container."""
    ui: UISettings = field(default_factory=UISettings)
    keybindings: KeybindingSettings = field(default_factory=KeybindingSettings)
    cache: CacheSettings = field(default_factory=CacheSettings)
    transcripts: TranscriptSettings = field(default_factory=TranscriptSettings)
    youtube: YouTubeSettings = field(default_factory=YouTubeSettings)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Settings':
        """Create Settings from dictionary."""
        settings = cls()
        
        # Update UI settings
        if 'ui' in data:
            for key, value in data['ui'].items():
                if hasattr(settings.ui, key):
                    setattr(settings.ui, key, value)
        
        # Update keybindings
        if 'keybindings' in data:
            for key, value in data['keybindings'].items():
                if hasattr(settings.keybindings, key):
                    # Ensure keybindings are lists
                    if isinstance(value, str):
                        value = [value]
                    setattr(settings.keybindings, key, value)
        
        # Update cache settings
        if 'cache' in data:
            for key, value in data['cache'].items():
                if hasattr(settings.cache, key):
                    setattr(settings.cache, key, value)

        # Update transcript settings
        if 'transcripts' in data:
            for key, value in data['transcripts'].items():
                if key == 'proxy' and isinstance(value, dict):
                    # Handle nested proxy settings
                    for pkey, pvalue in value.items():
                        if hasattr(settings.transcripts.proxy, pkey):
                            setattr(settings.transcripts.proxy, pkey, pvalue)
                elif hasattr(settings.transcripts, key):
                    setattr(settings.transcripts, key, value)

        # Update YouTube settings
        if 'youtube' in data:
            for key, value in data['youtube'].items():
                if hasattr(settings.youtube, key):
                    setattr(settings.youtube, key, value)

        return settings
    
    def merge(self, other: 'Settings') -> None:
        """Merge another Settings object into this one."""
        # Merge each section
        for section in ['ui', 'keybindings', 'cache', 'transcripts', 'youtube']:
            self_section = getattr(self, section)
            other_section = getattr(other, section)

            for key in vars(other_section):
                value = getattr(other_section, key)
                if value is not None:  # Only override non-None values
                    setattr(self_section, key, value)


def load_settings(config_dir: Optional[Path] = None) -> Settings:
    """Load settings from configuration files.
    
    Loads from multiple sources in order of precedence:
    1. Default settings (built-in)
    2. System config file
    3. User config file
    4. Environment variables
    
    Args:
        config_dir: Optional config directory override
        
    Returns:
        Merged Settings object
    """
    # Load .env files so env-var overrides below (and resolve_transcript_command)
    # see them. User config .env wins over a project-local .env; neither overrides
    # variables already exported in the shell (load_dotenv default).
    from dotenv import load_dotenv
    load_dotenv(Path.home() / ".config" / "yanger" / ".env")
    load_dotenv()

    settings = Settings()

    # Determine config directory
    if config_dir is None:
        config_dir = Path.home() / ".config" / "yanger"

    # Load system default config
    default_config_path = Path(__file__).parent.parent.parent.parent / "config" / "default_config.yaml"
    if default_config_path.exists():
        try:
            with open(default_config_path) as f:
                data = yaml.safe_load(f)
                if data:
                    settings = Settings.from_dict(data)
        except Exception as e:
            # Route to stderr via logging: stdout is the MCP JSON-RPC channel.
            logger.warning(f"Failed to load default config: {e}")

    # Load user config
    user_config_path = config_dir / "config.yaml"
    if user_config_path.exists():
        try:
            with open(user_config_path) as f:
                data = yaml.safe_load(f)
                if data:
                    user_settings = Settings.from_dict(data)
                    settings.merge(user_settings)
        except Exception as e:
            logger.warning(f"Failed to load user config: {e}")

    # Override with environment variables
    if api_key := os.environ.get('YOUTUBE_API_KEY'):
        settings.youtube.api_key = api_key

    if cache_dir := os.environ.get('YANGER_CACHE_DIR'):
        settings.cache.directory = cache_dir

    if transcript_cmd := os.environ.get('YANGER_TRANSCRIPT_COMMAND'):
        settings.transcripts.transcript_command = transcript_cmd

    return settings


def save_settings(settings: Settings, config_dir: Optional[Path] = None) -> None:
    """Save settings to user config file.
    
    Args:
        settings: Settings object to save
        config_dir: Optional config directory override
    """
    if config_dir is None:
        config_dir = Path.home() / ".config" / "yanger"
    
    # Ensure directory exists
    config_dir.mkdir(parents=True, exist_ok=True)
    
    # Convert to dictionary
    transcript_data = dict(vars(settings.transcripts))
    # Handle nested proxy settings
    if hasattr(settings.transcripts, 'proxy'):
        transcript_data['proxy'] = vars(settings.transcripts.proxy)

    data = {
        'ui': vars(settings.ui),
        'keybindings': vars(settings.keybindings),
        'cache': vars(settings.cache),
        'transcripts': transcript_data,
        'youtube': vars(settings.youtube)
    }
    
    # Save to file
    config_path = config_dir / "config.yaml"
    with open(config_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# Maps a `:set` key to the config-file section it is stored under.
_USER_SETTING_SECTIONS = {
    "transcript_command": "transcripts",
}


def save_user_setting(key: str, value: Any, config_dir: Optional[Path] = None) -> None:
    """Persist a single setting to the user config YAML, merging (not overwriting).

    Used by the runtime `:set` command so a one-off tweak doesn't rewrite every
    default. Known keys (e.g. transcript_command) are stored under their section
    so they load back correctly.
    """
    if config_dir is None:
        config_dir = Path.home() / ".config" / "yanger"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"

    data: Dict[str, Any] = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning(f"Could not read user config for update: {e}")
            data = {}

    section = _USER_SETTING_SECTIONS.get(key)
    if section:
        data.setdefault(section, {})[key] = value
    else:
        data[key] = value

    with open(config_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)