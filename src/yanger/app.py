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
from .ui.miller_view import MillerView, PlaylistSelected, VideoSelected, RangerCommand, MarksChanged
from .ui.status_bar import StatusBar
from .cache import PlaylistCache


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
        self._cache = PlaylistCache(ttl_seconds=300)  # 5 minute cache
        
        # Data
        self.playlists: List[Playlist] = []
        self.current_playlist: Optional[Playlist] = None
        self.current_videos: List[Video] = []
        self.current_video: Optional[Video] = None
        
        # Ranger command state
        self._pending_command: Optional[str] = None
        
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
            
            # Check cache first
            cached_videos = self._cache.get(playlist.id)
            if cached_videos is not None:
                # Use cached data
                self.current_videos = cached_videos
                
                # Update UI immediately
                if self.miller_view:
                    await self.miller_view.set_videos(self.current_videos)
                
                # Update status
                if self.status_bar:
                    self.status_bar.update_context(
                        f"{playlist.title} ({len(self.current_videos)} videos) [cached]"
                    )
                    
                logger.debug(f"Loaded {len(cached_videos)} videos from cache for {playlist.title}")
                return
            
            # Show loading state only when fetching from API
            if self.miller_view:
                await self.miller_view.show_loading_videos()
            
            # Load videos from API
            self.current_videos = await asyncio.to_thread(
                self.api_client.get_playlist_items,
                playlist.id
            )
            
            # Cache the results
            self._cache.set(playlist.id, self.current_videos)
            
            # Update UI
            if self.miller_view:
                await self.miller_view.set_videos(self.current_videos)
            
            # Update status
            if self.status_bar:
                quota_info = f"{self.api_client.get_quota_remaining()}/10000"
                self.status_bar.update_context(
                    f"{playlist.title} ({len(self.current_videos)} videos)"
                )
                self.status_bar.update_status("", quota_info)
                
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
            # Invalidate cache for current playlist
            self._cache.invalidate(self.current_playlist.id)
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
    
    
    async def on_key(self, event: events.Key) -> None:
        """Handle global key events."""
        # Let miller view handle navigation keys and ranger commands
        if self.miller_view and event.key in ['h', 'j', 'k', 'l', 'g', 'G', 'enter', ' ', 'd', 'y', 'p']:
            await self.miller_view.handle_key(event.key)
            event.stop()
        # Handle double-key ranger commands
        elif hasattr(self, '_pending_command') and self._pending_command:
            if self._pending_command == event.key:  # Double key pressed
                await self.execute_ranger_command(self._pending_command)
            self._pending_command = None
            event.stop()
    
    def on_playlist_selected(self, message: PlaylistSelected) -> None:
        """Handle playlist selection message."""
        # Create task to handle async operation
        self.call_later(self.handle_playlist_selection, message.playlist)
    
    def on_video_selected(self, message: VideoSelected) -> None:
        """Handle video selection message."""
        self.call_later(self.handle_video_selection, message.video)
    
    async def handle_playlist_selection(self, playlist: Playlist) -> None:
        """Handle playlist selection."""
        await self.load_playlist_videos(playlist)
    
    async def handle_video_selection(self, video: Video) -> None:
        """Handle video selection."""
        self.current_video = video
        if self.miller_view:
            await self.miller_view.update_preview(video)
    
    def on_ranger_command(self, message: RangerCommand) -> None:
        """Handle ranger-style command initiation."""
        self._pending_command = message.command
        # Show hint in status bar
        if self.status_bar:
            hints = {
                'd': "Press 'd' again to cut",
                'y': "Press 'y' again to copy", 
                'p': "Press 'p' again to paste"
            }
            self.status_bar.update_status(hints.get(message.command, ""), "")
    
    def on_marks_changed(self, message: MarksChanged) -> None:
        """Handle marks changed message."""
        if self.status_bar and message.count > 0:
            self.status_bar.update_status(
                f"{message.count} marked",
                f"{self.api_client.get_quota_remaining()}/10000" if self.api_client else ""
            )
    
    async def execute_ranger_command(self, command: str) -> None:
        """Execute a ranger-style command."""
        if not self.miller_view or not self.miller_view.video_column:
            return
            
        video_column = self.miller_view.video_column
        
        if command == 'd':  # Cut
            marked_videos = video_column.get_marked_videos()
            if marked_videos:
                # Cut marked videos
                self._clipboard.cut(marked_videos, self.current_playlist.id)
                msg = f"Cut {len(marked_videos)} videos"
            elif 0 <= video_column.selected_index < len(video_column.videos):
                # Cut current video
                video = video_column.videos[video_column.selected_index]
                self._clipboard.cut([video], self.current_playlist.id)
                msg = f"Cut: {video.title}"
            else:
                msg = "Nothing to cut"
                
            self.notify(msg, timeout=2)
            if self.status_bar and self.current_playlist:
                self.status_bar.update_status(
                    f"{len(self._clipboard)} in clipboard (cut)",
                    f"{self.api_client.get_quota_remaining()}/10000"
                )
                
        elif command == 'y':  # Copy (yank)
            marked_videos = video_column.get_marked_videos()
            if marked_videos:
                # Copy marked videos
                self._clipboard.copy(marked_videos, self.current_playlist.id)
                msg = f"Copied {len(marked_videos)} videos"
            elif 0 <= video_column.selected_index < len(video_column.videos):
                # Copy current video
                video = video_column.videos[video_column.selected_index]
                self._clipboard.copy([video], self.current_playlist.id)
                msg = f"Copied: {video.title}"
            else:
                msg = "Nothing to copy"
                
            self.notify(msg, timeout=2)
            if self.status_bar:
                self.status_bar.update_status(
                    f"{len(self._clipboard)} in clipboard (copy)",
                    f"{self.api_client.get_quota_remaining()}/10000"
                )
                
        elif command == 'p':  # Paste
            if self._clipboard.is_empty():
                self.notify("Clipboard is empty", severity="warning")
                return
                
            if not self.current_playlist:
                self.notify("No playlist selected", severity="warning")
                return
                
            # Check quota
            operation_cost = 50 * len(self._clipboard)  # Each insert costs 50
            if self._clipboard.get_operation_type() == "cut":
                operation_cost *= 2  # Cut also requires delete
                
            if self.api_client.get_quota_remaining() < operation_cost:
                self.notify(
                    f"Not enough quota. Need {operation_cost}, have {self.api_client.get_quota_remaining()}",
                    severity="error"
                )
                return
                
            # Perform paste operation
            self.call_later(self.paste_videos)
            
    async def paste_videos(self) -> None:
        """Paste videos from clipboard to current playlist."""
        if not self.api_client or not self.current_playlist:
            return
            
        try:
            pasted_count = 0
            operation_type = self._clipboard.get_operation_type()
            
            for item in self._clipboard.items:
                # Add to current playlist
                await asyncio.to_thread(
                    self.api_client.add_video_to_playlist,
                    item.video.id,
                    self.current_playlist.id
                )
                pasted_count += 1
                
                # If cut operation, remove from source
                if operation_type == "cut" and item.video.playlist_item_id:
                    await asyncio.to_thread(
                        self.api_client.remove_video_from_playlist,
                        item.video.playlist_item_id
                    )
                    
            # Clear clipboard after successful paste
            self._clipboard.clear()
            
            # Clear marks in video column
            if self.miller_view and self.miller_view.video_column:
                self.miller_view.video_column.clear_marks()
                # Clear marks indicator
                self.post_message(MarksChanged(0))
            
            # Invalidate cache for affected playlists
            self._cache.invalidate(self.current_playlist.id)
            for item in self._clipboard.items:
                if item.source_playlist_id != self.current_playlist.id:
                    self._cache.invalidate(item.source_playlist_id)
            
            # Refresh current playlist
            await self.load_playlist_videos(self.current_playlist)
            
            self.notify(f"Pasted {pasted_count} videos", timeout=2)
            
        except Exception as e:
            logger.error(f"Error pasting videos: {e}")
            self.notify(f"Paste failed: {e}", severity="error")