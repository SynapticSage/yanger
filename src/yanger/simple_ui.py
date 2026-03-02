"""Simple rich-based UI for YouTube Ranger.

A fallback interface using rich for environments where Textual has issues.
Provides core playlist management through a menu-driven interface.
"""
# Created: 2026-03-02

from typing import Optional, List, Dict, Any
import sys

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich import box

from .auth import YouTubeAuth
from .api_client import YouTubeAPIClient, QuotaExceededError
from .cache import PersistentCache
from .models import Playlist, Video


class SimpleUI:
    """Simple menu-driven UI using rich."""

    def __init__(self):
        self.console = Console()
        self.auth: Optional[YouTubeAuth] = None
        self.api_client: Optional[YouTubeAPIClient] = None
        self.cache = PersistentCache()
        self._authenticated = False
        self._current_playlist: Optional[Playlist] = None
        self._playlists: List[Playlist] = []
        self._videos: List[Video] = []

    def run(self) -> None:
        """Run the simple UI main loop."""
        self.console.clear()
        self._print_header()

        # Authenticate
        if not self._authenticate():
            return

        # Load playlists
        self._load_playlists()

        # Main menu loop
        while True:
            try:
                action = self._main_menu()
                if action == "quit":
                    break
            except KeyboardInterrupt:
                self.console.print("\n[yellow]Interrupted[/yellow]")
                break
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")

        self.console.print("\n[dim]Goodbye![/dim]")

    def _print_header(self) -> None:
        """Print the application header."""
        self.console.print(Panel.fit(
            "[bold cyan]YouTube Ranger[/bold cyan] [dim]Simple Mode[/dim]",
            border_style="cyan"
        ))
        self.console.print()

    def _authenticate(self) -> bool:
        """Authenticate with YouTube API."""
        with self.console.status("[bold green]Authenticating..."):
            try:
                self.auth = YouTubeAuth()
                self.auth.authenticate()
                self.api_client = YouTubeAPIClient(self.auth)
                self._authenticated = True
                self.console.print("[green]✓[/green] Authenticated\n")
                return True
            except Exception as e:
                self.console.print(f"[red]Authentication failed: {e}[/red]")
                return False

    def _load_playlists(self) -> None:
        """Load playlists from cache or API."""
        with self.console.status("[bold green]Loading playlists..."):
            # Try cache first
            self._playlists = self.cache.get_playlists() or []

            if not self._playlists:
                # Fetch from API
                self._playlists = self.api_client.get_playlists()
                self.cache.set_playlists(self._playlists)

        self.console.print(f"[green]✓[/green] Loaded {len(self._playlists)} playlists\n")

    def _main_menu(self) -> str:
        """Display main menu and handle selection."""
        self.console.print("\n[bold]Main Menu[/bold]")
        self.console.print("─" * 40)

        options = [
            ("1", "List playlists"),
            ("2", "View playlist"),
            ("3", "Create playlist"),
            ("4", "Search videos"),
            ("5", "Virtual playlists"),
            ("6", "Check quota"),
            ("r", "Refresh playlists"),
            ("q", "Quit"),
        ]

        for key, label in options:
            self.console.print(f"  [{key}] {label}")

        choice = Prompt.ask("\nSelect", choices=[o[0] for o in options], default="1")

        if choice == "1":
            self._show_playlists()
        elif choice == "2":
            self._view_playlist_menu()
        elif choice == "3":
            self._create_playlist()
        elif choice == "4":
            self._search_videos()
        elif choice == "5":
            self._virtual_playlists_menu()
        elif choice == "6":
            self._show_quota()
        elif choice == "r":
            self._refresh_playlists()
        elif choice == "q":
            return "quit"

        return "continue"

    def _show_playlists(self) -> None:
        """Display all playlists in a table."""
        table = Table(title="Your Playlists", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="cyan")
        table.add_column("Videos", justify="right")
        table.add_column("Privacy", style="dim")

        for i, playlist in enumerate(self._playlists, 1):
            table.add_row(
                str(i),
                playlist.title[:50],
                str(playlist.item_count),
                playlist.privacy_status.value
            )

        self.console.print()
        self.console.print(table)

    def _view_playlist_menu(self) -> None:
        """Select and view a playlist."""
        if not self._playlists:
            self.console.print("[yellow]No playlists available[/yellow]")
            return

        self._show_playlists()

        try:
            idx = IntPrompt.ask(
                "\nEnter playlist number (0 to cancel)",
                default=0
            )
            if idx == 0:
                return
            if idx < 1 or idx > len(self._playlists):
                self.console.print("[red]Invalid selection[/red]")
                return

            playlist = self._playlists[idx - 1]
            self._view_playlist(playlist)

        except ValueError:
            self.console.print("[red]Invalid input[/red]")

    def _view_playlist(self, playlist: Playlist) -> None:
        """View videos in a playlist."""
        self._current_playlist = playlist

        with self.console.status(f"[bold green]Loading {playlist.title}..."):
            # Try cache first
            self._videos = self.cache.get_videos(playlist.id) or []

            if not self._videos:
                self._videos = self.api_client.get_playlist_items(playlist.id)
                self.cache.set_videos(playlist.id, self._videos)

        # Show videos
        self._show_videos(playlist)

        # Playlist actions menu
        while True:
            action = self._playlist_actions_menu()
            if action == "back":
                break

    def _show_videos(self, playlist: Playlist) -> None:
        """Display videos in a table."""
        self.console.print()
        self.console.print(Panel(
            f"[bold]{playlist.title}[/bold]\n"
            f"[dim]{playlist.description[:100] if playlist.description else 'No description'}[/dim]",
            title="Playlist",
            border_style="cyan"
        ))

        if not self._videos:
            self.console.print("[yellow]No videos in this playlist[/yellow]")
            return

        table = Table(box=box.SIMPLE)
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="white", max_width=50)
        table.add_column("Channel", style="cyan", max_width=25)
        table.add_column("Duration", justify="right", style="dim")

        for i, video in enumerate(self._videos[:50], 1):  # Show first 50
            duration = video.duration or ""
            if duration.startswith("PT"):
                # Parse ISO 8601 duration
                duration = duration[2:].lower().replace("h", "h ").replace("m", "m ")

            table.add_row(
                str(i),
                video.title[:50],
                video.channel_title[:25] if video.channel_title else "",
                duration
            )

        self.console.print(table)

        if len(self._videos) > 50:
            self.console.print(f"[dim]... and {len(self._videos) - 50} more videos[/dim]")

    def _playlist_actions_menu(self) -> str:
        """Show playlist action menu."""
        self.console.print("\n[bold]Actions[/bold]")
        options = [
            ("a", "Add video by URL"),
            ("d", "Delete video"),
            ("r", "Rename playlist"),
            ("x", "Delete playlist"),
            ("b", "Back to main menu"),
        ]

        for key, label in options:
            self.console.print(f"  [{key}] {label}")

        choice = Prompt.ask("Select", choices=[o[0] for o in options], default="b")

        if choice == "a":
            self._add_video()
        elif choice == "d":
            self._delete_video()
        elif choice == "r":
            self._rename_playlist()
        elif choice == "x":
            if self._delete_playlist():
                return "back"
        elif choice == "b":
            return "back"

        return "continue"

    def _add_video(self) -> None:
        """Add a video to the current playlist."""
        if not self._current_playlist:
            return

        url = Prompt.ask("Enter YouTube video URL or ID")
        if not url:
            return

        # Extract video ID from URL
        video_id = self._extract_video_id(url)
        if not video_id:
            self.console.print("[red]Invalid video URL or ID[/red]")
            return

        try:
            with self.console.status("[bold green]Adding video..."):
                self.api_client.add_video_to_playlist(
                    video_id=video_id,
                    playlist_id=self._current_playlist.id
                )
            self.console.print(f"[green]✓[/green] Added video {video_id}")

            # Refresh video list
            self._videos = self.api_client.get_playlist_items(self._current_playlist.id)
            self.cache.set_videos(self._current_playlist.id, self._videos)

        except QuotaExceededError:
            self.console.print("[red]API quota exceeded![/red]")
        except Exception as e:
            self.console.print(f"[red]Failed to add video: {e}[/red]")

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from URL or return as-is if already an ID."""
        import re

        # Already an ID (11 characters)
        if re.match(r'^[a-zA-Z0-9_-]{11}$', url):
            return url

        # YouTube URL patterns
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
            r'youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def _delete_video(self) -> None:
        """Delete a video from the current playlist."""
        if not self._videos:
            self.console.print("[yellow]No videos to delete[/yellow]")
            return

        try:
            idx = IntPrompt.ask("Enter video number to delete (0 to cancel)", default=0)
            if idx == 0:
                return
            if idx < 1 or idx > len(self._videos):
                self.console.print("[red]Invalid selection[/red]")
                return

            video = self._videos[idx - 1]

            if not Confirm.ask(f"Delete '{video.title[:50]}'?"):
                return

            with self.console.status("[bold green]Deleting video..."):
                self.api_client.remove_video_from_playlist(video.playlist_item_id)

            self.console.print(f"[green]✓[/green] Deleted video")

            # Refresh
            self._videos = self.api_client.get_playlist_items(self._current_playlist.id)
            self.cache.set_videos(self._current_playlist.id, self._videos)

        except QuotaExceededError:
            self.console.print("[red]API quota exceeded![/red]")
        except Exception as e:
            self.console.print(f"[red]Failed to delete: {e}[/red]")

    def _rename_playlist(self) -> None:
        """Rename the current playlist."""
        if not self._current_playlist:
            return

        new_name = Prompt.ask(
            "New name",
            default=self._current_playlist.title
        )

        if new_name == self._current_playlist.title:
            return

        try:
            with self.console.status("[bold green]Renaming..."):
                self.api_client.rename_playlist(self._current_playlist.id, new_name)

            self.console.print(f"[green]✓[/green] Renamed to '{new_name}'")
            self._current_playlist.title = new_name
            self._refresh_playlists()

        except Exception as e:
            self.console.print(f"[red]Failed to rename: {e}[/red]")

    def _delete_playlist(self) -> bool:
        """Delete the current playlist."""
        if not self._current_playlist:
            return False

        if not Confirm.ask(
            f"[red]Delete playlist '{self._current_playlist.title}'? This cannot be undone![/red]",
            default=False
        ):
            return False

        try:
            with self.console.status("[bold green]Deleting playlist..."):
                self.api_client.delete_playlist(self._current_playlist.id)

            self.console.print(f"[green]✓[/green] Deleted playlist")
            self._current_playlist = None
            self._refresh_playlists()
            return True

        except Exception as e:
            self.console.print(f"[red]Failed to delete: {e}[/red]")
            return False

    def _create_playlist(self) -> None:
        """Create a new playlist."""
        title = Prompt.ask("Playlist title")
        if not title:
            return

        description = Prompt.ask("Description (optional)", default="")

        privacy = Prompt.ask(
            "Privacy",
            choices=["private", "public", "unlisted"],
            default="private"
        )

        try:
            with self.console.status("[bold green]Creating playlist..."):
                playlist = self.api_client.create_playlist(
                    title=title,
                    description=description,
                    privacy_status=privacy
                )

            self.console.print(f"[green]✓[/green] Created playlist '{title}'")
            self._refresh_playlists()

        except QuotaExceededError:
            self.console.print("[red]API quota exceeded![/red]")
        except Exception as e:
            self.console.print(f"[red]Failed to create: {e}[/red]")

    def _search_videos(self) -> None:
        """Search videos across playlists."""
        query = Prompt.ask("Search query")
        if not query:
            return

        query_lower = query.lower()
        results = []

        with self.console.status("[bold green]Searching..."):
            for playlist in self._playlists:
                videos = self.cache.get_videos(playlist.id)
                if not videos:
                    continue

                for video in videos:
                    if query_lower in video.title.lower():
                        results.append((playlist, video))

        if not results:
            self.console.print("[yellow]No matches found[/yellow]")
            return

        table = Table(title=f"Search Results for '{query}'", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4)
        table.add_column("Video", style="white", max_width=40)
        table.add_column("Playlist", style="cyan", max_width=25)

        for i, (playlist, video) in enumerate(results[:20], 1):
            table.add_row(
                str(i),
                video.title[:40],
                playlist.title[:25]
            )

        self.console.print(table)

        if len(results) > 20:
            self.console.print(f"[dim]... and {len(results) - 20} more matches[/dim]")

    def _virtual_playlists_menu(self) -> None:
        """Show virtual playlists menu."""
        virtual = self.cache.get_virtual_playlists()

        if not virtual:
            self.console.print("[yellow]No virtual playlists.[/yellow]")
            self.console.print("[dim]Import from Google Takeout with: yanger takeout <path>[/dim]")
            return

        table = Table(title="Virtual Playlists", box=box.ROUNDED)
        table.add_column("#", style="dim", width=4)
        table.add_column("Title", style="cyan")
        table.add_column("Videos", justify="right")
        table.add_column("Source", style="dim")

        for i, vp in enumerate(virtual, 1):
            table.add_row(
                str(i),
                vp["title"],
                str(vp.get("video_count", 0)),
                vp.get("source", "")
            )

        self.console.print()
        self.console.print(table)

        # Option to copy videos to real playlist
        if Confirm.ask("\nCopy videos from virtual to real playlist?", default=False):
            self._copy_from_virtual(virtual)

    def _copy_from_virtual(self, virtual_playlists: List[Dict]) -> None:
        """Copy videos from virtual playlist to real playlist."""
        try:
            src_idx = IntPrompt.ask("Source virtual playlist #", default=1)
            if src_idx < 1 or src_idx > len(virtual_playlists):
                return

            source = virtual_playlists[src_idx - 1]

            self._show_playlists()
            dst_idx = IntPrompt.ask("Destination playlist #", default=1)
            if dst_idx < 1 or dst_idx > len(self._playlists):
                return

            target = self._playlists[dst_idx - 1]

            limit = IntPrompt.ask("How many videos to copy?", default=10)

            # Get virtual videos
            videos = self.cache.get_virtual_videos(source["id"])[:limit]

            if not Confirm.ask(f"Copy {len(videos)} videos to '{target.title}'?"):
                return

            copied = 0
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=self.console
            ) as progress:
                task = progress.add_task("Copying...", total=len(videos))

                for v in videos:
                    try:
                        self.api_client.add_video_to_playlist(
                            video_id=v["video_id"],
                            playlist_id=target.id
                        )
                        copied += 1
                    except QuotaExceededError:
                        self.console.print("\n[red]Quota exceeded![/red]")
                        break
                    except Exception:
                        pass  # Skip failed videos
                    progress.update(task, advance=1)

            self.console.print(f"[green]✓[/green] Copied {copied}/{len(videos)} videos")

        except ValueError:
            self.console.print("[red]Invalid input[/red]")

    def _show_quota(self) -> None:
        """Show API quota information."""
        self.console.print()
        self.console.print(Panel(
            f"[bold]Daily Limit:[/bold] {self.api_client.daily_quota:,}\n"
            f"[bold]Used:[/bold] {self.api_client.quota_used:,}\n"
            f"[bold]Remaining:[/bold] {self.api_client.get_quota_remaining():,}\n"
            f"[bold]Usage:[/bold] {self.api_client.quota_used / self.api_client.daily_quota * 100:.1f}%",
            title="API Quota",
            border_style="yellow"
        ))

    def _refresh_playlists(self) -> None:
        """Refresh playlists from API."""
        with self.console.status("[bold green]Refreshing playlists..."):
            self._playlists = self.api_client.get_playlists()
            self.cache.set_playlists(self._playlists)

        self.console.print(f"[green]✓[/green] Refreshed {len(self._playlists)} playlists")


def main() -> None:
    """Entry point for simple UI."""
    ui = SimpleUI()
    ui.run()


if __name__ == "__main__":
    main()
