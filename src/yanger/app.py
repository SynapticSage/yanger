"""Main YouTube Ranger TUI application.

Coordinates the overall application flow and UI components.
"""
# Created: 2025-08-03

import asyncio
from pathlib import Path
from typing import Optional, List
import logging

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static
from textual.reactive import reactive
from textual import events

from .auth import YouTubeAuth
from .api_client import YouTubeAPIClient
from .models import Playlist, Video, Clipboard
from .ui.miller_view import MillerView, PlaylistSelected, VideoSelected
from .ui.status_bar import StatusBar


logger = logging.getLogger(__name__)


class YouTubeRangerApp(App):
    """Main application class for YouTube Ranger."""
    
    CSS_PATH = "app.tcss"
    TITLE = "YouTube Ranger"
    SUB_TITLE = "Navigate playlists like files"
    
    # Keybindings
    BINDINGS = [
        Binding("q", "quit", "Quit", priority=True),
        Binding("?", "help", "Help"),
        Binding(":", "command_mode", "Command"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("ctrl+q", "force_quit", "Force Quit", show=False),
    ]
    
    # Reactive attributes
    show_help = reactive(False)
    command_mode = reactive(False)
    
    def __init__(self, 
                 config_dir: Optional[Path] = None,
                 use_cache: bool = True):
        """Initialize the application.
        
        Args:
            config_dir: Configuration directory path
            use_cache: Whether to use offline cache
        """
        super().__init__()
        
        self.config_dir = config_dir or Path.home() / ".config" / "yanger"
        self.use_cache = use_cache
        
        # Core components (initialized in on_mount)
        self.auth: Optional[YouTubeAuth] = None
        self.api_client: Optional[YouTubeAPIClient] = None
        self._clipboard = Clipboard()
        
        # Data
        self.playlists: List[Playlist] = []
        self.current_playlist: Optional[Playlist] = None
        self.current_videos: List[Video] = []
        self.current_video: Optional[Video] = None
        
        # UI components
        self.miller_view: Optional[MillerView] = None
        self.status_bar: Optional[StatusBar] = None
        
    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield Header()
        
        # Main content area
        with Container(id="main-container"):
            # Miller view will be added here
            yield Static("Initializing...", id="loading-message")
        
        # Status bar at bottom
        yield StatusBar(id="status-bar")
        yield Footer()
    
    async def on_mount(self) -> None:
        """Initialize the application after mounting."""
        try:
            # Setup authentication
            await self.setup_authentication()
            
            # Create miller view
            loading_msg = self.query_one("#loading-message")
            await loading_msg.remove()
            
            container = self.query_one("#main-container")
            self.miller_view = MillerView(id="miller-view")
            await container.mount(self.miller_view)
            
            # Get status bar reference
            self.status_bar = self.query_one("#status-bar", StatusBar)
            
            # Load initial data
            await self.load_playlists()
            
        except Exception as e:
            logger.error(f"Error during initialization: {e}")
            self.notify(f"Initialization error: {e}", severity="error")
            self.exit(1)
    
    async def setup_authentication(self) -> None:
        """Setup YouTube API authentication."""
        try:
            self.auth = YouTubeAuth()
            self.auth.authenticate()
            self.api_client = YouTubeAPIClient(self.auth)
            
        except FileNotFoundError:
            self.notify(
                "OAuth2 credentials not found. Run 'yanger auth' to setup.",
                severity="error"
            )
            self.exit(1)
        except Exception as e:
            self.notify(f"Authentication error: {e}", severity="error")
            self.exit(1)
    
    async def load_playlists(self) -> None:
        """Load user's playlists."""
        if not self.api_client:
            return
            
        try:
            # Show loading state
            if self.miller_view:
                await self.miller_view.show_loading_playlists()
            
            # Load playlists in background
            self.playlists = await asyncio.to_thread(
                self.api_client.get_playlists
            )
            
            # Update UI
            if self.miller_view:
                await self.miller_view.set_playlists(self.playlists)
            
            # Update status
            if self.status_bar:
                quota_info = f"{self.api_client.get_quota_remaining()}/10000"
                self.status_bar.update_status(
                    f"Loaded {len(self.playlists)} playlists",
                    quota_info
                )
                
        except Exception as e:
            logger.error(f"Error loading playlists: {e}")
            self.notify(f"Failed to load playlists: {e}", severity="error")
    
    async def load_playlist_videos(self, playlist: Playlist) -> None:
        """Load videos for a specific playlist."""
        if not self.api_client:
            return
            
        try:
            # Update current playlist
            self.current_playlist = playlist
            
            # Show loading state
            if self.miller_view:
                await self.miller_view.show_loading_videos()
            
            # Load videos
            self.current_videos = await asyncio.to_thread(
                self.api_client.get_playlist_items,
                playlist.id
            )
            
            # Update UI
            if self.miller_view:
                await self.miller_view.set_videos(self.current_videos)
            
            # Update status
            if self.status_bar:
                self.status_bar.update_context(
                    f"{playlist.title} ({len(self.current_videos)} videos)"
                )
                
        except Exception as e:
            logger.error(f"Error loading videos: {e}")
            self.notify(f"Failed to load videos: {e}", severity="error")
    
    def action_quit(self) -> None:
        """Quit the application."""
        self.exit(0)
    
    def action_force_quit(self) -> None:
        """Force quit without confirmation."""
        self.exit(0)
    
    async def action_refresh(self) -> None:
        """Refresh current view."""
        if self.current_playlist:
            await self.load_playlist_videos(self.current_playlist)
        else:
            await self.load_playlists()
        
        self.notify("Refreshed", timeout=2)
    
    def action_help(self) -> None:
        """Show help overlay."""
        # TODO: Implement help overlay
        self.notify("Help not implemented yet", severity="warning")
    
    def action_command_mode(self) -> None:
        """Enter command mode."""
        # TODO: Implement command mode
        self.notify("Command mode not implemented yet", severity="warning")
    
    async def on_playlist_selected(self, playlist: Playlist) -> None:
        """Handle playlist selection."""
        await self.load_playlist_videos(playlist)
    
    async def on_video_selected(self, video: Video) -> None:
        """Handle video selection."""
        self.current_video = video
        
        # Update preview if miller view exists
        if self.miller_view:
            await self.miller_view.update_preview(video)
    
    async def on_key(self, event: events.Key) -> None:
        """Handle global key events."""
        # Let miller view handle navigation keys
        if self.miller_view and event.key in ['h', 'j', 'k', 'l', 'g', 'G']:
            await self.miller_view.handle_key(event.key)
            event.stop()
    
    async def on_playlist_selected_message(self, message: PlaylistSelected) -> None:
        """Handle playlist selection message."""
        await self.on_playlist_selected(message.playlist)
    
    async def on_video_selected_message(self, message: VideoSelected) -> None:
        """Handle video selection message."""
        await self.on_video_selected(message.video)