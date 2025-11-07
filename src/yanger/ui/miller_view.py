"""Miller column view implementation for YouTube Ranger.

Three-column layout inspired by macOS Finder's column view.
"""
# Created: 2025-08-03

from typing import List, Optional
import asyncio
import re

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Container
from textual.widgets import Static, ListView, ListItem, Label, LoadingIndicator
from textual.reactive import reactive
from textual.widget import Widget
from textual import events

from ..models import Playlist, Video
from .search_input import SearchInput, SearchHighlighter


class PlaylistColumn(ScrollableContainer):
    """Left column showing playlists."""
    
    DEFAULT_CSS = """
    PlaylistColumn {
        width: 1fr;
        height: 100%;
        border-right: solid $accent;
        padding: 0 1;
    }
    
    PlaylistColumn > .playlist-item {
        width: 100%;
        height: 1;
        padding: 0 1;
    }
    
    PlaylistColumn > .playlist-item.selected {
        background: $primary;
        color: $text;
    }
    
    PlaylistColumn > .playlist-item.focused {
        background: $accent;
        text-style: bold;
    }
    
    PlaylistColumn > .playlist-item.search-match {
        background: $warning-darken-2;
    }
    
    PlaylistColumn > .playlist-item.selected.search-match {
        background: $warning;
    }
    
    PlaylistColumn > .loading {
        width: 100%;
        height: 100%;
        content-align: center middle;
    }
    """
    
    selected_index = reactive(0)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlists: List[Playlist] = []
        self.can_focus = True
        self.search_query = ""
        self.search_matches: List[int] = []
        self.current_match_index = -1
        
    def compose(self) -> ComposeResult:
        """Initial composition."""
        yield Static("Loading playlists...", classes="loading")
        
    async def set_playlists(self, playlists: List[Playlist]) -> None:
        """Set the playlists to display."""
        self.playlists = playlists
        await self.refresh_display()
        
    async def refresh_display(self) -> None:
        """Refresh the playlist display."""
        # Clear existing content
        await self.remove_children()
        
        # Add playlist items
        for i, playlist in enumerate(self.playlists):
            classes = ["playlist-item"]
            if i == self.selected_index:
                classes.append("selected")
            if i in self.search_matches:
                classes.append("search-match")
                
            item = Static(
                f"{playlist.title} ({playlist.item_count})",
                classes=" ".join(classes)
            )
            item.playlist = playlist  # Attach playlist data
            await self.mount(item)
            
    def watch_selected_index(self, old_value: int, new_value: int) -> None:
        """React to selection changes."""
        # Update visual selection
        items = self.query(".playlist-item")
        for i, item in enumerate(items):
            if i == old_value:
                item.remove_class("selected")
            if i == new_value:
                item.add_class("selected")
                
        # Notify parent
        if 0 <= new_value < len(self.playlists):
            self.post_message(
                PlaylistSelected(self.playlists[new_value])
            )
    
    def move_selection(self, delta: int) -> None:
        """Move selection up or down."""
        if not self.playlists:
            return
            
        new_index = self.selected_index + delta
        new_index = max(0, min(new_index, len(self.playlists) - 1))
        self.selected_index = new_index
        
        # Scroll to show selected item
        self.scroll_to_widget(self.query(".playlist-item")[new_index])
        
    def select_first(self) -> None:
        """Select first playlist (gg)."""
        self.selected_index = 0
        self.scroll_home()
        
    def select_last(self) -> None:
        """Select last playlist (G)."""
        if self.playlists:
            self.selected_index = len(self.playlists) - 1
            self.scroll_end()
    
    def search(self, query: str) -> int:
        """Search for playlists matching query.
        
        Args:
            query: Search query
            
        Returns:
            Number of matches found
        """
        import re
        
        self.search_query = query
        self.search_matches = []
        self.current_match_index = -1
        
        if not query:
            asyncio.create_task(self.refresh_display())
            return 0
            
        # Case-insensitive search in title
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        
        for i, playlist in enumerate(self.playlists):
            if pattern.search(playlist.title):
                self.search_matches.append(i)
                
        # Jump to first match
        if self.search_matches:
            self.current_match_index = 0
            self.selected_index = self.search_matches[0]
            self.scroll_to_widget(self.query(".playlist-item")[self.search_matches[0]])
            
        asyncio.create_task(self.refresh_display())
        return len(self.search_matches)
        
    def next_search_match(self) -> bool:
        """Jump to next search match.
        
        Returns:
            True if there was a next match, False otherwise
        """
        if not self.search_matches:
            return False
            
        self.current_match_index = (self.current_match_index + 1) % len(self.search_matches)
        self.selected_index = self.search_matches[self.current_match_index]
        self.scroll_to_widget(self.query(".playlist-item")[self.selected_index])
        return True
        
    def previous_search_match(self) -> bool:
        """Jump to previous search match.
        
        Returns:
            True if there was a previous match, False otherwise
        """
        if not self.search_matches:
            return False
            
        self.current_match_index = (self.current_match_index - 1) % len(self.search_matches)
        self.selected_index = self.search_matches[self.current_match_index]
        self.scroll_to_widget(self.query(".playlist-item")[self.selected_index])
        return True
        
    def clear_search(self) -> None:
        """Clear search highlighting."""
        self.search_query = ""
        self.search_matches = []
        self.current_match_index = -1
        asyncio.create_task(self.refresh_display())


