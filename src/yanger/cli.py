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
@click.pass_context
def run(ctx: click.Context, no_cache: bool):
    """Run the YouTube Ranger TUI application."""
    try:
        # Import here to avoid circular imports and defer heavy imports
        from .app import YouTubeRangerApp
        
        # Get config directory from context
        config_dir = ctx.obj.get('config_dir') if ctx.obj else None
        
        # Create and run the app
        app = YouTubeRangerApp(
            config_dir=config_dir,
            use_cache=not no_cache
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
            
            # Import to database
            try:
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
    console.print(f"  Imported playlists: {imported_count}")
    console.print(f"  Total videos: {total_videos}")
    
    # Special playlist highlights
    if 'Watch Later (Imported)' in all_playlists:
        wl_count = len(all_playlists['Watch Later (Imported)'].videos)
        console.print(f"  📌 Watch Later: {wl_count} videos")
    
    if 'History (Imported)' in all_playlists:
        hist_count = len(all_playlists['History (Imported)'].videos)
        console.print(f"  📜 History: {hist_count} videos")
    
    console.print("\n[dim]Virtual playlists are now available in yanger.[/dim]")
    console.print("[dim]You can copy videos from these to your YouTube playlists.[/dim]")


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


if __name__ == '__main__':
    main()