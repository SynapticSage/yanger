"""Caching system for YouTube Ranger.

Caches playlist video lists to avoid redundant API calls during navigation.
"""
# Created: 2025-08-07

import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import logging

from .models import Video


logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry with TTL."""
    videos: List[Video]
    timestamp: float
    hits: int = 0


class PlaylistCache:
    """In-memory cache for playlist videos with TTL support."""
    
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 50):
        """Initialize cache.
        
        Args:
            ttl_seconds: Time-to-live in seconds (default: 5 minutes)
            max_entries: Maximum cache entries before LRU eviction
        """
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._cache: Dict[str, CacheEntry] = {}
        self._access_order: List[str] = []  # For LRU tracking
        
    def get(self, playlist_id: str) -> Optional[List[Video]]:
        """Get videos from cache if fresh.
        
        Args:
            playlist_id: ID of the playlist
            
        Returns:
            List of videos if cached and fresh, None otherwise
        """
        if playlist_id not in self._cache:
            return None
            
        entry = self._cache[playlist_id]
        current_time = time.time()
        
        # Check if expired
        if current_time - entry.timestamp > self.ttl_seconds:
            logger.debug(f"Cache expired for playlist {playlist_id}")
            del self._cache[playlist_id]
            self._access_order.remove(playlist_id)
            return None
            
        # Update access tracking
        entry.hits += 1
        self._update_access_order(playlist_id)
        
        logger.debug(
            f"Cache hit for playlist {playlist_id} "
            f"(hits: {entry.hits}, age: {current_time - entry.timestamp:.1f}s)"
        )
        return entry.videos.copy()  # Return copy to prevent modification
        
    def set(self, playlist_id: str, videos: List[Video]) -> None:
        """Store videos in cache.
        
        Args:
            playlist_id: ID of the playlist
            videos: List of videos to cache
        """
        # Check if we need to evict
        if len(self._cache) >= self.max_entries and playlist_id not in self._cache:
            self._evict_lru()
            
        # Store the entry
        self._cache[playlist_id] = CacheEntry(
            videos=videos.copy(),  # Store copy
            timestamp=time.time()
        )
        self._update_access_order(playlist_id)
        
        logger.debug(f"Cached {len(videos)} videos for playlist {playlist_id}")
        
    def invalidate(self, playlist_id: str) -> None:
        """Invalidate cache entry for a playlist.
        
        Args:
            playlist_id: ID of the playlist to invalidate
        """
        if playlist_id in self._cache:
            del self._cache[playlist_id]
            self._access_order.remove(playlist_id)
            logger.debug(f"Invalidated cache for playlist {playlist_id}")
            
    def invalidate_all(self) -> None:
        """Clear entire cache."""
        self._cache.clear()
        self._access_order.clear()
        logger.debug("Cleared entire cache")
        
    def get_stats(self) -> Dict[str, any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        total_hits = sum(entry.hits for entry in self._cache.values())
        total_videos = sum(len(entry.videos) for entry in self._cache.values())
        
        return {
            "entries": len(self._cache),
            "total_hits": total_hits,
            "total_videos_cached": total_videos,
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries
        }
        
    def _update_access_order(self, playlist_id: str) -> None:
        """Update LRU access order."""
        if playlist_id in self._access_order:
            self._access_order.remove(playlist_id)
        self._access_order.append(playlist_id)
        
    def _evict_lru(self) -> None:
        """Evict least recently used entry."""
        if self._access_order:
            lru_id = self._access_order[0]
            del self._cache[lru_id]
            self._access_order.pop(0)
            logger.debug(f"Evicted LRU cache entry: {lru_id}")


class OfflineCache:
    """Persistent cache for offline browsing (future enhancement)."""
    
    def __init__(self, cache_dir: str):
        """Initialize offline cache.
        
        Args:
            cache_dir: Directory for cache storage
        """
        # TODO: Implement SQLite-based offline cache
        self.cache_dir = cache_dir
        logger.info("Offline cache not yet implemented")