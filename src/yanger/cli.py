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


if __name__ == '__main__':
    main()