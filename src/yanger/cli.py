"""Command-line interface for YouTube Ranger.

Main entry point for the application.
"""
# Created: 2025-08-03

import sys
import logging
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .auth import YouTubeAuth
from .api_client import YouTubeAPIClient
from .cache import PersistentCache
from .takeout import TakeoutParser
from .export import PlaylistExporter


console = Console()


def setup_logging(verbose: bool = False) -> None:
    """Configure logging with rich handler."""
    level = logging.DEBUG if verbose else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)]
    )


@click.group(invoke_without_command=True)
@click.option('--version', is_flag=True, help='Show version and exit')
@click.option('-v', '--verbose', is_flag=True, help='Enable verbose logging')
@click.option('--config-dir', type=click.Path(), 
              default=None, help='Configuration directory')
@click.pass_context
def cli(ctx: click.Context, version: bool, verbose: bool, config_dir: Optional[str]):
    """YouTube Ranger - Terminal-based YouTube playlist manager.
    
    Navigate and manage YouTube playlists with vim-like keybindings.
    """
    if version:
        click.echo(f"YouTube Ranger v{__version__}")
        sys.exit(0)
    
    # Setup logging
    setup_logging(verbose)
    
    # Set config directory
    if config_dir:
        ctx.ensure_object(dict)
        ctx.obj['config_dir'] = Path(config_dir)
    
    # If no subcommand, run the main TUI
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
@click.option('--no-cache', is_flag=True, help='Disable offline cache')
@click.option('--log', type=click.Path(), help='Log keyboard commands and actions to file')
@click.option('--log-level', type=click.Choice(['DEBUG', 'INFO', 'WARNING', 'ERROR'], case_sensitive=False),
              default='INFO', help='Log level for command logging')
@click.pass_context
def run(ctx: click.Context, no_cache: bool, log: Optional[str], log_level: str):
    """Run the YouTube Ranger TUI application."""
    try:
        # Import here to avoid circular imports and defer heavy imports
        from .app import YouTubeRangerApp
        
        # Get config directory from context
        config_dir = ctx.obj.get('config_dir') if ctx.obj else None
        
        # Create and run the app
        app = YouTubeRangerApp(
            config_dir=config_dir,
            use_cache=not no_cache,
            log_file=log,
            log_level=log_level
        )
        app.run()
        
    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependencies: {e}")
        console.print("Please install all requirements: pip install -e .")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if ctx.obj and ctx.obj.get('verbose'):
            console.print_exception()
        sys.exit(1)


@cli.command()
@click.option('--client-secrets', type=click.Path(exists=True),
              default='config/client_secret.json',
              help='Path to OAuth2 client secrets file')
@click.option('--token-file', type=click.Path(),
              default='token.json',
              help='Path to store authentication token')
def auth(client_secrets: str, token_file: str):
    """Setup or test YouTube API authentication."""
    console.print("[yellow]YouTube API Authentication Setup[/yellow]\n")
    
    try:
        # Create auth handler
        auth_handler = YouTubeAuth(
            client_secrets_file=client_secrets,
            token_file=token_file
        )
        
        # Perform authentication
        console.print("Starting authentication flow...")
        auth_handler.authenticate()
        
        # Test the authentication
        console.print("\nTesting authentication...")
        if auth_handler.test_authentication():
            console.print("\n[green]✓[/green] Authentication successful!")
            console.print(f"Token saved to: {token_file}")
            console.print("\nYou're ready to use YouTube Ranger!")
        else:
            console.print("\n[red]✗[/red] Authentication test failed.")
            sys.exit(1)
            
    except FileNotFoundError as e:
        console.print(f"\n[red]Error:[/red] {e}")
        console.print("\nTo get OAuth2 credentials:")
        console.print("1. Go to https://console.cloud.google.com/")
        console.print("2. Create a project and enable YouTube Data API v3")
        console.print("3. Create OAuth 2.0 credentials (Desktop type)")
        console.print("4. Download and save as: config/client_secret.json")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        console.print_exception()
        sys.exit(1)


