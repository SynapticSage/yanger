"""Miller column view implementation for YouTube Ranger.

Three-column layout inspired by macOS Finder's column view.
"""
# Created: 2025-08-03

from typing import List, Optional
import asyncio

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer
from textual.widgets import Static, ListView, ListItem, Label, LoadingIndicator
from textual.reactive import reactive
from textual.widget import Widget
from textual import events

from ..models import Playlist, Video


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
    
    VideoColumn > .empty-message {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
    }
    """
    
    selected_index = reactive(0)
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.videos: List[Video] = []
        self.can_focus = True
        
    def compose(self) -> ComposeResult:
        """Initial composition."""
        yield Static("Select a playlist", classes="empty-message")
        
    async def set_videos(self, videos: List[Video]) -> None:
        """Set the videos to display."""
        self.videos = videos
        self.selected_index = 0 if videos else -1
        await self.refresh_display()
        
    async def refresh_display(self) -> None:
        """Refresh the video display."""
        await self.remove_children()
        
        if not self.videos:
            await self.mount(Static("No videos in playlist", classes="empty-message"))
            return
            
        for i, video in enumerate(self.videos):
            classes = ["video-item"]
            if i == self.selected_index:
                classes.append("selected")
            if video.is_marked:
                classes.append("marked")
                
            # Format display text
            marker = "◆ " if video.is_marked else "  "
            text = f"{marker}{video.title}"
            
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
        self.selected_index = new_index
        
        self.scroll_to_widget(self.query(".video-item")[new_index])
        
    def select_first(self) -> None:
        """Select first video (gg)."""
        self.selected_index = 0
        self.scroll_home()
        
    def select_last(self) -> None:
        """Select last video (G)."""
        if self.videos:
            self.selected_index = len(self.videos) - 1
            self.scroll_end()
    
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
    
    PreviewPane > .empty-preview {
        width: 100%;
        height: 100%;
        content-align: center middle;
        color: $text-muted;
        text-style: italic;
    }
    """
    
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
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlist_column: Optional[PlaylistColumn] = None
        self.video_column: Optional[VideoColumn] = None
        self.preview_pane: Optional[PreviewPane] = None
        
    def compose(self) -> ComposeResult:
        """Create the three columns."""
        self.playlist_column = PlaylistColumn(id="playlist-column")
        self.video_column = VideoColumn(id="video-column")
        self.preview_pane = PreviewPane(id="preview-pane")
        
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
                    
        # Space key - toggle mark on video
        elif key == ' ' and self.focused_column == 1 and self.video_column:
            self.video_column.toggle_mark()
            self.video_column.move_selection(1)  # Move to next item like ranger
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
                if key == 'j':
                    self.video_column.move_selection(1)
                elif key == 'k':
                    self.video_column.move_selection(-1)
                elif key == 'g':
                    self.video_column.select_first()
                elif key == 'G':
                    self.video_column.select_last()


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