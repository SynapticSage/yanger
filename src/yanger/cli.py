"""Command-line interface for YouTube Ranger.

Main entry point for the application.
"""
# Created: 2025-08-03

import sys
import json
import time
import shutil
import logging
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.logging import RichHandler

from . import __version__
from .auth import YouTubeAuth, resolve_token_file, config_dir
from .api_client import YouTubeAPIClient
from .cache import PersistentCache, default_cache_dir
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

    # Persist top-level flags so subcommands can read them (ctx.obj was only created
    # when --config-dir was passed, so `run`'s `ctx.obj.get('verbose')` was always
    # falsy — a dead flag). Always ensure the object and store verbose.
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose
    if config_dir:
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
@click.option('--client-secrets', type=click.Path(),
              default=None,
              help='Path to OAuth2 client secrets file (default: ~/.config/yanger/client_secret.json)')
@click.option('--token-file', type=click.Path(),
              default=None,
              help='Path to store authentication token (default: ~/.config/yanger/token.json)')
def auth(client_secrets: str, token_file: str):
    """Setup or test YouTube API authentication."""
    console.print("[yellow]YouTube API Authentication Setup[/yellow]\n")

    # Always WRITE the token to the canonical, cwd-independent location so the
    # MCP server (which may run from any directory) can find it. Reads elsewhere
    # still fall back to a legacy ./token.json for existing setups.
    if not token_file:
        token_file = str(config_dir() / "token.json")

    try:
        # Create auth handler (client_secrets=None resolves via the shared helper)
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
            # Report the RESOLVED path (the same one `yanger mcp` reads), not the
            # raw option value, so the documented auth->mcp flow is unambiguous.
            console.print(f"Token saved to: {auth_handler.token_file}")
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
        console.print("4. Download and save as: ~/.config/yanger/client_secret.json")
        console.print("   (a ./config/client_secret.json in the repo also works)")
        sys.exit(1)
    except Exception as e:
        console.print(f"\n[red]Error:[/red] {e}")
        console.print_exception()
        sys.exit(1)


@cli.command()
@click.option('--reset-token', is_flag=True, help='Remove stored authentication')
@click.option('--reset-cache', is_flag=True, help='Clear offline cache')
@click.option('--reset-config', is_flag=True, help='Reset to default configuration')
@click.option('-y', '--yes', is_flag=True, help='Skip confirmation prompts (for scripting)')
def reset(reset_token: bool, reset_cache: bool, reset_config: bool, yes: bool):
    """Reset various application data.

    Targets the REAL paths yanger uses: the token via the shared resolver, the cache
    at ~/.cache/yanger, and the user config at ~/.config/yanger/config.yaml. Each
    destructive action prompts for confirmation unless --yes is given.
    """
    if not any([reset_token, reset_cache, reset_config]):
        console.print("Nothing to reset. Use --help to see options.")
        return

    if reset_token:
        # Use the shared resolver so we remove the token that auth/mcp actually use.
        token_file = resolve_token_file()
        if token_file.exists():
            if yes or click.confirm(f"Remove authentication token ({token_file})?"):
                token_file.unlink()
                console.print(f"[green]✓[/green] Removed authentication token ({token_file})")
        else:
            console.print(f"No token file found ({token_file})")

    if reset_cache:
        # default_cache_dir() is the same resolver PersistentCache uses, so this can
        # never target a stale path (previously removed a nonexistent ./.yanger_cache).
        cache_dir = default_cache_dir()
        if cache_dir.exists():
            if yes or click.confirm(f"Delete offline cache ({cache_dir})? This cannot be undone."):
                import shutil
                shutil.rmtree(cache_dir)
                console.print(f"[green]✓[/green] Cleared cache directory ({cache_dir})")
        else:
            console.print(f"No cache directory found ({cache_dir})")

    if reset_config:
        # The real user config is ~/.config/yanger/config.yaml (settings.py), not the
        # repo-relative config/*.yaml the old code targeted.
        config_file = config_dir() / "config.yaml"
        if config_file.exists():
            if yes or click.confirm(f"Remove user config ({config_file})?"):
                config_file.unlink()
                console.print(f"[green]✓[/green] Removed {config_file}")
        else:
            console.print(f"No user config found ({config_file})")


