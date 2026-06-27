"""OAuth2 authentication handler for YouTube Data API.

Handles the OAuth2 flow, token storage, and refresh.
"""
# Created: 2025-08-03

import os
import json
import logging
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Log to stderr (logging's default). NEVER print() here: this module is reachable
# from the MCP stdio server, where stdout is the JSON-RPC channel.
logger = logging.getLogger(__name__)

# Single source of truth for where credentials live. The TUI, CLI and MCP server
# must all agree, or `yanger auth` writes a token the MCP server can't find.
def config_dir() -> Path:
    """User config dir, resolved at call time (honors $HOME changes and tests)."""
    return Path.home() / ".config" / "yanger"


def resolve_token_file(configured: Optional[str] = None) -> Path:
    """Resolve the OAuth token path used everywhere (cwd-independent).

    An absolute configured path wins; a relative one is anchored under the
    config dir (never the arbitrary process cwd). When unset, prefer the
    canonical ``~/.config/yanger/token.json`` but fall back to a legacy
    ``./token.json`` if one already exists, so existing setups keep working.
    """
    if configured:
        p = Path(configured).expanduser()
        return p if p.is_absolute() else config_dir() / p
    canonical = config_dir() / "token.json"
    if canonical.exists():
        return canonical
    legacy = Path.cwd() / "token.json"
    return legacy if legacy.exists() else canonical


def resolve_client_secrets_file(configured: Optional[str] = None) -> Path:
    """Resolve the OAuth client-secrets path (same rules as the token).

    Falls back to the legacy ``config/client_secret.json`` / ``./client_secret.json``
    locations if the canonical one is absent, so existing checkouts keep working.
    """
    if configured:
        p = Path(configured).expanduser()
        return p if p.is_absolute() else config_dir() / p
    canonical = config_dir() / "client_secret.json"
    for candidate in (canonical,
                      Path.cwd() / "config" / "client_secret.json",
                      Path.cwd() / "client_secret.json"):
        if candidate.exists():
            return candidate
    return canonical


class YouTubeAuth:
    """Handle YouTube API OAuth2 authentication."""
    
    # API settings
    API_SERVICE_NAME = 'youtube'
    API_VERSION = 'v3'
    
    # OAuth2 scopes needed for playlist management
    SCOPES = [
        'https://www.googleapis.com/auth/youtube',  # Full access to manage playlists
    ]
    
    def __init__(self,
                 client_secrets_file: Optional[str] = None,
                 token_file: Optional[str] = None):
        """Initialize the authentication handler.

        Args:
            client_secrets_file: OAuth2 client secrets path (None = resolve to
                the shared canonical location, with legacy fallbacks).
            token_file: Token storage path (None = resolve to the shared
                canonical location, with legacy fallback).
        """
        # Resolve through the shared helpers so the CLI, TUI and MCP server all
        # read/write the same files regardless of the process working directory.
        self.client_secrets_file = str(resolve_client_secrets_file(client_secrets_file))
        self.token_file = str(resolve_token_file(token_file))
        self.creds: Optional[Credentials] = None
        self.youtube = None
        
    def authenticate(self) -> None:
        """Perform OAuth2 authentication flow if needed."""
        # Load existing token if available
        if os.path.exists(self.token_file):
            # A corrupt/truncated token.json must not hard-crash; fall through to
            # the normal OAuth flow instead (mirrors refresh-failure recovery below).
            try:
                self.creds = Credentials.from_authorized_user_file(
                    self.token_file, self.SCOPES
                )
            except (ValueError, json.JSONDecodeError, OSError, KeyError) as e:
                logger.warning("Could not load token file %s: %s", self.token_file, e)
                logger.warning("Removing invalid token and starting new authentication...")
                os.remove(self.token_file)
                self.creds = None

        # Check if credentials are valid
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                # Try to refresh the token
                try:
                    logger.info("Refreshing authentication token...")
                    self.creds.refresh(Request())
                except Exception as e:
                    # Token refresh failed - likely revoked or expired
                    logger.warning("Token refresh failed: %s", e)
                    logger.warning("Removing invalid token and starting new authentication...")

                    # Delete the invalid token file
                    if os.path.exists(self.token_file):
                        os.remove(self.token_file)

                    # Clear credentials to trigger new OAuth flow
                    self.creds = None
            
            # If still no valid credentials, start OAuth flow
            if not self.creds or not self.creds.valid:
                # Perform OAuth2 flow
                if not os.path.exists(self.client_secrets_file):
                    raise FileNotFoundError(
                        f"Client secrets file not found: {self.client_secrets_file}\n"
                        "Please download your OAuth2 credentials from Google Cloud Console "
                        "and save them to this location."
                    )
                
                logger.info("Starting OAuth2 authentication flow...")
                logger.info("A browser window will open for authentication.")
                
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.client_secrets_file, self.SCOPES
                )
                self.creds = flow.run_local_server(
                    port=0,
                    success_message="Authentication successful! You can close this window."
                )
            
            # Save credentials for next run
            self._save_credentials()
            
    def get_youtube_service(self):
        """Get authenticated YouTube API service object.
        
        Returns:
            googleapiclient.discovery.Resource: YouTube API service object
        """
        if not self.creds:
            self.authenticate()
            
        if not self.youtube:
            self.youtube = build(
                self.API_SERVICE_NAME,
                self.API_VERSION,
                credentials=self.creds
            )
            
        return self.youtube
    
    def _save_credentials(self) -> None:
        """Save credentials to token file."""
        if self.creds:
            # Ensure directory exists
            token_path = Path(self.token_file)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Save credentials
            with open(self.token_file, 'w') as token:
                token.write(self.creds.to_json())
                
            logger.info("Authentication token saved to: %s", self.token_file)
            
    def revoke_credentials(self) -> None:
        """Revoke stored credentials and delete token file."""
        if os.path.exists(self.token_file):
            os.remove(self.token_file)
            logger.info("Removed token file: %s", self.token_file)

        if self.creds and self.creds.valid:
            try:
                self.creds.revoke(Request())
                logger.info("Successfully revoked credentials")
            except Exception as e:
                logger.error("Error revoking credentials: %s", e)
                
        self.creds = None
        self.youtube = None
        
    def test_authentication(self) -> bool:
        """Test if authentication is working.
        
        Returns:
            bool: True if authentication is successful
        """
        try:
            youtube = self.get_youtube_service()
            
            # Try to get user's channel info as a test
            request = youtube.channels().list(
                part="snippet",
                mine=True
            )
            response = request.execute()
            
            if response.get('items'):
                channel = response['items'][0]['snippet']
                logger.info("Authenticated as: %s", channel.get('title', 'Unknown'))
                return True
            else:
                logger.info("Authentication successful but no channel found")
                return True

        except HttpError as e:
            logger.error("Authentication test failed: %s", e)
            return False
        except Exception as e:
            logger.error("Unexpected error during authentication test: %s", e)
            return False


def setup_auth() -> YouTubeAuth:
    """Convenience function to setup authentication.
    
    Returns:
        YouTubeAuth: Initialized and authenticated auth handler
    """
    auth = YouTubeAuth()
    auth.authenticate()
    return auth


if __name__ == "__main__":
    # Interactive CLI test: surface info logs to stderr so the auth UX is readable.
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    logger.info("Testing YouTube API authentication...")
    auth = setup_auth()

    if auth.test_authentication():
        logger.info("Authentication successful! You're ready to use YouTube Ranger.")
    else:
        logger.error("Authentication failed. Please check your credentials.")