class VideoColumn(ScrollableContainer):
    """Middle column showing videos in selected playlist."""
    
    DEFAULT_CSS = """
    VideoColumn {
        width: 1fr;
        height: 100%;
        border-right: solid $accent;
        padding: 0 1;
    }
    
    VideoColumn > .video-item {
        width: 100%;
        height: 1;
        padding: 0 1;
    }
    
    VideoColumn > .video-item.selected {
        background: $primary;
        color: $text;
    }
    
    VideoColumn > .video-item.marked {
        text-style: bold;
    }
    
    VideoColumn > .video-item.focused {
        background: $accent;
        text-style: bold;
    }
    
    VideoColumn > .video-item.search-match {
        background: $warning-darken-2;
    }
    
    VideoColumn > .video-item.selected.search-match {
        background: $warning;
    }
    
    VideoColumn > .empty-message {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
    }
    
    VideoColumn > .page-info {
        width: 100%;
        height: 1;
        background: $panel;
        color: $text-muted;
        text-align: center;
        border-bottom: tall $accent;
        padding: 0 1;
    }
    """
    
    selected_index = reactive(0)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.videos: List[Video] = []
        self.can_focus = True
        self.search_query = ""
        self.search_matches: List[int] = []
        self.current_match_index = -1
        self.visual_mode = False
        self.visual_start_index = -1
        self.visual_unmark_mode = False  # For uV command
        
        # Pagination settings
        self.page_size = 100  # Number of videos per page
        self.current_page = 0
        self.total_pages = 1
        
    def compose(self) -> ComposeResult:
        """Initial composition."""
        yield Static("Select a playlist", classes="empty-message")
        
    async def set_videos(self, videos: List[Video]) -> None:
        """Set the videos to display."""
        self.videos = videos
        self.selected_index = 0 if videos else -1
        
        # Calculate pagination
        self.total_pages = max(1, (len(videos) + self.page_size - 1) // self.page_size)
        self.current_page = 0
        
        await self.refresh_display()
        
    async def refresh_display(self) -> None:
        """Refresh the video display."""
        await self.remove_children()
        
        if not self.videos:
            await self.mount(Static("No videos in playlist", classes="empty-message"))
            return
        
        # Calculate page boundaries
        start_idx = self.current_page * self.page_size
        end_idx = min(start_idx + self.page_size, len(self.videos))
        
        # Show page info if we have multiple pages
        if self.total_pages > 1:
            page_info = f"Page {self.current_page + 1}/{self.total_pages} (Videos {start_idx + 1}-{end_idx} of {len(self.videos)})"
            await self.mount(Static(page_info, classes="page-info"))
            
        # Calculate visual range if in visual mode
        visual_range = set()
        if self.visual_mode and self.visual_start_index >= 0:
            start = min(self.visual_start_index, self.selected_index)
            end = max(self.visual_start_index, self.selected_index)
            visual_range = set(range(start, end + 1))
            
        # Only display videos on current page
        for i in range(start_idx, end_idx):
            video = self.videos[i]
            classes = ["video-item"]
            if i == self.selected_index:
                classes.append("selected")
            if video.is_marked or i in visual_range:
                classes.append("marked")
            if i in self.search_matches:
                classes.append("search-match")
                
            # Format display text
            # In visual unmark mode, show different indicator
            if self.visual_unmark_mode and i in visual_range:
                marker = "✗ "  # X mark for items to be unmarked
            elif video.is_marked or i in visual_range:
                marker = "◆ "  # Diamond for marked/to-be-marked
            else:
                marker = "  "
            title = video.title
            
            # Highlight search matches
            if self.search_query and i in self.search_matches:
                title = SearchHighlighter.highlight(title, self.search_query)
                
            text = f"{marker}{title}"
            
            item = Static(text, classes=" ".join(classes))
            item.video = video  # Attach video data
            await self.mount(item)
            
    def watch_selected_index(self, old_value: int, new_value: int) -> None:
        """React to selection changes."""
        items = self.query(".video-item")
        for i, item in enumerate(items):
            if i == old_value:
                item.remove_class("selected")
            if i == new_value:
                item.add_class("selected")
                
        if 0 <= new_value < len(self.videos):
            self.post_message(
                VideoSelected(self.videos[new_value])
            )
    
    def move_selection(self, delta: int) -> None:
        """Move selection up or down."""
        if not self.videos:
            return
            
        new_index = self.selected_index + delta
        new_index = max(0, min(new_index, len(self.videos) - 1))
        
        # Check if we need to change page
        old_page = self.selected_index // self.page_size
        new_page = new_index // self.page_size
        
        self.selected_index = new_index
        
        # Change page if needed
        if new_page != old_page:
            self.current_page = new_page
            self.call_later(self.refresh_display)
        else:
            # Find the item index on current page
            page_start = self.current_page * self.page_size
            item_index = new_index - page_start
            items = self.query(".video-item")
            if 0 <= item_index < len(items):
                self.scroll_to_widget(items[item_index])
        
    def select_first(self) -> None:
        """Select first video (gg)."""
        self.selected_index = 0
        self.current_page = 0
        self.call_later(self.refresh_display)
        
    def select_last(self) -> None:
        """Select last video (G)."""
        if self.videos:
            self.selected_index = len(self.videos) - 1
            self.current_page = self.total_pages - 1
            self.call_later(self.refresh_display)
    
    def next_page(self) -> None:
        """Go to next page (Page Down)."""
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            # Move selection to first item on new page
            self.selected_index = self.current_page * self.page_size
            self.call_later(self.refresh_display)
    
    def prev_page(self) -> None:
        """Go to previous page (Page Up)."""
        if self.current_page > 0:
            self.current_page -= 1
            # Move selection to first item on new page
            self.selected_index = self.current_page * self.page_size
            self.call_later(self.refresh_display)
    
    def toggle_mark(self) -> None:
        """Toggle mark on current video (Space)."""
        if 0 <= self.selected_index < len(self.videos):
            video = self.videos[self.selected_index]
            video.is_marked = not video.is_marked
            asyncio.create_task(self.refresh_display())
            
    def get_marked_videos(self) -> List[Video]:
        """Get all marked videos."""
        return [v for v in self.videos if v.is_marked]
    
    def clear_marks(self) -> None:
        """Clear all marks."""
        for video in self.videos:
            video.is_marked = False
        asyncio.create_task(self.refresh_display())
        
    def search(self, query: str) -> int:
        """Search for videos matching query.
        
        Args:
            query: Search query
            
        Returns:
            Number of matches found
        """
        self.search_query = query
        self.search_matches = []
        self.current_match_index = -1
        
        if not query:
            asyncio.create_task(self.refresh_display())
            return 0
            
        # Case-insensitive search in title and channel
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        
        for i, video in enumerate(self.videos):
            if pattern.search(video.title) or (video.channel_title and pattern.search(video.channel_title)):
                self.search_matches.append(i)
                
        # Jump to first match
        if self.search_matches:
            self.current_match_index = 0
            self.selected_index = self.search_matches[0]
            
        asyncio.create_task(self.refresh_display())
        return len(self.search_matches)
        
    def next_match(self) -> bool:
        """Jump to next search match.
        
        Returns:
            True if moved to next match, False if no matches
        """
        if not self.search_matches:
            return False
            
        self.current_match_index = (self.current_match_index + 1) % len(self.search_matches)
        self.selected_index = self.search_matches[self.current_match_index]
        asyncio.create_task(self.refresh_display())
        return True
        
    def prev_match(self) -> bool:
        """Jump to previous search match.
        
        Returns:
            True if moved to previous match, False if no matches
        """
        if not self.search_matches:
            return False
            
        self.current_match_index = (self.current_match_index - 1) % len(self.search_matches)
        self.selected_index = self.search_matches[self.current_match_index]
        asyncio.create_task(self.refresh_display())
        return True
        
    def clear_search(self) -> None:
        """Clear search highlighting."""
        self.search_query = ""
        self.search_matches = []
        self.current_match_index = -1
        asyncio.create_task(self.refresh_display())
        
    def enter_visual_mode(self, unmark_mode: bool = False) -> None:
        """Enter visual mode for range selection.
        
        Args:
            unmark_mode: If True, visual mode will unmark instead of mark (uV command)
        """
        self.visual_mode = True
        self.visual_start_index = self.selected_index
        self.visual_unmark_mode = unmark_mode
        asyncio.create_task(self.refresh_display())
        
    def exit_visual_mode(self, mark_selection: bool = True) -> None:
        """Exit visual mode and optionally mark/unmark the selection.
        
        Args:
            mark_selection: Whether to apply marks/unmarks to the selected range
        """
        if self.visual_mode and mark_selection and self.visual_start_index >= 0:
            # Mark or unmark all videos in the visual range
            start = min(self.visual_start_index, self.selected_index)
            end = max(self.visual_start_index, self.selected_index)
            for i in range(start, end + 1):
                if i < len(self.videos):
                    # Set mark based on mode (mark for V, unmark for uV)
                    self.videos[i].is_marked = not self.visual_unmark_mode
                    
        self.visual_mode = False
        self.visual_start_index = -1
        self.visual_unmark_mode = False
        asyncio.create_task(self.refresh_display())
        
    def select_all(self) -> None:
        """Mark all videos (V command)."""
        for video in self.videos:
            video.is_marked = True
        asyncio.create_task(self.refresh_display())
        
    def unselect_all(self) -> None:
        """Unmark all videos (uv command)."""
        for video in self.videos:
            video.is_marked = False
        asyncio.create_task(self.refresh_display())
        
    def invert_selection(self) -> None:
        """Invert selection - marked become unmarked, unmarked become marked."""
        for video in self.videos:
            video.is_marked = not video.is_marked
        asyncio.create_task(self.refresh_display())


class PreviewPane(ScrollableContainer):
    """Right column showing video preview/metadata."""

    DEFAULT_CSS = """
    PreviewPane {
        width: 1fr;
        height: 100%;
        padding: 1;
    }

    PreviewPane > .preview-content {
        width: 100%;
    }

    PreviewPane > .preview-title {
        text-style: bold;
        margin-bottom: 1;
    }

    PreviewPane > .preview-field {
        margin-bottom: 1;
    }

    PreviewPane > .preview-label {
        color: $text-muted;
    }

    PreviewPane > .preview-transcript {
        margin-top: 1;
        padding-top: 1;
        border-top: solid $accent;
    }

    PreviewPane > .empty-preview {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
    }
    """

    def __init__(self, cache=None, settings=None, *args, **kwargs):
        """Initialize preview pane.

        Args:
            cache: PersistentCache instance for transcript access
            settings: Settings instance for configuration
        """
        super().__init__(*args, **kwargs)
        self.cache = cache
        self.settings = settings

    def compose(self) -> ComposeResult:
        """Initial composition."""
        yield Static("Select a video to preview", classes="empty-preview")
        
    async def show_video(self, video: Video) -> None:
        """Display video information."""
        await self.remove_children()
        
        # Title
        await self.mount(Static(video.title, classes="preview-title"))
        
        # Channel
        await self.mount(Static(
            f"[dim]Channel:[/dim] {video.channel_title}",
            classes="preview-field"
        ))
        
        # Duration
        if video.duration:
            await self.mount(Static(
                f"[dim]Duration:[/dim] {video.format_duration()}",
                classes="preview-field"
            ))
        
        # Views
        if video.view_count is not None:
            await self.mount(Static(
                f"[dim]Views:[/dim] {video.format_view_count()}",
                classes="preview-field"
            ))
        
        # Added date
        if video.added_at:
            date_str = video.added_at.strftime("%Y-%m-%d")
            await self.mount(Static(
                f"[dim]Added:[/dim] {date_str}",
                classes="preview-field"
            ))
        
        # Description
        if video.description:
            await self.mount(Static(
                "[dim]Description:[/dim]",
                classes="preview-field"
            ))
            # Truncate long descriptions
            desc = video.description[:500]
            if len(video.description) > 500:
                desc += "..."
            await self.mount(Static(desc, classes="preview-field"))

        # Transcript (if enabled and cached)
        if self.cache and self.settings and self.settings.transcripts.enabled:
            transcript_data = self.cache.get_transcript(video.id)
            if transcript_data and transcript_data['fetch_status'] == 'SUCCESS':
                try:
                    from ..core.transcript_fetcher import TranscriptFetcher

                    # Decompress transcript text
                    text = TranscriptFetcher.decompress_transcript(transcript_data['transcript_text'])

                    # Format for display
                    max_chars = 1000
                    if len(text) > max_chars:
                        text = text[:max_chars] + "..."

                    # Type string
                    type_str = "auto-generated" if transcript_data['auto_generated'] else "manual"
                    header = f"[dim]Transcript ({transcript_data['language']}, {type_str}):[/dim]"

                    await self.mount(Static(header, classes="preview-transcript"))
                    await self.mount(Static(text, classes="preview-field"))

                except Exception as e:
                    # Silently fail if transcript display fails
                    import logging
                    logging.getLogger(__name__).warning(f"Failed to display transcript: {e}")


class MillerView(Widget):
    """Three-column Miller view container."""
    
    DEFAULT_CSS = """
    MillerView {
        layout: horizontal;
        width: 100%;
        height: 100%;
    }
    """
    
    # Track which column has focus (0=playlists, 1=videos, 2=preview)
    focused_column = reactive(0)

    def __init__(self, cache=None, settings=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cache = cache
        self.settings = settings
        self.playlist_column: Optional[PlaylistColumn] = None
        self.video_column: Optional[VideoColumn] = None
        self.preview_pane: Optional[PreviewPane] = None
        self.search_input: Optional[SearchInput] = None
        self.search_active = False
        self.pending_u_command = False  # For 'uv' command
        
    def compose(self) -> ComposeResult:
        """Create the three columns."""
        self.playlist_column = PlaylistColumn(id="playlist-column")
        self.video_column = VideoColumn(id="video-column")
        self.preview_pane = PreviewPane(cache=self.cache, settings=self.settings, id="preview-pane")
        self.search_input = SearchInput(
            on_search=self.on_search_submit,
            on_cancel=self.on_search_cancel
        )
        
        # Search input overlay
        yield self.search_input
        
        # Three columns
        with Horizontal():
            yield self.playlist_column
            yield self.video_column
            yield self.preview_pane
        
    async def show_loading_playlists(self) -> None:
        """Show loading state in playlist column."""
        if self.playlist_column:
            await self.playlist_column.remove_children()
            loading = LoadingIndicator()
            await self.playlist_column.mount(loading)
            
    async def show_loading_videos(self) -> None:
        """Show loading state in video column."""
        if self.video_column:
            await self.video_column.remove_children()
            loading = LoadingIndicator()
            await self.video_column.mount(loading)
            
    async def set_playlists(self, playlists: List[Playlist]) -> None:
        """Set playlists in the left column."""
        if self.playlist_column:
            await self.playlist_column.set_playlists(playlists)
            
    async def set_videos(self, videos: List[Video]) -> None:
        """Set videos in the middle column."""
        if self.video_column:
            await self.video_column.set_videos(videos)
            
    async def update_preview(self, video: Video) -> None:
        """Update preview pane with video info."""
        if self.preview_pane:
            await self.preview_pane.show_video(video)
            
    def get_marked_count(self) -> int:
        """Get count of marked videos in current column."""
        if self.video_column:
            return len(self.video_column.get_marked_videos())
        return 0
        
    def on_search_submit(self, query: str) -> None:
        """Handle search submission based on current focus."""
        match_count = 0
        
        # Set search as active
        self.search_active = True
        
        # Search in the appropriate column based on focused_column
        if self.focused_column == 0 and self.playlist_column:
            # Search playlists
            match_count = self.playlist_column.search(query)
        elif self.focused_column == 1 and self.video_column:
            # Search videos
            match_count = self.video_column.search(query)
        
        # Update search status
        if match_count > 0:
            self.post_message(SearchStatusUpdate(1, match_count))
        else:
            self.post_message(SearchStatusUpdate(0, 0))
                
    def on_search_cancel(self) -> None:
        """Handle search cancellation."""
        self.search_active = False
        
        # Clear search in both columns
        if self.playlist_column:
            self.playlist_column.clear_search()
        if self.video_column:
            self.video_column.clear_search()
            
        self.post_message(SearchStatusUpdate(0, 0))
            
    def watch_focused_column(self, old_value: int, new_value: int) -> None:
        """Update focus styling when column focus changes."""
        columns = [self.playlist_column, self.video_column, self.preview_pane]
        
        # Remove focus from old column
        if 0 <= old_value < len(columns) and columns[old_value]:
            columns[old_value].remove_class("focused")
            
        # Add focus to new column
        if 0 <= new_value < len(columns) and columns[new_value]:
            columns[new_value].add_class("focused")
            columns[new_value].focus()
            
    async def handle_key(self, key: str) -> None:
        """Handle vim-style navigation keys."""
        # Handle 'u' prefix for 'uv' and 'uV' commands
        if key == 'u' and self.focused_column == 1:
            self.pending_u_command = True
            return
        elif self.pending_u_command:
            if key == 'v' and self.video_column:
                # Unselect all (uv) - clear all marks
                self.video_column.unselect_all()
                self.post_message(MarksChanged(0))
            elif key == 'V' and self.video_column:
                # Visual unmark mode (uV) - enter visual mode but for unmarking
                if not self.video_column.visual_mode:
                    self.video_column.enter_visual_mode(unmark_mode=True)
            self.pending_u_command = False
            return
            
        # Visual mode (uppercase V like ranger)
        if key == 'V' and self.focused_column == 1 and self.video_column:
            if self.video_column.visual_mode:
                # Exit visual mode and apply marks/unmarks
                self.video_column.exit_visual_mode(mark_selection=True)
                self.post_message(MarksChanged(self.get_marked_count()))
            else:
                # Enter visual mode for marking
                self.video_column.enter_visual_mode(unmark_mode=False)
            return
        elif key == 'v' and self.focused_column == 1 and self.video_column:
            # lowercase v - invert selection (mark unmarked, unmark marked)
            self.video_column.invert_selection()
            self.post_message(MarksChanged(self.get_marked_count()))
            return
        elif key == 'escape' and self.video_column and self.video_column.visual_mode:
            # Cancel visual mode without marking
            self.video_column.exit_visual_mode(mark_selection=False)
            return
            
        # Search mode - removed focused_column restriction since it's handled in action_search
        # This code path may not be needed anymore since '/' is handled via action_search
        elif key == 'n' and self.search_active and self.video_column:
            # Next search match
            if self.video_column.next_match():
                self.post_message(SearchStatusUpdate(
                    self.video_column.current_match_index + 1,
                    len(self.video_column.search_matches)
                ))
            return
        elif key == 'N' and self.search_active and self.video_column:
            # Previous search match
            if self.video_column.prev_match():
                self.post_message(SearchStatusUpdate(
                    self.video_column.current_match_index + 1,
                    len(self.video_column.search_matches)
                ))
            return
            
        # Column navigation
        if key == 'h':  # Move left
            self.focused_column = max(0, self.focused_column - 1)
        elif key == 'l':  # Move right
            self.focused_column = min(2, self.focused_column + 1)
            
        # Enter key - trigger selection
        elif key == 'enter':
            if self.focused_column == 0 and self.playlist_column:
                # Trigger playlist selection
                if 0 <= self.playlist_column.selected_index < len(self.playlist_column.playlists):
                    playlist = self.playlist_column.playlists[self.playlist_column.selected_index]
                    self.post_message(PlaylistSelected(playlist))
            elif self.focused_column == 1 and self.video_column:
                # Trigger video selection
                if 0 <= self.video_column.selected_index < len(self.video_column.videos):
                    video = self.video_column.videos[self.video_column.selected_index]
                    self.post_message(VideoSelected(video))
                    
        # Space key - toggle mark on video (NO auto-advance like real ranger)
        elif key == 'space' and self.focused_column == 1 and self.video_column:
            self.video_column.toggle_mark()
            # Don't move cursor - ranger doesn't auto-advance on spacebar
            # Notify about mark change
            self.post_message(MarksChanged(self.get_marked_count()))
            
        # Ranger-style commands
        elif key == 'd' and self.focused_column == 1 and self.video_column:
            # Handle dd (cut) - wait for second 'd'
            self.post_message(RangerCommand('d'))
        elif key == 'y' and self.focused_column == 1 and self.video_column:
            # Handle yy (copy) - wait for second 'y'
            self.post_message(RangerCommand('y'))
        elif key == 'p' and self.focused_column == 1:
            # Handle pp (paste) - wait for second 'p'
            self.post_message(RangerCommand('p'))
        elif key == 'o' and self.focused_column == 1 and self.video_column:
            # Handle sort menu
            self.post_message(SortMenuRequest())
        
        # Search navigation - 'n' and 'N'
        elif key == 'n' and self.search_active:
            # Next search match
            if self.focused_column == 0 and self.playlist_column:
                self.playlist_column.next_search_match()
            elif self.focused_column == 1 and self.video_column:
                self.video_column.next_search_match()
        elif key == 'N' and self.search_active:
            # Previous search match
            if self.focused_column == 0 and self.playlist_column:
                self.playlist_column.previous_search_match()
            elif self.focused_column == 1 and self.video_column:
                self.video_column.previous_search_match()
        
        # Cancel search with escape
        elif key == 'escape' and self.search_active:
            self.on_search_cancel()
            if self.search_input:
                self.search_input.hide()
                    
        # Vertical navigation in focused column
        elif key in ['j', 'k', 'g', 'G']:
            if self.focused_column == 0 and self.playlist_column:
                if key == 'j':
                    self.playlist_column.move_selection(1)
                elif key == 'k':
                    self.playlist_column.move_selection(-1)
                elif key == 'g':
                    self.playlist_column.select_first()
                elif key == 'G':
                    self.playlist_column.select_last()
                    
            elif self.focused_column == 1 and self.video_column:
                # In visual mode, just update selection to expand range
                if key == 'j':
                    self.video_column.move_selection(1)
                elif key == 'k':
                    self.video_column.move_selection(-1)
                elif key == 'g':
                    self.video_column.select_first()
                elif key == 'G':
                    self.video_column.select_last()
                # Refresh to show visual range updates
                if self.video_column.visual_mode:
                    asyncio.create_task(self.video_column.refresh_display())
        
        # Page navigation for video column
        elif key in ['pageup', 'pagedown'] and self.focused_column == 1 and self.video_column:
            if key == 'pagedown':
                self.video_column.next_page()
            elif key == 'pageup':
                self.video_column.prev_page()


# Custom messages
class PlaylistSelected(events.Message):
    """Message sent when a playlist is selected."""
    def __init__(self, playlist: Playlist):
        super().__init__()
        self.playlist = playlist


class VideoSelected(events.Message):
    """Message sent when a video is selected."""
    def __init__(self, video: Video):
        super().__init__()
        self.video = video


class RangerCommand(events.Message):
    """Message sent when a ranger-style command key is pressed."""
    def __init__(self, command: str):
        super().__init__()
        self.command = command


class MarksChanged(events.Message):
    """Message sent when video marks change."""
    def __init__(self, count: int):
        super().__init__()
        self.count = count


class SearchStatusUpdate(events.Message):
    """Message sent when search status changes."""
    def __init__(self, current: int, total: int):
        super().__init__()
        self.current = current
        self.total = total


class SortMenuRequest(events.Message):
    """Message sent when sort menu is requested."""
    pass