@cli.command()
def quota():
    """Check current API quota usage."""
    try:
        # Setup authentication
        auth_handler = YouTubeAuth()
        auth_handler.authenticate()
        
        # Create API client (share the cross-process quota counter via the cache)
        client = YouTubeAPIClient(auth_handler, quota_store=PersistentCache())

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
@click.argument('paths', nargs=-1, required=False, type=click.Path(exists=True))
@click.option('--merge/--replace', default=True, help='Merge with existing virtual playlists')
@click.option('-v', '--verbose', is_flag=True, help='Show detailed progress')
@click.pass_context
def takeout(ctx, paths, merge, verbose):
    """Import YouTube data from Google Takeout.

    Accepts multiple paths (zip files or directories). With no paths given,
    offers to fetch a fresh export through your browser via `yanger sync`.
    Creates virtual playlists for Watch Later and History.

    Examples:
        yanger takeout ~/Downloads/takeout.zip
        yanger takeout Takeout-1/ Takeout-2/ --merge
        yanger takeout                       # no file → guided download
    """
    if verbose:
        setup_logging(verbose=True)

    console.print("[bold cyan]YouTube Takeout Importer[/bold cyan]")

    # No artifacts provided → hand off to the guided browser download flow so the
    # user never hits a bare "Missing argument" error and a dead end.
    if not paths:
        console.print("No takeout files provided.\n")
        if click.confirm(
            "Fetch a fresh export now through your browser (yanger sync)?",
            default=True,
        ):
            ctx.invoke(sync, merge=merge, verbose=verbose)
        else:
            console.print("\nProvide a Takeout zip or folder, e.g.:")
            console.print("  [dim]yanger takeout ~/Downloads/takeout.zip[/dim]")
            console.print("Or run [dim]yanger sync[/dim] to download one interactively.")
        return

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


# --- YouTube data sync (Puppeteer-assisted Takeout) -------------------------
# The routine lives in scripts/takeout-refresh/ (Node). It attaches to a Chrome
# the user is already logged into and drives Google Takeout — we never automate
# credentials, which keeps us on Google's sanctioned data-portability path.

SYNC_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "takeout-refresh"


def _devtools_up(port: int) -> bool:
    """Return True if a Chrome DevTools endpoint is reachable on `port`."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=2
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _find_chrome() -> Optional[str]:
    """Locate a Chrome/Chromium binary (macOS app bundles first, then PATH)."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _launch_chrome(port: int, profile_dir: Path) -> bool:
    """Launch Chrome with a debug port on a DEDICATED profile.

    Chrome 136+ ignores --remote-debugging-port on the *default* profile, so we
    point at an isolated user-data-dir (also keeps it apart from normal browsing).
    Returns True once the DevTools endpoint comes up.
    """
    chrome = _find_chrome()
    if not chrome:
        console.print("[red]Could not find Google Chrome.[/red] Start it manually with:")
        console.print(
            f"  [dim]<chrome> --remote-debugging-port={port} "
            f"--user-data-dir={profile_dir} https://takeout.google.com/[/dim]"
        )
        return False
    profile_dir.mkdir(parents=True, exist_ok=True)
    console.print(
        f"[cyan]Launching Chrome[/cyan] (debug port {port}, profile {profile_dir})"
    )
    console.print("[dim]First run: sign into Google in the new window.[/dim]")
    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://takeout.google.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        if _devtools_up(port):
            return True
        time.sleep(1)
    return False


def _ensure_node_deps() -> bool:
    """Install the routine's npm deps on first run. Returns True when ready."""
    if not (SYNC_SCRIPT / "refresh.js").exists():
        console.print(f"[red]Sync routine not found at {SYNC_SCRIPT}[/red]")
        return False
    if (SYNC_SCRIPT / "node_modules").exists():
        return True
    if not shutil.which("npm"):
        console.print("[red]npm not found.[/red] Install Node.js to use `yanger sync`.")
        return False
    console.print("[cyan]Installing routine dependencies (first run)…[/cyan]")
    return subprocess.run(["npm", "install"], cwd=SYNC_SCRIPT).returncode == 0


