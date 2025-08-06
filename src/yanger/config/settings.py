"""Settings management for YouTube Ranger.

Handles loading and merging configuration from multiple sources.
"""
# Created: 2025-08-03

import os
from pathlib import Path
from typing import Dict, Any, Optional
import yaml
from dataclasses import dataclass, field


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
    directory: str = ".yanger_cache"
    ttl: int = 3600  # seconds
    max_size_mb: int = 100


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
        
        # Update YouTube settings
        if 'youtube' in data:
            for key, value in data['youtube'].items():
                if hasattr(settings.youtube, key):
                    setattr(settings.youtube, key, value)
        
        return settings
    
    def merge(self, other: 'Settings') -> None:
        """Merge another Settings object into this one."""
        # Merge each section
        for section in ['ui', 'keybindings', 'cache', 'youtube']:
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
            print(f"Warning: Failed to load default config: {e}")
    
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
            print(f"Warning: Failed to load user config: {e}")
    
    # Override with environment variables
    if api_key := os.environ.get('YOUTUBE_API_KEY'):
        settings.youtube.api_key = api_key
    
    if cache_dir := os.environ.get('YANGER_CACHE_DIR'):
        settings.cache.directory = cache_dir
    
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
    data = {
        'ui': vars(settings.ui),
        'keybindings': vars(settings.keybindings),
        'cache': vars(settings.cache),
        'youtube': vars(settings.youtube)
    }
    
    # Save to file
    config_path = config_dir / "config.yaml"
    with open(config_path, 'w') as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)