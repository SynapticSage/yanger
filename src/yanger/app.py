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
from .ui.playlist_creation_modal import PlaylistCreationModal, PlaylistCreated
from .ui.rename_modal import RenameModal, ItemRenamed
from .ui.confirmation_modal import ConfirmationModal, ConfirmationResult
from .cache import PersistentCache
from .keybindings import registry
from .config.settings import load_settings
from .operation_history import OperationStack, PasteOperation, CreatePlaylistOperation, RenameOperation, DeleteVideosOperation
from .command_logger import CommandLogger
from .export import PlaylistExporter


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
        Binding("/", "search", "Search"),
        Binding("r", "open_in_browser", "Open in Browser"),
        Binding("u", "undo", "Undo"),
        Binding("U", "redo", "Redo"),
        Binding("ctrl+r", "refresh", "Refresh"),
        Binding("ctrl+shift+r", "refresh_all", "Refresh All"),
        Binding("ctrl+q", "force_quit", "Force Quit", show=False),
    ]
    
    # Reactive attributes
    show_help = reactive(False)
    command_mode = reactive(False)
    
    def __init__(self, 
                 config_dir: Optional[Path] = None,
                 use_cache: bool = True,
                 log_file: Optional[str] = None,
                 log_level: str = "INFO"):
        """Initialize the application.
        
        Args:
            config_dir: Configuration directory path
            use_cache: Whether to use offline cache
            log_file: Optional path to log file for command logging
            log_level: Log level for command logging
        """
        super().__init__()
        
        self.config_dir = config_dir or Path.home() / ".config" / "yanger"
        self.use_cache = use_cache
        
        # Initialize command logger if log file specified
        self.command_logger: Optional[CommandLogger] = None
        if log_file:
            try:
                self.command_logger = CommandLogger(log_file, log_level)
                logger.info(f"Command logging enabled to {log_file}")
            except Exception as e:
                logger.warning(f"Failed to initialize command logger: {e}")
        
        # Core components (initialized in on_mount)
        self.auth: Optional[YouTubeAuth] = None
        self.api_client: Optional[YouTubeAPIClient] = None
        self._clipboard = Clipboard()
        self._operation_stack = OperationStack()
        self._cache = PersistentCache()  # Persistent SQLite cache
        self.offline_mode = False  # Track if running in offline mode
        
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
            
            # Handle offline mode
            if self.offline_mode:
                # Load only virtual playlists in offline mode
                self._append_virtual_playlists()
                if self.miller_view and self.playlists:
                    await self.miller_view.set_playlists(self.playlists)
                if self.status_bar:
                    self.status_bar.update_status(
                        f"Offline Mode - {len(self.playlists)} virtual playlists",
                        "OFFLINE"
                    )
            # Check if we should load playlists on startup
            elif self.settings.cache.load_on_startup:
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
            
            # Filter to only show Watch Later and History by default
            # Unless show_all_virtual_playlists is enabled
            show_all = getattr(self.settings.cache, 'show_all_virtual_playlists', False)
            
            for vp in virtual_playlists:
                # Only show Watch Later and History unless show_all is True
                if not show_all and vp['title'] not in ['Watch Later (Imported)', 'History (Imported)']:
                    continue
                    
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
                shown = len([vp for vp in virtual_playlists 
                           if show_all or vp['title'] in ['Watch Later (Imported)', 'History (Imported)']])
                logger.debug(f"Added {shown} virtual playlists (total: {len(virtual_playlists)})")
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
            self.offline_mode = False
            
        except FileNotFoundError:
            self.notify(
                "OAuth2 credentials not found. Running in offline mode (virtual playlists only).\n"
                "Run 'yanger auth' to setup YouTube access.",
                severity="warning",
                timeout=10
            )
            self.offline_mode = True
            self.api_client = None
        except Exception as e:
            self.notify(
                f"Authentication error: {e}\n"
                "Running in offline mode (virtual playlists only).\n"
                "Run 'yanger auth' to re-authenticate.",
                severity="warning",
                timeout=10
            )
            self.offline_mode = True
            self.api_client = None
    
    async def load_playlists(self, force_refresh: bool = False) -> None:
        """Load user's playlists.
        
        Args:
            force_refresh: Force refresh from API even if cached
        """
        if not self.api_client:
            # In offline mode, just load virtual playlists
            if self.offline_mode:
                self._append_virtual_playlists()
                if self.miller_view:
                    await self.miller_view.set_playlists(self.playlists)
                if self.status_bar:
                    self.status_bar.update_status(
                        f"Offline Mode - {len(self.playlists)} virtual playlists",
                        "OFFLINE"
                    )
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
            
            # Create progress callback for pagination
            def update_progress(page: int, total: int):
                """Update loading progress for paginated playlist fetching."""
                if self.status_bar:
                    self.status_bar.update_status(
                        f"Loading playlists: page {page}, {total} so far...",
                        f"Quota: {self.api_client.get_quota_remaining()}/10000"
                    )
            
            # Load playlists from API (without special playlists to avoid caching them)
            self.playlists = await asyncio.to_thread(
                self.api_client.get_playlists,
                True,  # mine
                None,  # channel_id
                50,    # max_results per page
                False,  # Don't include special playlists from API
                update_progress  # progress callback
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
            # Log error
            if self.command_logger:
                self.command_logger.log_error(str(e), "load_playlists")
    
    async def refresh_playlist_list(self) -> None:
        """Helper method to refresh the playlist list.
        
        This invalidates the cache and forces a fresh fetch from the API.
        Use this after operations that modify the playlist list itself.
        """
        self._cache.invalidate_playlists_cache()
        await self.load_playlists(force_refresh=True)
    
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
                    
                    # Track videos without metadata for auto-fetch
                    videos_without_metadata = []
                    
                    # Convert to Video objects
                    self.current_videos = []
                    for v in videos_data:
                        # Use video ID as fallback if title is empty
                        title = (v.get('title') or '').strip()
                        if not title:
                            videos_without_metadata.append(v['video_id'])
                            title = f"[Video: {v['video_id']}]"
                        
                        channel = (v.get('channel_title') or '').strip()
                        if not channel:
                            channel = 'Unknown Channel'
                        
                        video = Video(
                            id=v['video_id'],
                            playlist_item_id=f"virtual_{v['video_id']}",
                            title=title,
                            channel_title=channel,
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
                    
                    # Auto-fetch metadata if enabled and there are videos without metadata
                    if videos_without_metadata and self.settings.cache.auto_fetch_metadata:
                        # Limit to first batch to avoid high quota usage
                        batch_size = min(len(videos_without_metadata), self.settings.cache.auto_fetch_batch_size)
                        if batch_size > 0:
                            self.notify(
                                f"Auto-fetching metadata for {batch_size} videos (press 'M' to fetch all)...",
                                timeout=3
                            )
                            # Run metadata fetch in background
                            self.call_later(self._auto_fetch_metadata_batch, 
                                          videos_without_metadata[:batch_size], 
                                          virtual_id)
                    
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
            
            # Check if this is a large playlist (>50 videos)
            is_large_playlist = playlist.item_count > 50
            if is_large_playlist:
                logger.info(f"Large playlist detected: {playlist.title} has {playlist.item_count} videos")
                # Show loading state with progress for large playlists
                if self.miller_view:
                    await self.miller_view.show_loading_videos(
                        f"Loading {playlist.item_count} videos..."
                    )
            else:
                # Show loading state only when fetching from API
                if self.miller_view:
                    await self.miller_view.show_loading_videos()
            
            # Create progress callback for pagination
            def update_progress(loaded: int, total: int):
                """Update loading progress for paginated requests."""
                if self.status_bar:
                    self.status_bar.update_status(
                        f"Loading videos: {loaded}/{total}",
                        f"Quota: {self.api_client.get_quota_remaining()}/10000"
                    )
                # Also update the loading message
                if self.miller_view and is_large_playlist:
                    self.call_later(
                        self.miller_view.show_loading_videos,
                        f"Loading videos... {loaded}/{total}"
                    )
            
            # Load videos from API with progress callback for large playlists
            self.current_videos = await asyncio.to_thread(
                self.api_client.get_playlist_items,
                playlist.id,
                50,  # max_results per page
                update_progress if is_large_playlist else None
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
        if self.command_logger:
            self.command_logger.log_action("quit")
        self.exit(0)
    
    def action_force_quit(self) -> None:
        """Force quit without confirmation."""
        self.exit(0)
    
    async def action_refresh(self) -> None:
        """Refresh current view (Ctrl+R)."""
        if self.offline_mode:
            self.notify("Cannot refresh in offline mode. Run 'yanger auth' to re-authenticate.", 
                       severity="warning", timeout=5)
            return
            
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
        if self.offline_mode:
            self.notify("Cannot refresh in offline mode. Run 'yanger auth' to re-authenticate.", 
                       severity="warning", timeout=5)
            return
            
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
        if self.command_logger:
            self.command_logger.log_action("show_help")
        if self.help_overlay:
            self.help_overlay.show()
    
    def action_open_in_browser(self) -> None:
        """Open selected video(s) or playlist in browser."""
        import webbrowser
        
        if not self.miller_view:
            return
        
        # Log the action
        if self.command_logger:
            self.command_logger.log_action("open_in_browser")
        
        urls_to_open = []
        
        # Check if we have marked videos
        if self.miller_view.video_column:
            marked_videos = self.miller_view.video_column.get_marked_videos()
            
            if marked_videos:
                # Open marked videos (limit to 10 to prevent browser overwhelm)
                if len(marked_videos) > 10:
                    self.notify(
                        f"Opening first 10 of {len(marked_videos)} marked videos (browser tab limit)",
                        severity="warning",
                        timeout=3
                    )
                    marked_videos = marked_videos[:10]
                
                for video in marked_videos:
                    urls_to_open.append(f"https://www.youtube.com/watch?v={video.id}")
                
                # Open URLs
                for url in urls_to_open:
                    webbrowser.open(url)
                
                self.notify(f"Opened {len(urls_to_open)} videos in browser", timeout=2)
                return
            
            # No marked videos - open current video
            if (self.miller_view.video_column.selected_index >= 0 and 
                self.miller_view.video_column.selected_index < len(self.miller_view.video_column.videos)):
                
                video = self.miller_view.video_column.videos[self.miller_view.video_column.selected_index]
                url = f"https://www.youtube.com/watch?v={video.id}"
                webbrowser.open(url)
                self.notify(f"Opened video in browser: {video.title}", timeout=2)
                return
        
        # Check if we're in playlist column
        if self.miller_view.playlist_column and self.current_playlist:
            # Check if it's a virtual playlist
            if self.current_playlist.is_virtual:
                self.notify(
                    "Virtual playlists cannot be opened on YouTube (local only)",
                    severity="warning",
                    timeout=3
                )
                return
            
            # Check for special playlists with restrictions
            if self.current_playlist.id in ["WL", "HL"]:
                if self.current_playlist.id == "WL":
                    # Watch Later has a special URL
                    url = "https://www.youtube.com/playlist?list=WL"
                else:
                    self.notify(
                        "History playlist cannot be opened directly on YouTube",
                        severity="warning",
                        timeout=3
                    )
                    return
            else:
                # Regular playlist
                url = f"https://www.youtube.com/playlist?list={self.current_playlist.id}"
            
            webbrowser.open(url)
            self.notify(f"Opened playlist in browser: {self.current_playlist.title}", timeout=2)
            return
        
        # No selection
        self.notify("No video or playlist selected", severity="warning", timeout=2)
    
    def action_undo(self) -> None:
        """Undo the last operation."""
        if not self._operation_stack.can_undo():
            self.notify("Nothing to undo", severity="info", timeout=2)
            return
        
        # Get description of what we're undoing
        description = self._operation_stack.get_undo_description()
        
        # Log the undo action
        if self.command_logger:
            self.command_logger.log_action("undo", {"operation": description})
        
        # Perform undo in background
        self.call_later(self._perform_undo, description)
    
    async def _perform_undo(self, description: str) -> None:
        """Perform the undo operation asynchronously."""
        try:
            operation = await asyncio.to_thread(self._operation_stack.undo)
            if operation:
                # Check if this is a playlist-level operation
                is_playlist_operation = (
                    operation.__class__.__name__ == 'CreatePlaylistOperation' or
                    (operation.__class__.__name__ == 'RenameOperation' and 
                     hasattr(operation, 'item_type') and operation.item_type == 'playlist')
                )
                
                if is_playlist_operation:
                    # For playlist operations, refresh the playlist list
                    await self.refresh_playlist_list()
                else:
                    # For video operations, invalidate affected playlists
                    if hasattr(operation, 'target_playlist_id'):
                        self._cache.invalidate_playlist(operation.target_playlist_id)
                    if hasattr(operation, 'source_playlist_id') and operation.source_playlist_id:
                        self._cache.invalidate_playlist(operation.source_playlist_id)
                    
                    # Refresh current view
                    if self.current_playlist:
                        await self.load_playlist_videos(self.current_playlist)
                
                self.notify(f"Undone: {description}", timeout=2)
                
                # Update status bar
                if self.status_bar:
                    if self._operation_stack.can_redo():
                        self.status_bar.update_status(
                            "Press 'U' to redo",
                            f"{self.api_client.get_quota_remaining()}/10000"
                        )
                    else:
                        self.status_bar.update_status(
                            "",
                            f"{self.api_client.get_quota_remaining()}/10000"
                        )
            else:
                self.notify("Undo failed", severity="error", timeout=2)
        except Exception as e:
            logger.error(f"Error during undo: {e}")
            self.notify(f"Undo failed: {e}", severity="error")
    
    def action_redo(self) -> None:
        """Redo the last undone operation."""
        if not self._operation_stack.can_redo():
            self.notify("Nothing to redo", severity="info", timeout=2)
            return
        
        # Get description of what we're redoing
        description = self._operation_stack.get_redo_description()
        
        # Log the redo action
        if self.command_logger:
            self.command_logger.log_action("redo", {"operation": description})
        
        # Perform redo in background
        self.call_later(self._perform_redo, description)
    
    async def _perform_redo(self, description: str) -> None:
        """Perform the redo operation asynchronously."""
        try:
            operation = await asyncio.to_thread(self._operation_stack.redo)
            if operation:
                # Check if this is a playlist-level operation
                is_playlist_operation = (
                    operation.__class__.__name__ == 'CreatePlaylistOperation' or
                    (operation.__class__.__name__ == 'RenameOperation' and 
                     hasattr(operation, 'item_type') and operation.item_type == 'playlist')
                )
                
                if is_playlist_operation:
                    # For playlist operations, refresh the playlist list
                    await self.refresh_playlist_list()
                else:
                    # For video operations, invalidate affected playlists
                    if hasattr(operation, 'target_playlist_id'):
                        self._cache.invalidate_playlist(operation.target_playlist_id)
                    if hasattr(operation, 'source_playlist_id') and operation.source_playlist_id:
                        self._cache.invalidate_playlist(operation.source_playlist_id)
                    
                    # Refresh current view
                    if self.current_playlist:
                        await self.load_playlist_videos(self.current_playlist)
                
                self.notify(f"Redone: {description}", timeout=2)
                
                # Update status bar
                if self.status_bar:
                    if self._operation_stack.can_undo():
                        self.status_bar.update_status(
                            "Press 'u' to undo",
                            f"{self.api_client.get_quota_remaining()}/10000"
                        )
                    else:
                        self.status_bar.update_status(
                            "",
                            f"{self.api_client.get_quota_remaining()}/10000"
                        )
            else:
                self.notify("Redo failed", severity="error", timeout=2)
        except Exception as e:
            logger.error(f"Error during redo: {e}")
            self.notify(f"Redo failed: {e}", severity="error")
    
    def action_new_playlist(self) -> None:
        """Show modal to create a new playlist."""
        if self.command_logger:
            self.command_logger.log_action("new_playlist_modal")
        self.push_screen(PlaylistCreationModal())
    
    def action_rename(self) -> None:
        """Show modal to rename current playlist or video."""
        if not self.miller_view:
            return
        
        # Log the rename action
        if self.command_logger:
            self.command_logger.log_action("rename_modal")
        
        # Determine what to rename based on current focus
        if self.miller_view.video_column and self.miller_view.video_column.has_focus:
            # Rename video
            if (self.miller_view.video_column.selected_index >= 0 and 
                self.miller_view.video_column.selected_index < len(self.miller_view.video_column.videos)):
                video = self.miller_view.video_column.videos[self.miller_view.video_column.selected_index]
                self.push_screen(RenameModal("video", video.id, video.title))
        elif self.current_playlist:
            # Rename playlist
            # Check if it's a virtual playlist (can't rename those)
            if self.current_playlist.is_virtual:
                self.notify(
                    "Cannot rename virtual playlists (imported from Takeout)",
                    severity="warning",
                    timeout=3
                )
                return
            
            # Check for special playlists that shouldn't be renamed
            if self.current_playlist.id in ['WL', 'HL', 'LL']:
                self.notify(
                    "Cannot rename system playlists (Watch Later, History, Liked)",
                    severity="warning",
                    timeout=3
                )
                return
            
            self.push_screen(RenameModal("playlist", self.current_playlist.id, self.current_playlist.title))
    
    def action_search(self) -> None:
        """Start search in the current context."""
        if not self.miller_view:
            self.notify("Search not available - UI not initialized", severity="warning")
            return
        if not self.miller_view.search_input:
            self.notify("Search not available - search input not initialized", severity="warning")
            return
        
        # Determine context and placeholder based on focused column
        if self.miller_view.focused_column == 0:
            context = "playlist"
            placeholder = "Search playlists..."
        else:
            context = "video"
            placeholder = "Search videos..."
        
        # Log the search action
        if self.command_logger:
            self.command_logger.log_action("search_start", {"context": context})
        
        # Show the search input with appropriate placeholder
        self.miller_view.search_active = True
        self.miller_view.search_input.show(placeholder)
    
    def action_command_mode(self) -> None:
        """Enter command mode."""
        if self.command_input:
            self.command_input.show(":")
    
    
    async def on_key(self, event: events.Key) -> None:
        """Handle global key events."""
        
        # IMPORTANT: Let Input widgets handle their own keys when focused
        # Check if command input has focus
        if (self.command_input and 
            hasattr(self.command_input, 'input_widget') and 
            self.command_input.input_widget and 
            self.command_input.input_widget.has_focus):
            return  # Let the command input handle it
        
        # Check if search input has focus
        if (self.miller_view and 
            self.miller_view.search_input and 
            hasattr(self.miller_view.search_input, 'input_field') and
            self.miller_view.search_input.input_field and 
            self.miller_view.search_input.input_field.has_focus):
            # Only handle ESC to cancel search
            if event.key == "escape":
                self.miller_view.search_input.hide()
                self.miller_view.search_active = False
                if self.miller_view.video_column:
                    self.miller_view.video_column.clear_search()
                if self.miller_view.playlist_column:
                    self.miller_view.playlist_column.clear_search()
                event.stop()
            return  # Let the search input handle other keys
        
        # Log the key press if logger is enabled
        if self.command_logger:
            # Determine current context
            context = "global"
            if self.miller_view:
                if self.miller_view.video_column and self.miller_view.video_column.has_focus:
                    context = "video_list"
                elif self.miller_view.playlist_column and self.miller_view.playlist_column.has_focus:
                    context = "playlist_list"
                elif self.miller_view.preview_pane and self.miller_view.preview_pane.has_focus:
                    context = "preview"
            
            # Log the key with modifiers
            modifiers = {
                "ctrl": event.ctrl,
                "shift": event.shift,
                "meta": event.meta
            } if hasattr(event, 'ctrl') else None
            
            self.command_logger.log_key(event.key, context, modifiers)
        
        # FIRST: Check for pending sort selection
        if hasattr(self, '_pending_sort') and self._pending_sort:
            if event.key in ['t', 'd', 'p', 'v', 'D', 'escape']:
                await self.handle_sort_key(event.key)
            self._pending_sort = False
            event.stop()
        # SECOND: Check for pending double-key ranger commands
        elif hasattr(self, '_pending_command') and self._pending_command:
            if self._pending_command == 'd' and event.key == 'D':
                # dD - delete videos
                await self.handle_delete_videos()
            elif self._pending_command == event.key:  # Double key pressed (dd, yy, pp)
                await self.execute_ranger_command(self._pending_command)
            else:
                # Cancel pending command if different key pressed
                if self.status_bar:
                    self.status_bar.update_status("", "")
            self._pending_command = None
            event.stop()
        # Check for single 'g' - wait for second key for 'gn' (new playlist) or 'gd' (delete playlist)
        elif event.key == 'g' and not getattr(self, '_pending_g', False):
            self._pending_g = True
            if self.status_bar:
                self.status_bar.update_status("Press 'n' for new playlist, 'd' to delete playlist", "")
            event.stop()
        # Check for 'gn' or 'gd' commands
        elif hasattr(self, '_pending_g') and self._pending_g:
            if event.key == 'n':
                self.action_new_playlist()
            elif event.key == 'd':
                await self.handle_delete_playlist()
            elif event.key == 'g':
                # Double 'g' - pass to miller view for go to top
                if self.miller_view:
                    await self.miller_view.handle_key('g')
            else:
                # Cancel 'g' command if different key pressed
                if self.status_bar:
                    self.status_bar.update_status("", "")
            self._pending_g = False
            event.stop()
        # Check for single 'c' - wait for second key for 'cw' (rename)
        elif event.key == 'c' and not getattr(self, '_pending_c', False):
            self._pending_c = True
            if self.status_bar:
                self.status_bar.update_status("Press 'w' to rename", "")
            event.stop()
        # Check for 'cw' - rename
        elif hasattr(self, '_pending_c') and self._pending_c:
            if event.key == 'w':
                self.action_rename()
            else:
                # Cancel 'c' command if different key pressed
                if self.status_bar:
                    self.status_bar.update_status("", "")
            self._pending_c = False
            event.stop()
        # THEN: Let miller view handle navigation keys, ranger commands, search, and visual mode
        # V = visual mode, v = invert selection, space = toggle mark (no auto-advance)
        # pageup/pagedown for pagination
        # Note: 'u' is now handled at app level for undo, not passed to miller_view
        # Note: 'g' and 'c' are now intercepted for special commands
        elif self.miller_view and event.key in ['h', 'j', 'k', 'l', 'G', 'enter', 'space', 'd', 'y', 'p', 'n', 'N', 'v', 'V', 'escape', 'o', 'pageup', 'pagedown']:
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
        # Log playlist navigation
        if self.command_logger:
            prev_playlist = self.current_playlist.title if self.current_playlist else "None"
            self.command_logger.log_navigation(prev_playlist, playlist.title, "select_playlist")
        
        await self.load_playlist_videos(playlist)
    
    async def handle_video_selection(self, video: Video) -> None:
        """Handle video selection."""
        self.current_video = video
        if self.miller_view:
            await self.miller_view.update_preview(video)
    
    async def fetch_metadata_for_current_playlist(self) -> None:
        """Fetch metadata for videos in current virtual playlist."""
        if not self.current_playlist or not self.current_playlist.is_virtual:
            return
        
        # Get virtual playlist ID
        virtual_id = self.current_playlist.id.replace("virtual_", "")
        
        # Get videos without metadata (limit to current page for performance)
        video_ids = self._cache.get_virtual_videos_without_metadata(
            playlist_id=virtual_id,
            limit=100  # Fetch metadata for up to 100 videos at a time
        )
        
        if not video_ids:
            self.notify("All videos already have metadata!", severity="success")
            return
        
        # Calculate quota cost
        num_batches = (len(video_ids) + 49) // 50  # 50 videos per batch
        quota_cost = num_batches
        
        # Show confirmation
        self.notify(
            f"Fetching metadata for {len(video_ids)} videos ({quota_cost} quota units)...",
            timeout=5
        )
        
        try:
            if not self.api_client:
                self.notify("API client not initialized. Please restart the app.", severity="error")
                return
            
            # Fetch metadata
            updated_count = 0
            videos_data = self.api_client.get_videos_by_ids(video_ids)
            
            # Update database
            for video_data in videos_data:
                if self._cache.update_virtual_video_metadata(video_data['video_id'], video_data):
                    updated_count += 1
            
            # Reload current playlist to show updated titles
            await self.load_playlist_videos(self.current_playlist, force_refresh=False)
            
            self.notify(
                f"Successfully updated {updated_count} videos! Quota remaining: {self.api_client.get_quota_remaining()}/10000",
                severity="success",
                timeout=5
            )
            
        except Exception as e:
            self.notify(f"Error fetching metadata: {e}", severity="error")
            logger.error(f"Error fetching metadata: {e}")
    
    async def _auto_fetch_metadata_batch(self, video_ids: List[str], virtual_id: str) -> None:
        """Background task to fetch metadata for a batch of videos.
        
        Args:
            video_ids: List of video IDs to fetch metadata for
            virtual_id: Virtual playlist ID
        """
        try:
            if not self.api_client:
                return
            
            # Fetch metadata
            updated_count = 0
            videos_data = self.api_client.get_videos_by_ids(video_ids)
            
            # Update database
            for video_data in videos_data:
                if self._cache.update_virtual_video_metadata(video_data['video_id'], video_data):
                    updated_count += 1
            
            # Reload current playlist to show updated titles if still viewing same playlist
            if (self.current_playlist and 
                self.current_playlist.id == f"virtual_{virtual_id}"):
                await self.load_playlist_videos(self.current_playlist, force_refresh=False)
                
                self.notify(
                    f"Auto-fetched metadata for {updated_count} videos",
                    severity="success",
                    timeout=2
                )
            
        except Exception as e:
            logger.error(f"Error auto-fetching metadata: {e}")
            # Don't notify on auto-fetch errors to avoid annoying the user
    
    def on_ranger_command(self, message: RangerCommand) -> None:
        """Handle ranger-style command initiation."""
        self._pending_command = message.command
        # Show hint in status bar
        if self.status_bar:
            hints = {
                'd': "Press 'd' to cut or 'D' to delete",
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
    
    def on_playlist_created(self, message: PlaylistCreated) -> None:
        """Handle playlist creation from modal."""
        if not self.api_client:
            return
        
        # Create the playlist in background
        self.call_later(
            self.create_playlist,
            message.title,
            message.description,
            message.privacy
        )
    
    async def create_playlist(self, title: str, description: str, privacy: str) -> None:
        """Create a new playlist via API."""
        try:
            # Create operation for undo support
            create_op = CreatePlaylistOperation(
                api_client=self.api_client,
                title=title,
                description=description,
                privacy_status=privacy
            )
            
            # Execute through operation stack
            success = await asyncio.to_thread(self._operation_stack.execute, create_op)
            
            if success:
                self.notify(f"Created playlist: {title}", timeout=2)
                
                # Log playlist creation
                if self.command_logger:
                    self.command_logger.log_operation(
                        "create_playlist",
                        success=True,
                        details={"title": title, "privacy": privacy}
                    )
                
                # Refresh playlist list to show the new one
                await self.refresh_playlist_list()
                
                # Update status bar
                if self.status_bar:
                    self.status_bar.update_status(
                        "Press 'u' to undo",
                        f"{self.api_client.get_quota_remaining()}/10000"
                    )
            else:
                self.notify("Failed to create playlist", severity="error")
                
        except Exception as e:
            logger.error(f"Error creating playlist: {e}")
            self.notify(f"Error: {e}", severity="error")
    
    def on_item_renamed(self, message: ItemRenamed) -> None:
        """Handle rename from modal."""
        if not self.api_client:
            return
        
        # Rename the item in background
        self.call_later(
            self.rename_item,
            message.item_type,
            message.item_id,
            message.old_name,
            message.new_name
        )
    
    async def rename_item(self, item_type: str, item_id: str, 
                          old_name: str, new_name: str) -> None:
        """Rename a playlist or video via API."""
        try:
            # Create operation for undo support
            playlist_id = self.current_playlist.id if item_type == "video" else None
            rename_op = RenameOperation(
                api_client=self.api_client,
                item_type=item_type,
                item_id=item_id,
                old_title=old_name,
                new_title=new_name,
                playlist_id=playlist_id
            )
            
            # Execute through operation stack
            success = await asyncio.to_thread(self._operation_stack.execute, rename_op)
            
            if success:
                self.notify(f"Renamed {item_type}: {new_name}", timeout=2)
                
                # Log rename operation
                if self.command_logger:
                    self.command_logger.log_operation(
                        f"rename_{item_type}",
                        success=True,
                        details={"old_name": old_name, "new_name": new_name}
                    )
                
                # Refresh current view to show the new name
                if item_type == "playlist":
                    # Refresh playlist list to show the new name
                    await self.refresh_playlist_list()
                else:
                    # Refresh videos if renamed a video
                    if self.current_playlist:
                        self._cache.invalidate_playlist(self.current_playlist.id)
                        await self.load_playlist_videos(self.current_playlist)
                
                # Update status bar
                if self.status_bar:
                    self.status_bar.update_status(
                        "Press 'u' to undo",
                        f"{self.api_client.get_quota_remaining()}/10000"
                    )
            else:
                self.notify(f"Failed to rename {item_type}", severity="error")
                
        except Exception as e:
            logger.error(f"Error renaming {item_type}: {e}")
            self.notify(f"Error: {e}", severity="error")
    
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
        
        # Log the ranger command
        if self.command_logger:
            self.command_logger.log_action(f"ranger_command_{command}", {"command": command})
        
        if command == 'd':  # Cut
            marked_videos = video_column.get_marked_videos()
            if marked_videos:
                # Cut marked videos
                self._clipboard.cut(marked_videos, self.current_playlist.id)
                msg = f"Cut {len(marked_videos)} videos"
                # Log clipboard operation
                if self.command_logger:
                    self.command_logger.log_clipboard("cut", len(marked_videos), 
                                                     source=self.current_playlist.title)
            elif 0 <= video_column.selected_index < len(video_column.videos):
                # Cut current video
                video = video_column.videos[video_column.selected_index]
                self._clipboard.cut([video], self.current_playlist.id)
                msg = f"Cut: {video.title}"
                # Log clipboard operation
                if self.command_logger:
                    self.command_logger.log_clipboard("cut", 1, source=self.current_playlist.title)
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
                # Log clipboard operation
                if self.command_logger:
                    self.command_logger.log_clipboard("copy", len(marked_videos), 
                                                     source=self.current_playlist.title)
            elif 0 <= video_column.selected_index < len(video_column.videos):
                # Copy current video
                video = video_column.videos[video_column.selected_index]
                self._clipboard.copy([video], self.current_playlist.id)
                msg = f"Copied: {video.title}"
                # Log clipboard operation
                if self.command_logger:
                    self.command_logger.log_clipboard("copy", 1, source=self.current_playlist.title)
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
            operation_type = self._clipboard.get_operation_type()
            videos = [item.video for item in self._clipboard.items]
            source_playlist_id = self._clipboard.items[0].source_playlist_id if self._clipboard.items else None
            
            # Create paste operation
            paste_op = PasteOperation(
                api_client=self.api_client,
                videos=videos,
                target_playlist_id=self.current_playlist.id,
                source_playlist_id=source_playlist_id,
                is_cut=(operation_type == "cut")
            )
            
            # Execute operation through the stack (enables undo)
            success = await asyncio.to_thread(self._operation_stack.execute, paste_op)
            
            if success:
                pasted_count = len(videos)
                
                # Log paste operation
                if self.command_logger:
                    self.command_logger.log_clipboard(
                        "paste", 
                        pasted_count,
                        source=source_playlist_id,
                        target=self.current_playlist.title
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
                if source_playlist_id and source_playlist_id != self.current_playlist.id:
                    self._cache.invalidate_playlist(source_playlist_id)
                
                # Refresh current playlist
                await self.load_playlist_videos(self.current_playlist)
                
                self.notify(f"Pasted {pasted_count} videos", timeout=2)
                
                # Update status bar to show undo is available
                if self.status_bar:
                    self.status_bar.update_status(
                        "Press 'u' to undo",
                        f"{self.api_client.get_quota_remaining()}/10000"
                    )
            else:
                self.notify("Paste operation failed", severity="error")
                
        except Exception as e:
            logger.error(f"Error pasting videos: {e}")
            self.notify(f"Paste failed: {e}", severity="error")
    
    async def handle_export_command(self, args: List[str]) -> None:
        """Handle the export command.
        
        Args:
            args: Command arguments
        """
        try:
            # Determine output path and format
            if not args:
                # Default: export current playlist to JSON
                if not self.current_playlist:
                    self.notify("No playlist selected", severity="warning")
                    return
                
                # Clean filename from playlist title
                import re
                safe_title = re.sub(r'[^\w\s-]', '', self.current_playlist.title)
                safe_title = re.sub(r'[-\s]+', '-', safe_title)
                output_path = Path(f"{safe_title}.json")
                format = "json"
                export_all = False
                
            elif args[0] == "all":
                # Export all playlists
                output_path = Path("youtube_playlists_export.json")
                format = "json"
                export_all = True
                if len(args) > 1:
                    output_path = Path(args[1])
                    # Detect format from extension
                    if output_path.suffix == ".yaml" or output_path.suffix == ".yml":
                        format = "yaml"
                    elif output_path.suffix == ".csv":
                        format = "csv"
                        
            else:
                # Export current playlist to specified file
                if not self.current_playlist:
                    self.notify("No playlist selected", severity="warning")
                    return
                    
                output_path = Path(args[0])
                export_all = False
                
                # Detect format from extension
                if output_path.suffix == ".yaml" or output_path.suffix == ".yml":
                    format = "yaml"
                elif output_path.suffix == ".csv":
                    format = "csv"
                else:
                    format = "json"
            
            # Create exporter
            exporter = PlaylistExporter(
                api_client=self.api_client,
                cache=self._cache
            )
            
            # Perform export
            if export_all:
                # Export all playlists
                stats = await asyncio.to_thread(
                    exporter.export_all,
                    output_path,
                    format=format,
                    include_virtual=True,
                    include_real=True
                )
                
                self.notify(
                    f"Exported {stats['real_playlist_count']} real and "
                    f"{stats['virtual_playlist_count']} virtual playlists to {output_path}",
                    timeout=5
                )
                
            else:
                # Export single playlist with current videos
                if not self.current_videos:
                    self.notify("No videos to export", severity="warning")
                    return
                
                # Build export data
                export_data = {
                    "export_date": datetime.now().isoformat(),
                    "playlist": {
                        "id": self.current_playlist.id,
                        "title": self.current_playlist.title,
                        "description": self.current_playlist.description,
                        "channel": self.current_playlist.channel_title,
                        "privacy": self.current_playlist.privacy_status.value if self.current_playlist.privacy_status else "private",
                        "video_count": len(self.current_videos),
                        "is_virtual": self.current_playlist.is_virtual
                    },
                    "videos": []
                }
                
                # Add video data
                for i, video in enumerate(self.current_videos):
                    video_data = {
                        "position": i + 1,
                        "video_id": video.id,
                        "title": video.title,
                        "channel": video.channel_title,
                        "description": video.description[:500] if video.description else "",  # Truncate long descriptions
                        "duration": video.duration,
                        "view_count": video.view_count,
                        "like_count": video.like_count,
                        "published_at": video.published_at.isoformat() if video.published_at else None,
                        "added_at": video.added_at.isoformat() if video.added_at else None,
                        "url": f"https://www.youtube.com/watch?v={video.id}"
                    }
                    export_data["videos"].append(video_data)
                
                # Write to file based on format
                import json
                import yaml
                
                if format == "json":
                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(export_data, f, indent=2, ensure_ascii=False)
                        
                elif format == "yaml":
                    with open(output_path, 'w', encoding='utf-8') as f:
                        yaml.dump(export_data, f, default_flow_style=False, allow_unicode=True)
                        
                elif format == "csv":
                    import csv
                    with open(output_path, 'w', newline='', encoding='utf-8') as f:
                        if export_data["videos"]:
                            fieldnames = ["position", "video_id", "title", "channel", "url", "duration", "view_count"]
                            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                            writer.writeheader()
                            writer.writerows(export_data["videos"])
                
                self.notify(
                    f"Exported {len(self.current_videos)} videos from '{self.current_playlist.title}' to {output_path}",
                    timeout=5
                )
                
            # Log the export
            if self.command_logger:
                self.command_logger.log_operation(
                    "export",
                    success=True,
                    details={
                        "output": str(output_path),
                        "format": format,
                        "export_all": export_all
                    }
                )
                
        except Exception as e:
            logger.error(f"Export failed: {e}")
            self.notify(f"Export failed: {e}", severity="error")
    
    def execute_command(self, command: str) -> None:
        """Execute a command entered in command mode.
        
        Args:
            command: Command string starting with ':'
        """
        # Parse command
        cmd_name, args = parse_command(command)
        
        if not cmd_name:
            return
        
        # Log the command execution
        if self.command_logger:
            self.command_logger.log_command(cmd_name, " ".join(args) if args else None)
            
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
            # Sort videos in current playlist
            if not args:
                self.notify("Usage: :sort <field> [asc|desc]", severity="warning")
                self.notify("Fields: title, date, views, duration, position", severity="info")
                return
            
            field = args[0].lower()
            reverse = False
            if len(args) > 1:
                order = args[1].lower()
                reverse = (order == "desc")
            
            self.call_later(self.sort_videos, field, reverse)
            
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
            
        elif cmd_name == "fetch-metadata":
            # Fetch metadata for current virtual playlist
            if self.current_playlist and self.current_playlist.is_virtual:
                self.call_later(self.fetch_metadata_for_current_playlist)
            else:
                self.notify("This command only works for virtual playlists", severity="warning")
            
        elif cmd_name == "export":
            # Export current playlist or all playlists
            self.call_later(self.handle_export_command, args)
            
        elif cmd_name == "delete":
            # Delete videos or playlist
            if not args or args[0] == "videos":
                # Delete selected/marked videos
                self.call_later(self.handle_delete_videos)
            elif args[0] == "playlist":
                # Delete current playlist  
                self.call_later(self.handle_delete_playlist)
            else:
                self.notify("Usage: :delete [videos|playlist]", severity="warning")
        
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
    
    async def handle_delete_videos(self) -> None:
        """Handle dD command to delete videos from playlist."""
        if not self.miller_view or not self.miller_view.video_column:
            self.notify("No videos to delete", severity="warning")
            return
        
        video_column = self.miller_view.video_column
        
        # Get videos to delete (marked or current)
        marked_videos = video_column.get_marked_videos()
        if marked_videos:
            videos_to_delete = marked_videos
            message = f"Delete {len(marked_videos)} marked videos?"
            details = "This will permanently remove them from the playlist."
        elif 0 <= video_column.selected_index < len(video_column.videos):
            videos_to_delete = [video_column.videos[video_column.selected_index]]
            message = f"Delete '{videos_to_delete[0].title}'?"
            details = "This will permanently remove it from the playlist."
        else:
            self.notify("No video selected", severity="warning")
            return
        
        # Show confirmation dialog
        modal = ConfirmationModal(
            title="Confirm Delete",
            message=message,
            details=details,
            confirm_text="Delete",
            cancel_text="Cancel",
            action="delete_videos",
            dangerous=True
        )
        
        # Store the videos for deletion after confirmation
        self._pending_delete_videos = videos_to_delete
        await self.push_screen(modal)
    
    async def handle_delete_playlist(self) -> None:
        """Handle gd command to delete a playlist."""
        if not self.current_playlist:
            self.notify("No playlist selected", severity="warning")
            return
        
        # Check if it's a special playlist that can't be deleted
        if self.current_playlist.is_special:
            self.notify(f"Cannot delete special playlist '{self.current_playlist.title}'", severity="error")
            return
        
        # Get video count for warning
        video_count = len(self.current_videos) if self.current_videos else 0
        
        message = f"Delete playlist '{self.current_playlist.title}'?"
        if video_count > 0:
            details = f"WARNING: This playlist contains {video_count} videos. Deletion cannot be undone!"
        else:
            details = "This action cannot be undone."
        
        # Show confirmation dialog
        modal = ConfirmationModal(
            title="Confirm Playlist Deletion",
            message=message,
            details=details,
            confirm_text="Delete Playlist",
            cancel_text="Cancel",
            action="delete_playlist",
            dangerous=True
        )
        
        await self.push_screen(modal)
    
    def on_confirmation_result(self, message: ConfirmationResult) -> None:
        """Handle confirmation dialog result."""
        if not message.confirmed:
            self.notify("Cancelled", timeout=1)
            return
        
        if message.action == "delete_videos":
            # Execute video deletion
            if hasattr(self, '_pending_delete_videos'):
                self.call_later(self.execute_delete_videos, self._pending_delete_videos)
                delattr(self, '_pending_delete_videos')
        elif message.action == "delete_playlist":
            # Execute playlist deletion
            self.call_later(self.execute_delete_playlist)
    
    async def execute_delete_videos(self, videos: List[Video]) -> None:
        """Execute the actual video deletion."""
        try:
            if not self.current_playlist:
                return
            
            # Create delete operation for undo support
            delete_op = DeleteVideosOperation(
                api_client=self.api_client,
                playlist_id=self.current_playlist.id,
                videos=videos
            )
            
            # Execute through operation stack
            success = await asyncio.to_thread(self._operation_stack.execute, delete_op)
            
            if success:
                # Remove videos from UI
                if self.miller_view and self.miller_view.video_column:
                    remaining_videos = [v for v in self.current_videos if v not in videos]
                    self.current_videos = remaining_videos
                    await self.miller_view.set_videos(remaining_videos)
                    
                    # Clear marks if any
                    self.miller_view.video_column.clear_marks()
                
                video_word = "video" if len(videos) == 1 else "videos"
                self.notify(f"Deleted {len(videos)} {video_word}", timeout=2)
                
                # Log the deletion
                if self.command_logger:
                    self.command_logger.log_operation(
                        "delete_videos",
                        success=True,
                        details={"count": len(videos), "playlist": self.current_playlist.title}
                    )
                
                # Update status bar
                if self.status_bar:
                    self.status_bar.update_status(
                        "",
                        f"{self.api_client.get_quota_remaining()}/10000"
                    )
            else:
                self.notify("Delete operation failed", severity="error")
                
        except Exception as e:
            logger.error(f"Error deleting videos: {e}")
            self.notify(f"Delete failed: {e}", severity="error")
    
    async def sort_videos(self, field: str, reverse: bool = False) -> None:
        """Sort videos in the current playlist.
        
        Args:
            field: Field to sort by (title, date, views, duration, position)
            reverse: Whether to sort in descending order
        """
        if not self.miller_view or not self.miller_view.video_column:
            self.notify("No videos to sort", severity="warning")
            return
        
        videos = self.current_videos
        if not videos:
            self.notify("No videos to sort", severity="warning")
            return
        
        try:
            # Sort based on field
            if field == "title":
                sorted_videos = sorted(videos, key=lambda v: v.title.lower(), reverse=reverse)
                sort_desc = f"title ({'desc' if reverse else 'asc'})"
            elif field == "date":
                sorted_videos = sorted(videos, 
                                     key=lambda v: v.added_at or datetime.min, 
                                     reverse=not reverse)  # Most recent first by default
                sort_desc = f"date added ({'oldest first' if reverse else 'newest first'})"
            elif field == "views":
                sorted_videos = sorted(videos, 
                                     key=lambda v: v.view_count or 0, 
                                     reverse=not reverse)  # Most views first by default
                sort_desc = f"views ({'least first' if reverse else 'most first'})"
            elif field == "duration":
                # Parse ISO 8601 duration for sorting
                def parse_duration(duration_str):
                    if not duration_str:
                        return 0
                    # Simple parser for PT#M#S format
                    import re
                    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
                    if match:
                        hours = int(match.group(1) or 0)
                        minutes = int(match.group(2) or 0)  
                        seconds = int(match.group(3) or 0)
                        return hours * 3600 + minutes * 60 + seconds
                    return 0
                
                sorted_videos = sorted(videos, 
                                     key=lambda v: parse_duration(v.duration), 
                                     reverse=not reverse)  # Longest first by default
                sort_desc = f"duration ({'shortest first' if reverse else 'longest first'})"
            elif field == "position":
                sorted_videos = sorted(videos, key=lambda v: v.position, reverse=reverse)
                sort_desc = f"position ({'reverse' if reverse else 'original'})"
            else:
                self.notify(f"Unknown sort field: {field}", severity="error")
                self.notify("Valid fields: title, date, views, duration, position", severity="info")
                return
            
            # Update the video column with sorted videos
            self.current_videos = sorted_videos
            await self.miller_view.set_videos(sorted_videos)
            
            self.notify(f"Sorted by {sort_desc}", timeout=2)
            
            # Log the sort operation
            if self.command_logger:
                self.command_logger.log_operation(
                    "sort_videos",
                    success=True,
                    details={"field": field, "reverse": reverse, "count": len(sorted_videos)}
                )
                
        except Exception as e:
            logger.error(f"Error sorting videos: {e}")
            self.notify(f"Sort failed: {e}", severity="error")
    
    async def execute_delete_playlist(self) -> None:
        """Execute the actual playlist deletion."""
        try:
            if not self.current_playlist:
                return
            
            playlist_to_delete = self.current_playlist
            playlist_title = playlist_to_delete.title
            
            # Delete the playlist
            if playlist_to_delete.is_virtual:
                # Delete virtual playlist from cache
                self._cache.delete_virtual_playlist(playlist_to_delete.id)
                logger.info(f"Deleted virtual playlist: {playlist_title}")
            else:
                # Delete real playlist via API
                await asyncio.to_thread(
                    self.api_client.delete_playlist,
                    playlist_to_delete.id
                )
                logger.info(f"Deleted playlist: {playlist_title}")
            
            self.notify(f"Deleted playlist: {playlist_title}", timeout=3)
            
            # Log the deletion
            if self.command_logger:
                self.command_logger.log_operation(
                    "delete_playlist",
                    success=True,
                    details={"playlist": playlist_title, "virtual": playlist_to_delete.is_virtual}
                )
            
            # Refresh playlist list and navigate to first available playlist
            await self.refresh_playlist_list()
            
            # Select first playlist if available
            if self.miller_view and self.miller_view.playlist_column:
                if self.miller_view.playlist_column.playlists:
                    self.miller_view.playlist_column.selected_index = 0
                    first_playlist = self.miller_view.playlist_column.playlists[0]
                    await self.handle_playlist_selection(first_playlist)
                else:
                    # No playlists left
                    self.current_playlist = None
                    self.current_videos = []
                    if self.miller_view:
                        await self.miller_view.set_videos([])
                        
        except Exception as e:
            logger.error(f"Error deleting playlist: {e}")
            self.notify(f"Delete failed: {e}", severity="error")
