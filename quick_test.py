#!/usr/bin/env python3
"""Quick test after enabling YouTube Data API v3."""
# Created: 2025-08-03

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'src'))

from yanger.auth import YouTubeAuth
from yanger.api_client import YouTubeAPIClient

print("Quick YouTube Ranger Test")
print("========================\n")

try:
    # Test auth
    print("1. Testing authentication...")
    auth = YouTubeAuth()
    
    if auth.test_authentication():
        print("✅ Authentication successful!\n")
    else:
        print("❌ Authentication failed\n")
        sys.exit(1)
    
    # Test API
    print("2. Testing API access...")
    client = YouTubeAPIClient(auth)
    
    print("3. Fetching your playlists...")
    playlists = client.get_playlists()
    
    print(f"\n✅ Success! Found {len(playlists)} playlists:")
    for i, playlist in enumerate(playlists[:5], 1):
        print(f"   {i}. {playlist.title} ({playlist.item_count} videos)")
    
    if len(playlists) > 5:
        print(f"   ... and {len(playlists) - 5} more")
    
    print(f"\nQuota used: {client.quota_used} units")
    print(f"Remaining: {client.get_quota_remaining()} units")
    
    print("\n🎉 Everything is working! You can now run: yanger")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    print("\nMake sure you've enabled the YouTube Data API v3 in Google Cloud Console")
    sys.exit(1)