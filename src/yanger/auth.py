"""OAuth2 authentication handler for YouTube Data API.

Handles the OAuth2 flow, token storage, and refresh.
"""
# Created: 2025-08-03

import os
import json
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


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
                 client_secrets_file: str = 'config/client_secret.json',
                 token_file: str = 'token.json'):
        """Initialize the authentication handler.
        
        Args:
            client_secrets_file: Path to OAuth2 client secrets JSON file
            token_file: Path to store/load authentication tokens
        """
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file
        self.creds: Optional[Credentials] = None
        self.youtube = None
        
    def authenticate(self) -> None:
        """Perform OAuth2 authentication flow if needed."""
        # Load existing token if available
        if os.path.exists(self.token_file):
            self.creds = Credentials.from_authorized_user_file(
                self.token_file, self.SCOPES
            )
        
        # Check if credentials are valid
        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                # Refresh the token
                print("Refreshing authentication token...")
                self.creds.refresh(Request())
            else:
                # Perform OAuth2 flow
                if not os.path.exists(self.client_secrets_file):
                    raise FileNotFoundError(
                        f"Client secrets file not found: {self.client_secrets_file}\n"
                        "Please download your OAuth2 credentials from Google Cloud Console "
                        "and save them to this location."
                    )
                
                print("Starting OAuth2 authentication flow...")
                print("A browser window will open for authentication.")
                
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
                
            print(f"Authentication token saved to: {self.token_file}")
            
    def revoke_credentials(self) -> None:
        """Revoke stored credentials and delete token file."""
        if os.path.exists(self.token_file):
            os.remove(self.token_file)
            print(f"Removed token file: {self.token_file}")
            
        if self.creds and self.creds.valid:
            try:
                self.creds.revoke(Request())
                print("Successfully revoked credentials")
            except Exception as e:
                print(f"Error revoking credentials: {e}")
                
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
                print(f"Authenticated as: {channel.get('title', 'Unknown')}")
                return True
            else:
                print("Authentication successful but no channel found")
                return True
                
        except HttpError as e:
            print(f"Authentication test failed: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error during authentication test: {e}")
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
    # Test authentication when run directly
    print("Testing YouTube API authentication...")
    auth = setup_auth()
    
    if auth.test_authentication():
        print("\nAuthentication successful! You're ready to use YouTube Ranger.")
    else:
        print("\nAuthentication failed. Please check your credentials.")