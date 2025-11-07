#!/usr/bin/env python3
"""Quick test script to verify authentication and basic API functionality."""
# Created: 2025-08-03

import sys
from pathlib import Path

# Add src to path for development
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from yanger.auth import YouTubeAuth
from yanger.api_client import YouTubeAPIClient


def main():
    print("Testing YouTube Ranger Authentication...\n")
    
    try:
        # Test authentication
        auth = YouTubeAuth()
        auth.authenticate()
        
        if auth.test_authentication():
            print("✓ Authentication successful!\n")
        else:
            print("✗ Authentication failed\n")
            return 1
        
        # Test API client
        client = YouTubeAPIClient(auth)
        
        print("Fetching playlists...")
        playlists = client.get_playlists()
        
        print(f"\nFound {len(playlists)} playlists:")
        for i, playlist in enumerate(playlists[:5]):  # Show first 5
            print(f"  {i+1}. {playlist.title} ({playlist.item_count} videos)")
        
        if len(playlists) > 5:
            print(f"  ... and {len(playlists) - 5} more")
        
        print(f"\nQuota used: {client.quota_used}")
        print(f"Quota remaining: {client.get_quota_remaining()}")
        
        return 0
        
    except Exception as e:
        print(f"\nError: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())