@cli.command()
@click.option('--reset-token', is_flag=True, help='Remove stored authentication')
@click.option('--reset-cache', is_flag=True, help='Clear offline cache')
@click.option('--reset-config', is_flag=True, help='Reset to default configuration')
def reset(reset_token: bool, reset_cache: bool, reset_config: bool):
    """Reset various application data."""
    if not any([reset_token, reset_cache, reset_config]):
        console.print("Nothing to reset. Use --help to see options.")
        return
    
    if reset_token:
        token_file = Path('token.json')
        if token_file.exists():
            token_file.unlink()
            console.print("[green]✓[/green] Removed authentication token")
        else:
            console.print("No token file found")
    
    if reset_cache:
        cache_dir = Path('.yanger_cache')
        if cache_dir.exists():
            import shutil
            shutil.rmtree(cache_dir)
            console.print("[green]✓[/green] Cleared cache directory")
        else:
            console.print("No cache directory found")
    
    if reset_config:
        config_files = [
            Path('config/user_config.yaml'),
            Path('config/keybindings_user.yaml')
        ]
        for config_file in config_files:
            if config_file.exists():
                config_file.unlink()
                console.print(f"[green]✓[/green] Removed {config_file}")


@cli.command()
def quota():
    """Check current API quota usage."""
    try:
        # Setup authentication
        auth_handler = YouTubeAuth()
        auth_handler.authenticate()
        
        # Create API client
        client = YouTubeAPIClient(auth_handler)
        
        # Get channel info to test and use 1 quota unit
        client._track_quota('playlists.list')
        
        # Display quota info
        console.print("[yellow]YouTube API Quota Status[/yellow]\n")
        console.print(f"Daily limit: {client.daily_quota:,} units")
        console.print(f"Used today: {client.quota_used:,} units")
        console.print(f"Remaining: {client.get_quota_remaining():,} units")
        console.print(f"Percentage: {(client.quota_used / client.daily_quota * 100):.1f}%")
        
        console.print("\n[dim]Operation costs:[/dim]")
        console.print("• List operations: 1 unit")
        console.print("• Write operations: 50 units")
        console.print("• Move video: 100 units (add + remove)")
        
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def main():
    """Main entry point."""
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]Unexpected error:[/red] {e}")
        console.print_exception()
        sys.exit(1)


@cli.command()
@click.argument('paths', nargs=-1, required=True, type=click.Path(exists=True))
@click.option('--merge/--replace', default=True, help='Merge with existing virtual playlists')
@click.option('-v', '--verbose', is_flag=True, help='Show detailed progress')
def takeout(paths, merge, verbose):
    """Import YouTube data from Google Takeout.
    
    Accepts multiple paths (zip files or directories).
    Creates virtual playlists for Watch Later and History.
    
    Examples:
        yanger takeout ~/Downloads/takeout.zip
        yanger takeout Takeout-1/ Takeout-2/ --merge
    """
    if verbose:
        setup_logging(verbose=True)
    
    console.print("[bold cyan]YouTube Takeout Importer[/bold cyan]")
    console.print(f"Processing {len(paths)} takeout file(s)...\n")
    console.print(f"Mode: {'Merge with existing' if merge else 'Replace existing'}")
    
    # Initialize parser and cache
    parser = TakeoutParser()
    cache = PersistentCache()
    
    # Process all takeout paths
    all_playlists = parser.process_multiple(list(paths))
    
    if not all_playlists:
        console.print("[red]No YouTube data found in the provided takeout files.[/red]")
        sys.exit(1)
    
    # Import into database
    imported_count = 0
    updated_count = 0
    total_videos = 0
    
    with console.status("[bold green]Importing playlists...") as status:
        for name, playlist in all_playlists.items():
            # Prepare video data
            videos = [
                {
                    'video_id': v.video_id,
                    'title': v.title,
                    'channel': v.channel,
                    'added_at': v.added_at.isoformat() if v.added_at else None
                }
                for v in playlist.videos
            ]
            
            # Determine description based on source
            if playlist.source == 'watch_later':
                description = f"Watch Later playlist imported from Google Takeout ({len(videos)} videos)"
            elif playlist.source == 'history':
                description = f"Watch History imported from Google Takeout ({len(videos)} videos)"
            else:
                description = f"Playlist imported from Google Takeout ({len(videos)} videos)"
            
            # Check if playlist already exists
            existing = cache.get_virtual_playlist_by_name(name)
            
            # Import or update database
            try:
                if existing and merge:
                    # Merge mode: update existing playlist with new videos
                    playlist_id = cache.update_or_create_virtual_playlist(
                        name=name,
                        videos=videos,
                        source='takeout',
                        description=description,
                        merge=True
                    )
                    updated_count += 1
                    total_videos += len(videos)
                    status.update(f"Updated {updated_count} playlists, imported {imported_count} new...")
                    
                    if verbose:
                        console.print(f"  ⟳ {name}: merged {len(videos)} videos")
                        
                elif existing and not merge:
                    # Replace mode: delete old and create new
                    cache.delete_virtual_playlist(existing['id'])
                    playlist_id = cache.import_virtual_playlist(
                        name=name,
                        videos=videos,
                        source='takeout',
                        description=description
                    )
                    updated_count += 1
                    total_videos += len(videos)
                    status.update(f"Replaced {updated_count} playlists, imported {imported_count} new...")
                    
                    if verbose:
                        console.print(f"  ↻ {name}: replaced with {len(videos)} videos")
                        
                else:
                    # New playlist
                    playlist_id = cache.import_virtual_playlist(
                        name=name,
                        videos=videos,
                        source='takeout',
                        description=description
                    )
                    imported_count += 1
                    total_videos += len(videos)
                    
                    status.update(f"Imported {imported_count} playlists, {total_videos} videos...")
                    
                    if verbose:
                        console.print(f"  ✓ {name}: {len(videos)} videos")
                    
            except Exception as e:
                console.print(f"  [red]✗ Failed to import {name}: {e}[/red]")
    
    # Show summary
    console.print("\n[bold green]Import Complete![/bold green]")
    if imported_count > 0:
        console.print(f"  New playlists imported: {imported_count}")
    if updated_count > 0:
        console.print(f"  Existing playlists {'merged' if merge else 'replaced'}: {updated_count}")
    console.print(f"  Total videos processed: {total_videos}")
    
    # Special playlist highlights
    if 'Watch Later (Imported)' in all_playlists:
        wl_count = len(all_playlists['Watch Later (Imported)'].videos)
        console.print(f"  📌 Watch Later: {wl_count} videos")
    
    if 'History (Imported)' in all_playlists:
        hist_count = len(all_playlists['History (Imported)'].videos)
        console.print(f"  📜 History: {hist_count} videos")
    
    console.print("\n[dim]Virtual playlists are now available in yanger.[/dim]")
    console.print("[dim]You can copy videos from these to your YouTube playlists.[/dim]")
    
    # Suggest running deduplication if needed
    if not merge:
        console.print("\n[dim]Tip: Run 'yanger dedupe-virtual' to remove any duplicate playlists.[/dim]")


