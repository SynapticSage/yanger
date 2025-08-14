"""Main YouTube Ranger TUI application.

Coordinates the overall application flow and UI components.
"""
# Created: 2025-08-03

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List
import logging

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Static
from textual.reactive import reactive
from textual import events

from .auth import YouTubeAuth
from .api_client import YouTubeAPIClient
from .models import Playlist, Video, Clipboard, PrivacyStatus
from .ui.miller_view import MillerView, PlaylistSelected, VideoSelected, RangerCommand, MarksChanged, SearchStatusUpdate, SortMenuRequest
from .ui.status_bar import StatusBar
from .ui.help_overlay import HelpOverlay
from .ui.command_input import CommandInput, parse_command
from .cache import PersistentCache
from .keybindings import registry
from .config.settings import load_settings


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
        Binding("ctrl+shift+r", "refresh_all", "Refresh All"),
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
        self._cache = PersistentCache()  # Persistent SQLite cache
        
        # Data
        self.playlists: List[Playlist] = []
        self.current_playlist: Optional[Playlist] = None
        self.current_videos: List[Video] = []
        self.current_video: Optional[Video] = None
        self.unfiltered_videos: List[Video] = []  # Original videos before filtering
        self.playlists_loaded: bool = False  # Track if playlists have been loaded
        
        # Settings
        self.settings = load_settings()
        
        # Ranger command state
        self._pending_command: Optional[str] = None
        self._pending_sort: bool = False
        
        # UI components
        self.miller_view: Optional[MillerView] = None
        self.status_bar: Optional[StatusBar] = None
        self.help_overlay: Optional[HelpOverlay] = None
        self.command_input: Optional[CommandInput] = None
        
    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield Header()
        
        # Main content area
        with Container(id="main-container"):
            # Miller view will be added here
            yield Static("Initializing...", id="loading-message")
        
        # Command input (hidden by default, docked at bottom)
        self.command_input = CommandInput(
            on_submit=lambda cmd: self.call_later(self.execute_command, cmd),
            on_cancel=self.cancel_command,
            id="command-input"
        )
        yield self.command_input
        
        # Status bar at bottom (below command input when visible)
        yield StatusBar(id="status-bar")
        
        # Help overlay (hidden by default)
        self.help_overlay = HelpOverlay()
        yield self.help_overlay
    
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
            
            # Check if we should load playlists on startup
            if self.settings.cache.load_on_startup:
                await self.load_playlists()
            else:
                # Show empty state with instructions
                if self.status_bar:
                    self.status_bar.update_status(
                        "Press Ctrl+R to load playlists",
                        f"{self.api_client.get_quota_remaining() if self.api_client else 10000}/10000"
                    )
                self.notify("Press Ctrl+R to load playlists", timeout=5)
            
        except Exception as e:
            logger.error(f"Error during initialization: {e}")
            self.notify(f"Initialization error: {e}", severity="error")
            self.exit(1)
    
    def _append_virtual_playlists(self) -> None:
        """Load and append virtual playlists from database."""
        try:
            virtual_playlists = self._cache.get_virtual_playlists()
            
            for vp in virtual_playlists:
                playlist = Playlist(
                    id=f"virtual_{vp['id']}",
                    title=f"💾 {vp['title']}",
                    description=f"{vp['description']} (Virtual playlist from {vp['source']})",
                    item_count=vp['video_count'],
                    is_virtual=True,
                    is_special=True,  # Prevent YouTube sync operations
                    source=vp['source'],
                    imported_at=vp.get('imported_at'),
                    privacy_status=PrivacyStatus.PRIVATE
                )
                self.playlists.append(playlist)
            
            if virtual_playlists:
                logger.debug(f"Added {len(virtual_playlists)} virtual playlists")
        except Exception as e:
            logger.warning(f"Could not load virtual playlists: {e}")
    
    def _append_special_playlists(self) -> None:
        """Append special playlists (Watch Later, History) to the playlist list.
        
        Note: As of 2024, YouTube API v3 no longer provides access to Watch Later (WL)
        or History (HL) playlist contents. These are shown for awareness but will
        appear empty when accessed.
        """
        # Define special playlists with notes about limitations
        special_playlists = [
            Playlist(
                id="WL",
                title="📌 Watch Later (API Limited)",
                description="Watch Later playlist - API access restricted by YouTube since 2016",
                channel_title="YouTube",
                is_special=True,
                privacy_status=PrivacyStatus.PRIVATE,
                item_count=0  # Will always be 0 due to API restrictions
            ),
            Playlist(
                id="HL",
                title="📜 History (Not Available)",
                description="Watch History - No longer available via API. Use Google Takeout instead",
                channel_title="YouTube",
                is_special=True,
                privacy_status=PrivacyStatus.PRIVATE,
                item_count=0  # Not accessible via API
            ),
        ]
        
        # Remove any existing special playlists to avoid duplicates
        self.playlists = [p for p in self.playlists if not p.is_special]
        
        # Add special playlists at the end
        self.playlists.extend(special_playlists)
        logger.debug(f"Added {len(special_playlists)} special playlists (with API limitations)")
    
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
    
    async def load_playlists(self, force_refresh: bool = False) -> None:
        """Load user's playlists.
        
        Args:
            force_refresh: Force refresh from API even if cached
        """
        if not self.api_client:
            return
        
        # Mark that we've loaded playlists at least once
        self.playlists_loaded = True
            
        try:
            # Try to load from cache first
            if not force_refresh:
                cached_playlists = self._cache.get_playlists()
                if cached_playlists:
                    self.playlists = cached_playlists
                    
                    # Append special playlists
                    self._append_special_playlists()
                    
                    # Append virtual playlists
                    self._append_virtual_playlists()
                    
                    # Update UI immediately with cached data
                    if self.miller_view:
                        await self.miller_view.set_playlists(self.playlists)
                    
                    # Update status
                    if self.status_bar:
                        self.status_bar.update_status(
                            f"Loaded {len(self.playlists)} playlists [cached]",
                            f"{self.api_client.get_quota_remaining()}/10000"
                        )
                    
                    logger.debug(f"Loaded {len(self.playlists)} playlists from cache")
                    return
            
            # Show loading state only when fetching from API
            if self.miller_view:
                await self.miller_view.show_loading_playlists()
            
            # Load playlists from API (without special playlists to avoid caching them)
            self.playlists = await asyncio.to_thread(
                self.api_client.get_playlists,
                include_special=False  # Don't include special playlists from API
            )
            
            # Cache the regular playlists (not special ones)
            self._cache.set_playlists(self.playlists)
            
            # Now append special playlists after caching
            self._append_special_playlists()
            
            # Append virtual playlists
            self._append_virtual_playlists()
            
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
    
    async def load_playlist_videos(self, playlist: Playlist, force_refresh: bool = False) -> None:
        """Load videos for a specific playlist.
        
        Args:
            playlist: Playlist to load videos for
            force_refresh: Force refresh from API even if cached
        """
        if not self.api_client:
            return
            
        try:
            # Check if this is a virtual playlist
            if playlist.is_virtual:
                # Load videos from virtual playlist database
                if playlist.id.startswith("virtual_"):
                    virtual_id = playlist.id.replace("virtual_", "")
                    videos_data = self._cache.get_virtual_videos(virtual_id)
                    
                    # Convert to Video objects
                    self.current_videos = []
                    for v in videos_data:
                        video = Video(
                            id=v['video_id'],
                            playlist_item_id=f"virtual_{v['video_id']}",
                            title=v.get('title', v['video_id']),
                            channel_title=v.get('channel_title', 'Unknown'),
                            position=v.get('position', 0)
                        )
                        self.current_videos.append(video)
                    
                    self.unfiltered_videos = self.current_videos.copy()
                    
                    # Update UI
                    if self.miller_view:
                        await self.miller_view.set_videos(self.current_videos)
                    
                    # Update status
                    if self.status_bar:
                        self.status_bar.update_status(
                            f"Loaded {len(self.current_videos)} videos from virtual playlist",
                            "Virtual"
                        )
                    
                    self.notify(f"Loaded {len(self.current_videos)} videos from {playlist.title}", timeout=2)
                    return
            
            # Check if this is a restricted special playlist
            elif playlist.id == "WL":
                self.notify(
                    "Watch Later playlist is restricted by YouTube API since 2016. Cannot retrieve videos.",
                    severity="warning",
                    timeout=5
                )
                self.current_videos = []
                if self.miller_view:
                    await self.miller_view.set_videos([])
                return
            elif playlist.id == "HL":
                self.notify(
                    "History playlist is not available via API. Use Google Takeout to export your watch history.",
                    severity="warning",
                    timeout=5
                )
                self.current_videos = []
                if self.miller_view:
                    await self.miller_view.set_videos([])
                return
            
            # Update current playlist
            self.current_playlist = playlist
            
            # Check cache first (unless force refresh)
            if not force_refresh:
                cached_videos = self._cache.get_videos(playlist.id)
                if cached_videos is not None:
                    # Use cached data
                    self.current_videos = cached_videos
                    self.unfiltered_videos = cached_videos.copy()
                    
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
            self.unfiltered_videos = self.current_videos.copy()
            
            # Cache the results
            self._cache.set_videos(playlist.id, self.current_videos)
            
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
        """Refresh current view (Ctrl+R)."""
        if not self.playlists_loaded:
            # First time loading playlists - use cache if available
            await self.load_playlists(force_refresh=False)
            self.notify("Loaded playlists", timeout=2)
        elif self.current_playlist:
            # Force refresh current playlist from API
            await self.load_playlist_videos(self.current_playlist, force_refresh=True)
            self.notify(f"Refreshed {self.current_playlist.title}", timeout=2)
        else:
            # Force refresh playlist list
            await self.load_playlists(force_refresh=True)
            self.notify("Refreshed playlists", timeout=2)
    
    async def action_refresh_all(self) -> None:
        """Refresh all playlists (Ctrl+Shift+R)."""
        # Clear entire cache
        self._cache.clear()
        
        # Reload playlists from API
        await self.load_playlists(force_refresh=True)
        
        # If we have a current playlist, reload its videos too
        if self.current_playlist:
            await self.load_playlist_videos(self.current_playlist, force_refresh=True)
        
        self.notify("Refreshed all playlists", timeout=3)
    
    def action_help(self) -> None:
        """Show help overlay."""
        if self.help_overlay:
            self.help_overlay.show()
    
    def action_command_mode(self) -> None:
        """Enter command mode."""
        if self.command_input:
            self.command_input.show(":")
    
    
    async def on_key(self, event: events.Key) -> None:
        """Handle global key events."""
        # FIRST: Check for pending sort selection
        if hasattr(self, '_pending_sort') and self._pending_sort:
            if event.key in ['t', 'd', 'p', 'v', 'D', 'escape']:
                await self.handle_sort_key(event.key)
            self._pending_sort = False
            event.stop()
        # SECOND: Check for pending double-key ranger commands
        elif hasattr(self, '_pending_command') and self._pending_command:
            if self._pending_command == event.key:  # Double key pressed
                await self.execute_ranger_command(self._pending_command)
            else:
                # Cancel pending command if different key pressed
                if self.status_bar:
                    self.status_bar.update_status("", "")
            self._pending_command = None
            event.stop()
        # THEN: Let miller view handle navigation keys, ranger commands, search, and visual mode
        # V = visual mode, v = invert selection, space = toggle mark (no auto-advance)
        elif self.miller_view and event.key in ['h', 'j', 'k', 'l', 'g', 'G', 'enter', 'space', 'd', 'y', 'p', '/', 'n', 'N', 'v', 'V', 'u', 'escape', 'o']:
            await self.miller_view.handle_key(event.key)
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
        if self.status_bar and self.current_playlist:
            # Update context to show Mrk indicator
            playlist_info = f"{self.current_playlist.title} ({len(self.current_videos)} videos)"
            self.status_bar.update_context(playlist_info, marked_count=message.count)
            
            # Clear the status message if no marks
            if message.count == 0:
                self.status_bar.update_status(
                    "",
                    f"{self.api_client.get_quota_remaining()}/10000" if self.api_client else ""
                )
    
    def on_search_status_update(self, message: SearchStatusUpdate) -> None:
        """Handle search status update message."""
        if self.status_bar:
            if message.total > 0:
                self.status_bar.update_status(
                    f"Search: {message.current}/{message.total} matches",
                    f"{self.api_client.get_quota_remaining()}/10000" if self.api_client else ""
                )
    
            elif message.current == 0 and message.total == 0:
                # Search cancelled or no matches
                self.status_bar.update_status(
                    "No matches" if self.miller_view and self.miller_view.search_active else "",
                    f"{self.api_client.get_quota_remaining()}/10000" if self.api_client else ""
                )
    
    def on_sort_menu_request(self, message: SortMenuRequest) -> None:
        """Handle sort menu request."""
        # For now, show a simple notification with sort options
        # In the future, this could be a proper overlay widget
        self.notify(
            "Sort by: (t)itle, (d)ate added, (p)osition, (v)iews, (D)uration\n"
            "Press key to sort, ESC to cancel",
            timeout=5
        )
        self._pending_sort = True
    
    async def handle_sort_key(self, key: str) -> None:
        """Handle sort key selection."""
        if key == 'escape':
            self.notify("Sort cancelled", timeout=1)
            return
            
        if not self.miller_view or not self.miller_view.video_column:
            return
            
        videos = self.miller_view.video_column.videos
        if not videos:
            return
            
        # Sort the videos based on the key
        if key == 't':  # Title
            sorted_videos = sorted(videos, key=lambda v: v.title.lower())
            sort_type = "title"
        elif key == 'd':  # Date added
            sorted_videos = sorted(videos, key=lambda v: v.added_at or datetime.min, reverse=True)
            sort_type = "date added"
        elif key == 'p':  # Position
            sorted_videos = sorted(videos, key=lambda v: v.position)
            sort_type = "position"
        elif key == 'v':  # Views
            sorted_videos = sorted(videos, key=lambda v: v.view_count or 0, reverse=True)
            sort_type = "views"
        elif key == 'D':  # Duration
            sorted_videos = sorted(videos, key=lambda v: v.duration or "", reverse=True)
            sort_type = "duration"
        else:
            return
            
        # Update the video column with sorted videos
        await self.miller_view.set_videos(sorted_videos)
        self.notify(f"Sorted by {sort_type}", timeout=2)
    
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
            self._cache.invalidate_playlist(self.current_playlist.id)
            for item in self._clipboard.items:
                if item.source_playlist_id != self.current_playlist.id:
                    self._cache.invalidate_playlist(item.source_playlist_id)
            
            # Refresh current playlist
            await self.load_playlist_videos(self.current_playlist)
            
            self.notify(f"Pasted {pasted_count} videos", timeout=2)
            
        except Exception as e:
            logger.error(f"Error pasting videos: {e}")
            self.notify(f"Paste failed: {e}", severity="error")
    
    def execute_command(self, command: str) -> None:
        """Execute a command entered in command mode.
        
        Args:
            command: Command string starting with ':'
        """
        # Parse command
        cmd_name, args = parse_command(command)
        
        if not cmd_name:
            return
            
        # Handle built-in commands
        if cmd_name == "quit" or cmd_name == "q":
            self.exit(0)
            
        elif cmd_name == "help":
            if args and args[0] in registry.commands:
                # Show help for specific command
                cmd = registry.get_command(args[0])
                if cmd:
                    help_text = f"{cmd.name}: {cmd.description}\n"
                    help_text += f"Syntax: {cmd.syntax}\n"
                    help_text += "Examples:\n"
                    for example in cmd.examples:
                        help_text += f"  {example}\n"
                    self.notify(help_text, timeout=10)
            else:
                # Show general help
                self.action_help()
                
        elif cmd_name == "refresh":
            if args and args[0] == "all":
                # Refresh all playlists
                self.call_later(self.action_refresh_all)
            else:
                # Refresh current view
                self.call_later(self.action_refresh)
        
        elif cmd_name == "cache":
            if not args:
                # Show cache status
                stats = self._cache.get_stats()
                cache_info = f"""Cache Statistics:
                Playlists: {stats['playlist_count']}
                Videos: {stats['video_count']}
                Total Hits: {stats['total_hits']}
                Size: {stats['cache_size_mb']:.2f} MB
                Path: {stats['cache_path']}
                TTL: {stats['ttl_days']} days"""
                self.notify(cache_info, timeout=10)
            elif args[0] == "clear":
                self._cache.clear()
                self.notify("Cache cleared", timeout=2)
            elif args[0] == "expire" and len(args) > 1:
                playlist_id = args[1]
                self._cache.invalidate_playlist(playlist_id)
                self.notify(f"Expired cache for playlist {playlist_id}", timeout=2)
            else:
                self.notify("Usage: :cache [clear|expire <playlist_id>]", severity="warning")
                
        elif cmd_name == "clear":
            if not args:
                self.notify("Usage: :clear [marks|filter|search]", severity="warning")
            elif args[0] == "marks":
                if self.miller_view and self.miller_view.video_column:
                    self.miller_view.video_column.clear_marks()
                    self.post_message(MarksChanged(0))
                    self.notify("Cleared all marks", timeout=2)
            elif args[0] == "filter":
                # Clear filter and restore original videos
                if self.miller_view and self.miller_view.video_column and self.unfiltered_videos:
                    self.call_later(self.miller_view.set_videos, self.unfiltered_videos)
                    self.current_videos = self.unfiltered_videos.copy()
                    self.notify("Cleared filter", timeout=2)
            elif args[0] == "search":
                if self.miller_view and self.miller_view.video_column:
                    self.miller_view.video_column.clear_search()
                    self.notify("Cleared search", timeout=2)
                    
        elif cmd_name == "quota":
            if self.api_client:
                quota_used = 10000 - self.api_client.get_quota_remaining()
                quota_remaining = self.api_client.get_quota_remaining()
                percentage = (quota_used / 10000) * 100
                self.notify(
                    f"API Quota: {quota_used}/10000 used ({percentage:.1f}%)\n"
                    f"Remaining: {quota_remaining}",
                    timeout=5
                )
                
        elif cmd_name == "sort":
            # TODO: Implement sorting
            self.notify(f"Sort by {' '.join(args) if args else 'default'} not implemented yet", severity="warning")
            
        elif cmd_name == "filter":
            if not args:
                # Clear filter
                if self.miller_view and self.miller_view.video_column and self.unfiltered_videos:
                    self.call_later(self.miller_view.set_videos, self.unfiltered_videos)
                    self.current_videos = self.unfiltered_videos.copy()
                    self.notify("Cleared filter", timeout=2)
            else:
                # Apply filter
                filter_text = ' '.join(args).lower()
                if self.miller_view and self.miller_view.video_column and self.current_videos:
                    # Support wildcard matching
                    import fnmatch
                    filtered_videos = []
                    for video in self.current_videos:
                        # Check title, channel, or description
                        searchable = f"{video.title} {video.channel_title or ''} {video.description or ''}".lower()
                        # Support * wildcards
                        if '*' in filter_text or '?' in filter_text:
                            if fnmatch.fnmatch(searchable, f"*{filter_text}*"):
                                filtered_videos.append(video)
                        else:
                            if filter_text in searchable:
                                filtered_videos.append(video)
                    
                    if filtered_videos:
                        self.call_later(self.miller_view.set_videos, filtered_videos)
                        self.current_videos = filtered_videos  # Update current videos to filtered set
                        self.notify(f"Filtered: {len(filtered_videos)} matches", timeout=2)
                    else:
                        self.notify("No matches found", severity="warning")
            
        elif cmd_name == "export":
            # TODO: Implement export
            self.notify("Export not implemented yet", severity="warning")
            
        elif cmd_name == "stats":
            if self.current_playlist and self.current_videos:
                total_duration = sum(v.duration or 0 for v in self.current_videos)
                total_views = sum(v.view_count or 0 for v in self.current_videos)
                avg_duration = total_duration / len(self.current_videos) if self.current_videos else 0
                
                stats_text = f"Playlist Statistics\n"
                stats_text += f"Videos: {len(self.current_videos)}\n"
                stats_text += f"Total Duration: {total_duration // 3600}h {(total_duration % 3600) // 60}m\n"
                stats_text += f"Average Duration: {avg_duration // 60:.1f}m\n"
                stats_text += f"Total Views: {total_views:,}\n"
                
                self.notify(stats_text, timeout=10)
            else:
                self.notify("No playlist selected", severity="warning")
                
        else:
            # Unknown command
            self.notify(f"Unknown command: {cmd_name}", severity="error")
            
    def cancel_command(self) -> None:
        """Handle command cancellation."""
        # Just hide the command input
        if self.command_input:
            self.command_input.hide()