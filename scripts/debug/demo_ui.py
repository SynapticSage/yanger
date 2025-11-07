#!/usr/bin/env python3
"""Demo UI for YouTube Ranger - No authentication required.

Shows the UI with sample data for testing.
"""
# Created: 2025-08-03

import sys
from pathlib import Path
from datetime import datetime

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Header, Footer

from yanger.models import Playlist, Video, PrivacyStatus
from yanger.ui.miller_view import MillerView, PlaylistSelected, VideoSelected
from yanger.ui.status_bar import StatusBar


class DemoApp(App):
    """Demo application with sample data."""
    
    CSS_PATH = "src/yanger/app.tcss"
    TITLE = "YouTube Ranger - Demo Mode"
    SUB_TITLE = "Navigate with hjkl, quit with q"
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "help", "Help"),
    ]
    
    def compose(self) -> ComposeResult:
        """Create the application layout."""
        yield Header()
        
        with Container(id="main-container"):
            yield MillerView(id="miller-view")
        
        yield StatusBar(id="status-bar")
        yield Footer()
    
    async def on_mount(self) -> None:
        """Initialize with demo data."""
        # Get UI components
        self.miller_view = self.query_one("#miller-view", MillerView)
        self.status_bar = self.query_one("#status-bar", StatusBar)
        
        # Create demo playlists
        demo_playlists = [
            Playlist(
                id="PL1",
                title="Watch Later",
                description="Videos to watch later",
                item_count=42,
                privacy_status=PrivacyStatus.PRIVATE,
                published_at=datetime(2024, 1, 1)
            ),
            Playlist(
                id="PL2",
                title="Music Videos",
                description="Favorite music videos",
                item_count=156,
                privacy_status=PrivacyStatus.PUBLIC,
                published_at=datetime(2023, 6, 15)
            ),
            Playlist(
                id="PL3",
                title="Programming Tutorials",
                description="Learn programming",
                item_count=89,
                privacy_status=PrivacyStatus.PUBLIC,
                published_at=datetime(2023, 9, 1)
            ),
            Playlist(
                id="PL4",
                title="Cooking Recipes",
                description="Delicious recipes",
                item_count=34,
                privacy_status=PrivacyStatus.UNLISTED,
                published_at=datetime(2024, 2, 10)
            ),
            Playlist(
                id="PL5",
                title="Travel Vlogs",
                description="Travel inspiration",
                item_count=67,
                privacy_status=PrivacyStatus.PUBLIC,
                published_at=datetime(2023, 11, 20)
            ),
        ]
        
        # Set playlists
        await self.miller_view.set_playlists(demo_playlists)
        
        # Update status
        self.status_bar.update_status(
            f"Demo mode - {len(demo_playlists)} playlists",
            "No quota usage"
        )
    
    async def on_playlist_selected(self, message: PlaylistSelected) -> None:
        """Handle playlist selection."""
        playlist = message.playlist
        
        # Create demo videos
        demo_videos = []
        for i in range(min(playlist.item_count, 20)):  # Show max 20 for demo
            video = Video(
                id=f"V{playlist.id}_{i}",
                playlist_item_id=f"PI{playlist.id}_{i}",
                title=f"Video {i+1} - {playlist.title}",
                channel_title=f"Channel {(i % 5) + 1}",
                description=f"This is a demo video in the {playlist.title} playlist.",
                position=i,
                duration=f"PT{(i*3+5)%60}M{(i*7+15)%60}S",
                view_count=(i+1) * 12345,
                added_at=datetime(2024, 1, i+1),
                playlist_id=playlist.id
            )
            demo_videos.append(video)
        
        await self.miller_view.set_videos(demo_videos)
        self.status_bar.update_context(
            f"{playlist.title} ({len(demo_videos)} videos shown)"
        )
    
    async def on_video_selected(self, message: VideoSelected) -> None:
        """Handle video selection."""
        await self.miller_view.update_preview(message.video)
    
    async def on_key(self, event) -> None:
        """Handle key events."""
        if self.miller_view and event.key in ['h', 'j', 'k', 'l', 'g', 'G', ' ', 'enter']:
            await self.miller_view.handle_key(event.key)
            event.stop()
    
    def action_quit(self) -> None:
        """Quit the application."""
        self.exit(0)
    
    def action_help(self) -> None:
        """Show help."""
        self.notify("Navigation: hjkl | Select: Space | Quit: q", timeout=3)


def main():
    """Run the demo app."""
    print("Starting YouTube Ranger Demo UI...")
    print("This is a demonstration with sample data - no YouTube account required.")
    print()
    
    app = DemoApp()
    app.run()


if __name__ == "__main__":
    main()