@cli.command()
@click.option('--format', '-f', type=click.Choice(['json', 'csv', 'yaml']), 
              default='json', help='Export format')
@click.option('--output', '-o', type=click.Path(), help='Output file path')
@click.option('--include-virtual/--no-virtual', default=True, 
              help='Include virtual playlists')
@click.option('--include-real/--no-real', default=True, 
              help='Include real YouTube playlists')
@click.option('-v', '--verbose', is_flag=True, help='Show detailed progress')
def export(format, output, include_virtual, include_real, verbose):
    """Export all playlists (real and virtual) to file.
    
    Examples:
        yanger export -o backup.json
        yanger export --format csv -o playlists/
        yanger export --no-real --format yaml
    """
    from datetime import datetime
    
    if verbose:
        setup_logging(verbose=True)
    
    console.print("[bold cyan]YouTube Playlist Exporter[/bold cyan]\n")
    
    # Determine output path
    if output:
        output_path = Path(output)
    else:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        if format == 'csv':
            output_path = Path(f'yanger_export_{timestamp}')
        else:
            output_path = Path(f'yanger_export_{timestamp}.{format}')
    
    # Initialize components
    cache = PersistentCache()
    api_client = None
    
    # Setup API client if exporting real playlists
    if include_real:
        try:
            auth = YouTubeAuth()
            auth.authenticate()
            api_client = YouTubeAPIClient(auth)
        except Exception as e:
            console.print(f"[yellow]Warning: Could not authenticate YouTube API: {e}[/yellow]")
            console.print("[yellow]Skipping real playlists...[/yellow]\n")
            include_real = False
    
    # Initialize exporter
    exporter = PlaylistExporter(api_client=api_client, cache=cache)
    
    # Export with progress
    with console.status("[bold green]Exporting playlists...") as status:
        try:
            stats = exporter.export_all(
                output_path=output_path,
                format=format,
                include_virtual=include_virtual,
                include_real=include_real
            )
            
            # Show results
            console.print("\n[bold green]Export Complete![/bold green]")
            console.print(f"  Output: {output_path}")
            
            if include_real:
                console.print(f"  Real playlists: {stats['real_playlist_count']}")
                console.print(f"  Real videos: {stats['total_real_videos']}")
            
            if include_virtual:
                console.print(f"  Virtual playlists: {stats['virtual_playlist_count']}")
                console.print(f"  Virtual videos: {stats['total_virtual_videos']}")
            
            # Show file size
            if output_path.exists():
                if output_path.is_file():
                    size_kb = output_path.stat().st_size / 1024
                    console.print(f"\n  File size: {size_kb:.1f} KB")
            
        except Exception as e:
            console.print(f"\n[red]Export failed: {e}[/red]")
            if verbose:
                console.print_exception()
            sys.exit(1)