def _last_json_line(text: str) -> Optional[dict]:
    """Parse the final JSON object the Node routine prints on stdout."""
    for line in reversed((text or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _print_finish_later(downloads: Path) -> None:
    """Explain how to finish the import from the email link.

    Google generates the export asynchronously and it routinely takes longer
    than we're willing to block the terminal — so a timeout is expected, not a
    failure. These instructions make the email link a first-class resume path.
    """
    console.print(
        "\n[bold]The export is still being generated on Google's side.[/bold] "
        "This is normal — it can take from a few minutes to several hours, so you "
        "do not need to keep this open."
    )
    console.print(
        "\nWhen the [bold]“Your Google data is ready”[/bold] email arrives "
        "(the link stays valid ~1 week):"
    )
    console.print("  1. Open it and click [bold]Download your files[/bold]; save the zip anywhere.")
    console.print("  2. Finish the import by pointing yanger at that zip:")
    console.print("       [dim]yanger takeout ~/Downloads/takeout-XXXXXXXX.zip[/dim]")
    console.print(
        f"\n[dim]Tip: if you save it into {downloads}, a later "
        f"`yanger sync` will auto-detect it.[/dim]"
    )


@cli.command()
@click.option('--debug-port', default=9222, show_default=True,
              help='Chrome remote-debugging port to attach to.')
@click.option('--profile-dir', type=click.Path(), default=None,
              help='Dedicated Chrome profile (default: ~/.yanger/chrome-profile).')
@click.option('--download-dir', type=click.Path(), default=None,
              help='Where Takeout zips land (default: ~/.yanger/takeout-downloads).')
@click.option('--start-chrome/--no-start-chrome', default=True,
              help='Launch Chrome with the debug port if not already running.')
@click.option('--wait-minutes', default=20, show_default=True,
              help='How long to watch for the export download (0 = configure only).')
@click.option('--merge/--replace', default=True,
              help='Merge with existing virtual playlists when importing.')
@click.option('-v', '--verbose', is_flag=True, help='Show detailed progress.')
@click.pass_context
def sync(ctx, debug_port, profile_dir, download_dir, start_chrome,
         wait_minutes, merge, verbose):
    """Sync your YouTube data (history, playlists, Watch Later) from Google Takeout.

    Brings your local copy up to date by driving Google Takeout in your own
    browser: attaches to a Chrome started with --remote-debugging-port,
    pre-configures a YouTube-only export, waits for you to click "Create export",
    then downloads and imports the result. Your credentials are never automated —
    you stay signed into your own session (Google's sanctioned Takeout path).

    Examples:
        yanger sync
        yanger sync --wait-minutes 0     # configure now, import later
    """
    if verbose:
        setup_logging(verbose=True)

    profile = Path(profile_dir) if profile_dir else Path.home() / ".yanger" / "chrome-profile"
    downloads = Path(download_dir) if download_dir else Path.home() / ".yanger" / "takeout-downloads"
    downloads.mkdir(parents=True, exist_ok=True)

    console.print("[bold cyan]YouTube Data Sync[/bold cyan]")

    # 1. Ensure a debuggable Chrome is reachable.
    if _devtools_up(debug_port):
        console.print(f"[green]Attached[/green] to Chrome on port {debug_port}")
    elif start_chrome:
        if not _launch_chrome(debug_port, profile):
            console.print("[red]Chrome debug endpoint never came up.[/red]")
            sys.exit(1)
    else:
        console.print(f"[red]No Chrome DevTools endpoint on port {debug_port}.[/red]")
        console.print("Start Chrome with --remote-debugging-port, or drop --no-start-chrome.")
        sys.exit(1)

    # 2. Ensure the Node routine can run.
    if not shutil.which("node"):
        console.print("[red]node not found.[/red] Install Node.js to use `yanger sync`.")
        sys.exit(1)
    if not _ensure_node_deps():
        sys.exit(1)

    # 3. Drive the browser. stdout is captured for the JSON result; stderr/stdin
    #    stay attached to the terminal so the routine can prompt the user live.
    cmd = [
        "node", str(SYNC_SCRIPT / "refresh.js"),
        "--browser-url", f"http://127.0.0.1:{debug_port}",
        "--download-dir", str(downloads),
        "--wait-minutes", str(wait_minutes),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    result = _last_json_line(proc.stdout)
    status = result.get("status") if result else None

    # 4. Act on the routine's outcome.
    if status == "downloaded":
        zip_path = result["zipPath"]
        console.print(f"\n[green]Downloaded:[/green] {zip_path}")
        console.print("[cyan]Importing into yanger…[/cyan]\n")
        ctx.invoke(takeout, paths=(zip_path,), merge=merge, verbose=verbose)
    elif status == "configured":
        console.print("\n[green]Export request submitted.[/green]")
        _print_finish_later(downloads)
    elif status == "timeout":
        console.print("\n[yellow]Stopped waiting — the export wasn't ready in time.[/yellow]")
        _print_finish_later(downloads)
    elif status == "aborted":
        console.print("\n[yellow]Aborted.[/yellow]")
    else:
        msg = result.get("message") if result else "no result from routine"
        console.print(f"\n[red]Sync failed:[/red] {msg}")
        sys.exit(1)


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
            api_client = YouTubeAPIClient(auth, quota_store=PersistentCache())
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
        sys.exit(1)
    
    # Parse date filters
    since_date = None
    if since:
        try:
            since_date = datetime.strptime(since, "%Y-%m-%d")
        except ValueError:
            console.print(f"[red]Invalid date format: {since}. Use YYYY-MM-DD[/red]")
            sys.exit(1)
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
                sys.exit(1)
        
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
        # `cache` (created above) backs the shared, cross-process quota counter.
        api_client = YouTubeAPIClient(auth, quota_store=cache)

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


@cli.group()
def proxy():
    """Manage proxy settings for transcript fetching.

    YouTube may block transcript requests from certain IPs.
    Configure a proxy to work around these blocks.
    """
    pass


@proxy.command(name='status')
def proxy_status():
    """Show current proxy configuration."""
    from .config.settings import load_settings
    from .core.proxy import ProxySettings as CoreProxySettings

    settings = load_settings()
    proxy_cfg = settings.transcripts.proxy

    console.print("\n[bold]Proxy Configuration[/bold]")
    console.print("-" * 40)
    console.print(f"Enabled: {'[green]Yes[/green]' if proxy_cfg.enabled else '[yellow]No[/yellow]'}")
    console.print(f"Type: {proxy_cfg.type}")

    if proxy_cfg.type == "webshare":
        console.print(f"Webshare User: {proxy_cfg.webshare_username or '[dim]not set[/dim]'}")
        console.print(f"Webshare Pass: {'***' if proxy_cfg.webshare_password else '[dim]not set[/dim]'}")
        if proxy_cfg.webshare_locations:
            console.print(f"Locations: {', '.join(proxy_cfg.webshare_locations)}")
    else:
        console.print(f"HTTP URL: {proxy_cfg.http_url or '[dim]not set[/dim]'}")
        # Mask password in HTTPS URL
        https_display = proxy_cfg.https_url
        if https_display and '@' in https_display:
            parts = https_display.split('@')
            https_display = f"***@{parts[-1]}"
        console.print(f"HTTPS URL: {https_display or '[dim]not set[/dim]'}")

    console.print("\n[dim]Environment variables:[/dim]")
    console.print("  YANGER_PROXY_URL, YANGER_PROXY_HTTP, YANGER_PROXY_HTTPS")
    console.print("  YANGER_WEBSHARE_USER, YANGER_WEBSHARE_PASS")


@proxy.command(name='test')
@click.option('--video-id', '-v', default='dQw4w9WgXcQ',
              help='Video ID to test with (default: Rick Astley)')
def proxy_test(video_id: str):
    """Test proxy connection by fetching a transcript."""
    from .config.settings import load_settings
    from .core.proxy import ProxySettings as CoreProxySettings, test_proxy_connection

    console.print("\n[bold]Testing Proxy Connection[/bold]")
    console.print("-" * 40)

    settings = load_settings()
    proxy_cfg = settings.transcripts.proxy

    # Convert to core ProxySettings
    core_proxy = CoreProxySettings(
        enabled=proxy_cfg.enabled,
        type=proxy_cfg.type,
        http_url=proxy_cfg.http_url,
        https_url=proxy_cfg.https_url,
        webshare_username=proxy_cfg.webshare_username,
        webshare_password=proxy_cfg.webshare_password,
        webshare_locations=proxy_cfg.webshare_locations,
    )

    console.print(f"Proxy: {core_proxy.get_display_info()}")
    console.print(f"Test video: {video_id}")
    console.print()

    with console.status("Fetching transcript..."):
        result = test_proxy_connection(core_proxy, video_id)

    if result["success"]:
        console.print(f"[green]SUCCESS[/green] - Fetched {result['transcript_length']} segments")
    else:
        console.print(f"[red]FAILED[/red] - {result['error']}")

        if "blocking" in str(result.get("error", "")).lower():
            console.print("\n[yellow]YouTube is blocking requests.[/yellow]")
            console.print("Try configuring a proxy with: yanger proxy set --help")

        # Non-zero exit so scripts/CI can detect a failed proxy test (was exit 0).
        sys.exit(1)


@proxy.command(name='set')
@click.option('--type', 'proxy_type', type=click.Choice(['generic', 'webshare']),
              help='Proxy type')
@click.option('--url', 'https_url', help='HTTPS proxy URL (generic type)')
@click.option('--http-url', help='HTTP proxy URL (generic type)')
@click.option('--webshare-user', help='Webshare username')
@click.option('--webshare-pass', help='Webshare password')
@click.option('--locations', help='Webshare IP locations (comma-separated, e.g., us,de)')
@click.option('--enable/--disable', default=None, help='Enable or disable proxy')
def proxy_set(proxy_type, https_url, http_url, webshare_user, webshare_pass, locations, enable):
    """Configure proxy settings.

    Examples:

        # Set generic HTTPS proxy
        yanger proxy set --type generic --url https://user:pass@proxy:8080 --enable

        # Set Webshare rotating proxy
        yanger proxy set --type webshare --webshare-user myuser --webshare-pass mypass --enable

        # Disable proxy
        yanger proxy set --disable
    """
    from .config.settings import load_settings, save_settings

    settings = load_settings()
    proxy_cfg = settings.transcripts.proxy
    changed = False

    if enable is not None:
        proxy_cfg.enabled = enable
        changed = True

    if proxy_type:
        proxy_cfg.type = proxy_type
        changed = True

    if https_url:
        proxy_cfg.https_url = https_url
        proxy_cfg.type = "generic"
        changed = True

    if http_url:
        proxy_cfg.http_url = http_url
        changed = True

    if webshare_user:
        proxy_cfg.webshare_username = webshare_user
        proxy_cfg.type = "webshare"
        changed = True

    if webshare_pass:
        proxy_cfg.webshare_password = webshare_pass
        changed = True

    if locations:
        proxy_cfg.webshare_locations = [loc.strip() for loc in locations.split(',')]
        changed = True

    if changed:
        save_settings(settings)
        console.print("[green]Proxy settings updated.[/green]")
        console.print(f"Enabled: {proxy_cfg.enabled}")
        console.print(f"Type: {proxy_cfg.type}")
    else:
        console.print("[yellow]No changes made.[/yellow] Use --help for options.")


@cli.command()
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
def mcp(verbose):
    """Start the MCP (Model Context Protocol) server.

    Exposes yanger's playlist management capabilities to Claude
    and other MCP-compatible tools via stdio.

    Example Claude Code configuration:
        {
            "mcpServers": {
                "yanger": {
                    "command": "yanger",
                    "args": ["mcp"]
                }
            }
        }
    """
    try:
        from .mcp_server import main as mcp_main, MCP_AVAILABLE

        if not MCP_AVAILABLE:
            console.print("[red]Error:[/red] MCP package not installed.")
            console.print("\nInstall with: [bold]pip install 'yanger[mcp]'[/bold]")
            sys.exit(1)

        if verbose:
            setup_logging(verbose=True)

        mcp_main()

    except ImportError as e:
        console.print(f"[red]Error:[/red] Missing dependencies: {e}")
        console.print("\nInstall MCP support with: [bold]pip install 'yanger[mcp]'[/bold]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        if verbose:
            console.print_exception()
        sys.exit(1)


if __name__ == '__main__':
    main()