@cli.command(name='dedupe-virtual')
@click.option('--dry-run', is_flag=True, help='Show what would be removed without making changes')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed progress')
def dedupe_virtual(dry_run, verbose):
    """Remove duplicate virtual playlists from database.
    
    Keeps the oldest version of each playlist and merges videos.
    """
    from rich.console import Console
    console = Console()
    
    console.print("\n[bold cyan]Virtual Playlist Deduplicator[/bold cyan]")
    
    try:
        from .cache import PersistentCache
        cache = PersistentCache()
        
        # Check for duplicates
        with cache.db_connection() as conn:
            cursor = conn.execute("""
                SELECT title, COUNT(*) as count
                FROM virtual_playlists
                WHERE is_active = 1
                GROUP BY title
                HAVING count > 1
            """)
            
            duplicates = cursor.fetchall()
            
            if not duplicates:
                console.print("[green]No duplicate playlists found![/green]")
                return
            
            # Show what will be removed
            console.print(f"\nFound [bold]{len(duplicates)}[/bold] playlists with duplicates:")
            total_duplicates = 0
            for title, count in duplicates:
                console.print(f"  - {title}: {count} copies ({count-1} will be removed)")
                total_duplicates += (count - 1)
            
            if dry_run:
                console.print(f"\n[yellow]Dry run - would remove {total_duplicates} duplicate playlists[/yellow]")
                return
            
            # Confirm
            if not click.confirm(f"\nRemove {total_duplicates} duplicate playlists?"):
                console.print("[yellow]Cancelled[/yellow]")
                return
            
            # Perform deduplication
            console.print("\n[bold]Deduplicating...[/bold]")
            removed = cache.deduplicate_virtual_playlists()
            
            console.print(f"\n[bold green]Success![/bold green]")
            console.print(f"Removed {removed} duplicate playlists")
            console.print("Videos have been merged into the remaining playlists")
            
            # Show final stats
            with cache.db_connection() as conn:
                cursor = conn.execute("""
                    SELECT COUNT(*) FROM virtual_playlists WHERE is_active = 1
                """)
                final_count = cursor.fetchone()[0]
                console.print(f"\nTotal virtual playlists now: {final_count}")
                
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command(name='fetch-metadata')
@click.option('--playlist', '-p', help='Virtual playlist name to fetch metadata for')
@click.option('--batch-size', '-b', default=50, help='Number of videos per API call (max 50)')
@click.option('--limit', '-l', type=int, help='Maximum number of videos to process')
@click.option('--since', '-s', help='Only fetch metadata for videos added after this date (YYYY-MM-DD)')
@click.option('--days-ago', '-d', type=int, help='Only fetch metadata for videos added in the last N days')
@click.option('--dry-run', is_flag=True, help='Show what would be fetched without making API calls')
@click.option('--verbose', '-v', is_flag=True, help='Show detailed progress')
def fetch_metadata(playlist, batch_size, limit, since, days_ago, dry_run, verbose):
    """Fetch video metadata from YouTube API for virtual playlists.
    
    Examples:
        yanger fetch-metadata --playlist "Watch Later (Imported)"
        yanger fetch-metadata --limit 100 --dry-run
        yanger fetch-metadata --since 2024-01-01
        yanger fetch-metadata --days-ago 30
    """
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
    from datetime import datetime, timedelta
    console = Console()
    
    console.print("\n[bold cyan]YouTube Video Metadata Fetcher[/bold cyan]")
    
    # Validate date options
    if since and days_ago:
        console.print("[red]Error: Cannot use both --since and --days-ago options[/red]")
        return
    
    # Parse date filters
    since_date = None
    if since:
        try:
            since_date = datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            console.print(f"[red]Invalid date format: {since}. Use YYYY-MM-DD[/red]")
            return
    elif days_ago:
        since_date = datetime.now() - timedelta(days=days_ago)
    
    try:
        # Initialize cache
        from .cache import PersistentCache
        cache = PersistentCache()
        
        # Get virtual playlists
        virtual_playlists = cache.get_virtual_playlists()
        
        if not virtual_playlists:
            console.print("[yellow]No virtual playlists found. Import data using 'yanger takeout' first.[/yellow]")
            return
        
        # Filter by playlist name if specified
        target_playlist_id = None
        if playlist:
            for vp in virtual_playlists:
                if vp['title'] == playlist:
                    target_playlist_id = vp['id']
                    break
            if not target_playlist_id:
                console.print(f"[red]Playlist '{playlist}' not found.[/red]")
                console.print("\nAvailable virtual playlists:")
                for vp in virtual_playlists:
                    console.print(f"  - {vp['title']} ({vp['video_count']} videos)")
                return
        
        # Get videos without metadata, with date filtering
        video_ids = cache.get_virtual_videos_without_metadata(
            playlist_id=target_playlist_id,
            limit=limit,
            since_date=since_date
        )
        
        if not video_ids:
            console.print("[green]All videos already have metadata![/green]")
            return
        
        # Calculate quota cost
        batch_size = min(batch_size, 50)  # YouTube API max
        num_batches = (len(video_ids) + batch_size - 1) // batch_size
        quota_cost = num_batches  # 1 quota unit per batch
        
        console.print(f"\nFound [bold]{len(video_ids)}[/bold] videos without metadata")
        if since_date:
            if since:
                console.print(f"Filtering videos added after: [bold]{since}[/bold]")
            else:
                console.print(f"Filtering videos added in the last [bold]{days_ago}[/bold] days")
        console.print(f"Will make [bold]{num_batches}[/bold] API calls (batches of {batch_size})")
        console.print(f"Estimated quota usage: [bold]{quota_cost}[/bold] units")
        
        if dry_run:
            console.print("\n[yellow]Dry run mode - no API calls will be made[/yellow]")
            if verbose:
                console.print("\nSample video IDs that would be processed:")
                for vid in video_ids[:10]:
                    console.print(f"  - {vid}")
                if len(video_ids) > 10:
                    console.print(f"  ... and {len(video_ids) - 10} more")
            return
        
        # Confirm with user
        if not click.confirm(f"\nProceed with fetching metadata? This will use {quota_cost} quota units"):
            console.print("[yellow]Cancelled[/yellow]")
            return
        
        # Initialize API client
        from .auth import YouTubeAuth
        from .api_client import YouTubeAPIClient
        
        console.print("\nAuthenticating...")
        auth = YouTubeAuth()
        auth.authenticate()
        api_client = YouTubeAPIClient(auth)
        
        # Fetch metadata with progress bar
        updated_count = 0
        failed_ids = []
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console
        ) as progress:
            task = progress.add_task("Fetching metadata...", total=len(video_ids))
            
            for i in range(0, len(video_ids), batch_size):
                batch = video_ids[i:i + batch_size]
                
                try:
                    # Fetch metadata from YouTube
                    videos_data = api_client.get_videos_by_ids(batch)
                    
                    # Update database
                    for video_data in videos_data:
                        if cache.update_virtual_video_metadata(video_data['video_id'], video_data):
                            updated_count += 1
                            if verbose:
                                console.print(f"  ✓ {video_data['title'][:60]}...")
                    
                    # Track videos that weren't found
                    found_ids = {v['video_id'] for v in videos_data}
                    for vid in batch:
                        if vid not in found_ids:
                            failed_ids.append(vid)
                            if verbose:
                                console.print(f"  ✗ {vid} - Video not found or private")
                    
                except Exception as e:
                    console.print(f"[red]Error fetching batch: {e}[/red]")
                    failed_ids.extend(batch)
                
                progress.update(task, advance=len(batch))
        
        # Summary
        console.print(f"\n[bold green]Metadata fetching complete![/bold green]")
        console.print(f"  Successfully updated: {updated_count} videos")
        if failed_ids:
            console.print(f"  Failed/not found: {len(failed_ids)} videos")
            if verbose and len(failed_ids) <= 20:
                console.print("\n  Failed video IDs:")
                for vid in failed_ids:
                    console.print(f"    - {vid}")
        
        console.print(f"\nQuota used: {quota_cost} units")
        console.print(f"Remaining quota: {api_client.get_quota_remaining()}/10000")
        
    except Exception as e:
        console.print(f"\n[red]Error: {e}[/red]")
        if verbose:
            console.print_exception()
        sys.exit(1)


if __name__ == '__main__':
    main()