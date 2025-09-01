"""Caching system for YouTube Ranger.

Provides persistent caching of playlist and video data using SQLite.
"""
# Modified: 2025-08-08

import sqlite3
import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
import logging

from .models import Video, Playlist, PrivacyStatus


logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry with TTL."""
    videos: List[Video]
    timestamp: float
    hits: int = 0


class PersistentCache:
    """SQLite-based persistent cache for playlists and videos."""
    
    SCHEMA_VERSION = 1
    
    def __init__(self, cache_dir: Optional[Path] = None, 
                 ttl_days: int = 7,
                 auto_cleanup: bool = True):
        """Initialize persistent cache.
        
        Args:
            cache_dir: Directory for cache database (default: ~/.cache/yanger)
            ttl_days: Time-to-live in days (default: 7)
            auto_cleanup: Automatically clean expired entries (default: True)
        """
        self.ttl_days = ttl_days
        self.auto_cleanup = auto_cleanup
        
        # Setup cache directory
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "yanger"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Database path
        self.db_path = self.cache_dir / "cache.db"
        
        # Initialize database
        self._init_database()
        
        # Cleanup expired entries on startup
        if auto_cleanup:
            self.cleanup_expired()
            
        logger.info(f"Initialized persistent cache at {self.db_path}")
    
    def db_connection(self):
        """Get a database connection context manager."""
        return sqlite3.connect(self.db_path)
        
    def _init_database(self) -> None:
        """Initialize SQLite database with schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys = ON")
            
            # Create tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS playlists (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    item_count INTEGER,
                    privacy_status TEXT,
                    channel_id TEXT,
                    channel_title TEXT,
                    etag TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    hit_count INTEGER DEFAULT 0
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    id TEXT,
                    playlist_id TEXT,
                    playlist_item_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    channel_title TEXT,
                    description TEXT,
                    position INTEGER,
                    duration TEXT,
                    view_count INTEGER,
                    added_at TIMESTAMP,
                    published_at TIMESTAMP,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
                )
            """)
            
            # Create indices for performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_playlist ON videos(playlist_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_playlists_cached ON playlists(cached_at)")
            
            # Virtual playlists tables
            conn.execute("""
                CREATE TABLE IF NOT EXISTS virtual_playlists (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    source TEXT,  -- 'takeout', 'manual'
                    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    video_count INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT 1
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS virtual_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id TEXT NOT NULL,
                    video_id TEXT NOT NULL,
                    title TEXT,
                    channel_title TEXT,
                    added_at TIMESTAMP,
                    position INTEGER,
                    FOREIGN KEY (playlist_id) REFERENCES virtual_playlists(id) ON DELETE CASCADE,
                    UNIQUE(playlist_id, video_id)
                )
            """)
            
            # Create indices for virtual tables
            conn.execute("CREATE INDEX IF NOT EXISTS idx_virtual_videos_playlist ON virtual_videos(playlist_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_virtual_videos_video ON virtual_videos(video_id)")
            
            # Check and update schema version
            cursor = conn.execute("SELECT value FROM cache_metadata WHERE key = 'schema_version'")
            row = cursor.fetchone()
            
            if row is None:
                conn.execute(
                    "INSERT INTO cache_metadata (key, value) VALUES ('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),)
                )
            elif int(row[0]) < self.SCHEMA_VERSION:
                # Handle schema migrations here in the future
                logger.info(f"Migrating cache schema from version {row[0]} to {self.SCHEMA_VERSION}")
                conn.execute(
                    "UPDATE cache_metadata SET value = ? WHERE key = 'schema_version'",
                    (str(self.SCHEMA_VERSION),)
                )
            
            conn.commit()
    
    def get_playlists(self) -> Optional[List[Playlist]]:
        """Get all cached playlists.
        
        Returns:
            List of playlists if cached and fresh, None if cache is empty or expired
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # First check if we have any playlists and when they were cached
            cursor = conn.execute("""
                SELECT MIN(cached_at) as oldest_cache, COUNT(*) as count 
                FROM playlists 
                WHERE id NOT LIKE 'virtual_%'
            """)
            
            row = cursor.fetchone()
            if not row or row['count'] == 0:
                return None
            
            # Check if the oldest cached playlist is expired
            if row['oldest_cache']:
                cached_at = datetime.fromisoformat(row['oldest_cache'])
                if datetime.now() - cached_at > timedelta(days=self.ttl_days):
                    logger.debug(f"Playlist cache expired (oldest: {row['oldest_cache']})")
                    return None
            
            # Get all playlists
            cursor = conn.execute("""
                SELECT * FROM playlists 
                ORDER BY title
            """)
            
            rows = cursor.fetchall()
            if not rows:
                return None
                
            playlists = []
            for row in rows:
                playlist = Playlist(
                    id=row['id'],
                    title=row['title'],
                    description=row['description'] or '',
                    item_count=row['item_count'] or 0,
                    privacy_status=PrivacyStatus(row['privacy_status']) if row['privacy_status'] else PrivacyStatus.PRIVATE,
                    channel_id=row['channel_id'],
                    channel_title=row['channel_title']
                )
                playlists.append(playlist)
                
            logger.debug(f"Loaded {len(playlists)} playlists from cache")
            return playlists
    
    def set_playlists(self, playlists: List[Playlist]) -> None:
        """Cache a list of playlists.
        
        Args:
            playlists: List of playlists to cache
        """
        with sqlite3.connect(self.db_path) as conn:
            # Use REPLACE to update existing or insert new
            for playlist in playlists:
                conn.execute("""
                    INSERT OR REPLACE INTO playlists 
                    (id, title, description, item_count, privacy_status, channel_id, channel_title, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    playlist.id,
                    playlist.title,
                    playlist.description,
                    playlist.item_count,
                    playlist.privacy_status.value,
                    playlist.channel_id,
                    playlist.channel_title
                ))
            
            conn.commit()
            logger.debug(f"Cached {len(playlists)} playlists")
    
    def get_videos(self, playlist_id: str) -> Optional[List[Video]]:
        """Get videos for a playlist from cache.
        
        Args:
            playlist_id: ID of the playlist
            
        Returns:
            List of videos if cached and fresh, None otherwise
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            # Check if playlist exists and is not expired
            cursor = conn.execute("""
                SELECT cached_at FROM playlists WHERE id = ?
            """, (playlist_id,))
            
            row = cursor.fetchone()
            if row is None:
                return None
                
            # Check if expired
            cached_at = datetime.fromisoformat(row['cached_at'])
            if datetime.now() - cached_at > timedelta(days=self.ttl_days):
                logger.debug(f"Cache expired for playlist {playlist_id}")
                return None
            
            # Get videos
            cursor = conn.execute("""
                SELECT * FROM videos 
                WHERE playlist_id = ?
                ORDER BY position
            """, (playlist_id,))
            
            rows = cursor.fetchall()
            if not rows:
                return None
                
            # Update access stats
            conn.execute("""
                UPDATE playlists 
                SET accessed_at = CURRENT_TIMESTAMP, hit_count = hit_count + 1
                WHERE id = ?
            """, (playlist_id,))
            conn.commit()
            
            videos = []
            for row in rows:
                video = Video(
                    id=row['id'],
                    playlist_item_id=row['playlist_item_id'],
                    title=row['title'],
                    channel_title=row['channel_title'] or '',
                    description=row['description'] or '',
                    position=row['position'] or 0,
                    duration=row['duration'],
                    view_count=row['view_count'],
                    added_at=datetime.fromisoformat(row['added_at']) if row['added_at'] else None,
                    published_at=datetime.fromisoformat(row['published_at']) if row['published_at'] else None
                )
                videos.append(video)
                
            logger.debug(f"Cache hit: loaded {len(videos)} videos for playlist {playlist_id}")
            return videos
        
    def set_videos(self, playlist_id: str, videos: List[Video]) -> None:
        """Cache videos for a playlist.
        
        Args:
            playlist_id: ID of the playlist
            videos: List of videos to cache
        """
        with sqlite3.connect(self.db_path) as conn:
            # Delete existing videos for this playlist
            conn.execute("DELETE FROM videos WHERE playlist_id = ?", (playlist_id,))
            
            # Insert new videos
            for video in videos:
                conn.execute("""
                    INSERT INTO videos 
                    (id, playlist_id, playlist_item_id, title, channel_title, description,
                     position, duration, view_count, added_at, published_at, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    video.id,
                    playlist_id,
                    video.playlist_item_id,
                    video.title,
                    video.channel_title,
                    video.description,
                    video.position,
                    video.duration,
                    video.view_count,
                    video.added_at.isoformat() if video.added_at else None,
                    video.published_at.isoformat() if video.published_at else None
                ))
            
            # Update playlist cache time
            conn.execute("""
                UPDATE playlists 
                SET cached_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (playlist_id,))
            
            conn.commit()
            logger.debug(f"Cached {len(videos)} videos for playlist {playlist_id}")
        
    def invalidate_playlist(self, playlist_id: str) -> None:
        """Invalidate cache for a specific playlist.
        
        Args:
            playlist_id: ID of the playlist to invalidate
        """
        with sqlite3.connect(self.db_path) as conn:
            # Delete videos first (cascade should handle this, but be explicit)
            conn.execute("DELETE FROM videos WHERE playlist_id = ?", (playlist_id,))
            # Delete playlist
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
            conn.commit()
            logger.debug(f"Invalidated cache for playlist {playlist_id}")
    
    def invalidate_playlists_cache(self) -> None:
        """Invalidate the entire playlists cache.
        
        This forces a fresh fetch from the API on the next load_playlists call.
        Useful after operations that modify the playlist list itself (create, rename, delete).
        """
        with sqlite3.connect(self.db_path) as conn:
            # Delete all playlist entries but keep virtual playlists
            conn.execute("DELETE FROM playlists")
            conn.execute("DELETE FROM videos")
            conn.commit()
            logger.debug("Invalidated playlists cache")
            
    def clear(self) -> None:
        """Clear entire cache."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM videos")
            conn.execute("DELETE FROM playlists")
            conn.commit()
            logger.info("Cleared entire cache")
        
    def cleanup_expired(self) -> int:
        """Remove expired cache entries.
        
        Returns:
            Number of entries removed
        """
        cutoff_date = datetime.now() - timedelta(days=self.ttl_days)
        
        with sqlite3.connect(self.db_path) as conn:
            # Get count before deletion
            cursor = conn.execute(
                "SELECT COUNT(*) FROM playlists WHERE cached_at < ?",
                (cutoff_date.isoformat(),)
            )
            count = cursor.fetchone()[0]
            
            # Delete expired playlists (videos cascade)
            conn.execute(
                "DELETE FROM playlists WHERE cached_at < ?",
                (cutoff_date.isoformat(),)
            )
            conn.commit()
            
            if count > 0:
                logger.info(f"Cleaned up {count} expired cache entries")
            
            return count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            
            # Count playlists
            cursor = conn.execute("SELECT COUNT(*) FROM playlists")
            stats['playlist_count'] = cursor.fetchone()[0]
            
            # Count videos
            cursor = conn.execute("SELECT COUNT(*) FROM videos")
            stats['video_count'] = cursor.fetchone()[0]
            
            # Total hits
            cursor = conn.execute("SELECT SUM(hit_count) FROM playlists")
            stats['total_hits'] = cursor.fetchone()[0] or 0
            
            # Cache size
            stats['cache_size_mb'] = self.db_path.stat().st_size / (1024 * 1024)
            
            # Oldest and newest entries
            cursor = conn.execute("SELECT MIN(cached_at), MAX(cached_at) FROM playlists")
            row = cursor.fetchone()
            if row[0]:
                stats['oldest_entry'] = row[0]
                stats['newest_entry'] = row[1]
            
            stats['ttl_days'] = self.ttl_days
            stats['cache_path'] = str(self.db_path)
            
            return stats
    
    # Virtual Playlist Methods
    
    def import_virtual_playlist(self, name: str, videos: List[Dict], 
                              source: str = 'takeout', 
                              description: str = '') -> str:
        """Import a virtual playlist from takeout or other source.
        
        Args:
            name: Playlist name
            videos: List of video dictionaries with 'video_id' and optional metadata
            source: Source of the playlist ('takeout', 'manual', etc.)
            description: Playlist description
            
        Returns:
            Playlist ID
        """
        import uuid
        playlist_id = str(uuid.uuid4())
        
        with sqlite3.connect(self.db_path) as conn:
            # Insert playlist
            conn.execute("""
                INSERT INTO virtual_playlists (id, title, description, source, video_count)
                VALUES (?, ?, ?, ?, ?)
            """, (playlist_id, name, description, source, len(videos)))
            
            # Insert videos
            for position, video in enumerate(videos):
                conn.execute("""
                    INSERT OR IGNORE INTO virtual_videos 
                    (playlist_id, video_id, title, channel_title, added_at, position)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    playlist_id,
                    video['video_id'],
                    video.get('title', ''),
                    video.get('channel', ''),
                    video.get('added_at'),
                    position
                ))
            
            conn.commit()
            logger.info(f"Imported virtual playlist '{name}' with {len(videos)} videos")
            
        return playlist_id
    
    def update_or_create_virtual_playlist(self, name: str, videos: List[Dict],
                                         source: str = 'takeout',
                                         description: str = '',
                                         merge: bool = True) -> str:
        """Update existing virtual playlist or create new one.
        
        Args:
            name: Playlist name
            videos: List of video dictionaries with 'video_id' and optional metadata
            source: Source of the playlist ('takeout', 'manual', etc.)
            description: Playlist description
            merge: If True, merge with existing videos. If False, replace all videos.
            
        Returns:
            Playlist ID
        """
        existing = self.get_virtual_playlist_by_name(name)
        
        if existing:
            playlist_id = existing['id']
            
            with sqlite3.connect(self.db_path) as conn:
                if merge:
                    # Get existing video IDs to avoid duplicates
                    cursor = conn.execute("""
                        SELECT video_id FROM virtual_videos
                        WHERE playlist_id = ?
                    """, (playlist_id,))
                    existing_video_ids = {row[0] for row in cursor.fetchall()}
                    
                    # Get max position for appending
                    cursor = conn.execute("""
                        SELECT MAX(position) FROM virtual_videos
                        WHERE playlist_id = ?
                    """, (playlist_id,))
                    max_position = cursor.fetchone()[0] or -1
                    
                    # Add only new videos
                    new_videos_count = 0
                    for video in videos:
                        if video['video_id'] not in existing_video_ids:
                            max_position += 1
                            conn.execute("""
                                INSERT OR IGNORE INTO virtual_videos
                                (playlist_id, video_id, title, channel_title, added_at, position)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                playlist_id,
                                video['video_id'],
                                video.get('title', ''),
                                video.get('channel', ''),
                                video.get('added_at'),
                                max_position
                            ))
                            new_videos_count += 1
                    
                    # Update video count
                    conn.execute("""
                        UPDATE virtual_playlists
                        SET video_count = (
                            SELECT COUNT(*) FROM virtual_videos
                            WHERE playlist_id = ?
                        ),
                        description = ?
                        WHERE id = ?
                    """, (playlist_id, description, playlist_id))
                    
                    conn.commit()
                    logger.info(f"Merged {new_videos_count} new videos into playlist '{name}'")
                    
                else:
                    # Replace mode: delete existing videos and add new ones
                    conn.execute("DELETE FROM virtual_videos WHERE playlist_id = ?", (playlist_id,))
                    
                    # Insert new videos
                    for position, video in enumerate(videos):
                        conn.execute("""
                            INSERT OR IGNORE INTO virtual_videos
                            (playlist_id, video_id, title, channel_title, added_at, position)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            playlist_id,
                            video['video_id'],
                            video.get('title', ''),
                            video.get('channel', ''),
                            video.get('added_at'),
                            position
                        ))
                    
                    # Update playlist info
                    conn.execute("""
                        UPDATE virtual_playlists
                        SET video_count = ?, description = ?
                        WHERE id = ?
                    """, (len(videos), description, playlist_id))
                    
                    conn.commit()
                    logger.info(f"Replaced playlist '{name}' with {len(videos)} videos")
            
            return playlist_id
        else:
            # Create new playlist
            return self.import_virtual_playlist(name, videos, source, description)
    
    def get_virtual_playlists(self) -> List[Dict]:
        """Get all virtual playlists.
        
        Returns:
            List of playlist dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM virtual_playlists 
                WHERE is_active = 1
                ORDER BY imported_at DESC
            """)
            
            playlists = []
            for row in cursor:
                playlists.append({
                    'id': row['id'],
                    'title': row['title'],
                    'description': row['description'],
                    'source': row['source'],
                    'imported_at': row['imported_at'],
                    'video_count': row['video_count']
                })
            
            return playlists
    
    def get_virtual_playlist_by_name(self, name: str) -> Optional[Dict]:
        """Get a virtual playlist by name.
        
        Args:
            name: Playlist name
            
        Returns:
            Playlist dictionary or None
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM virtual_playlists
                WHERE title = ? AND is_active = 1
                LIMIT 1
            """, (name,))
            
            row = cursor.fetchone()
            if row:
                return {
                    'id': row['id'],
                    'title': row['title'],
                    'description': row['description'],
                    'source': row['source'],
                    'video_count': row['video_count'],
                    'imported_at': row['imported_at']
                }
            return None
    
    def get_virtual_videos(self, playlist_id: str) -> List[Dict]:
        """Get videos from a virtual playlist.
        
        Args:
            playlist_id: Virtual playlist ID
            
        Returns:
            List of video dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT * FROM virtual_videos
                WHERE playlist_id = ?
                ORDER BY position
            """, (playlist_id,))
            
            videos = []
            for row in cursor:
                videos.append({
                    'video_id': row['video_id'],
                    'title': row['title'] or '',
                    'channel_title': row['channel_title'] or '',
                    'added_at': row['added_at'],
                    'position': row['position']
                })
            
            return videos
    
    def delete_virtual_playlist(self, playlist_id: str) -> bool:
        """Delete a virtual playlist.
        
        Args:
            playlist_id: Virtual playlist ID
            
        Returns:
            True if deleted
        """
        with sqlite3.connect(self.db_path) as conn:
            # Videos will be deleted automatically due to CASCADE
            result = conn.execute(
                "DELETE FROM virtual_playlists WHERE id = ?",
                (playlist_id,)
            )
            conn.commit()
            
            if result.rowcount > 0:
                logger.info(f"Deleted virtual playlist {playlist_id}")
                return True
            return False
    
    def update_virtual_video_metadata(self, video_id: str, metadata: Dict[str, Any]) -> bool:
        """Update metadata for a virtual video.
        
        Args:
            video_id: YouTube video ID
            metadata: Dictionary with title, channel_title, description, etc.
            
        Returns:
            True if updated
        """
        with sqlite3.connect(self.db_path) as conn:
            # First check if we need to add columns for new metadata
            cursor = conn.execute("PRAGMA table_info(virtual_videos)")
            columns = [col[1] for col in cursor.fetchall()]
            
            # Add metadata columns if they don't exist
            if 'description' not in columns:
                conn.execute("ALTER TABLE virtual_videos ADD COLUMN description TEXT")
            if 'thumbnail_url' not in columns:
                conn.execute("ALTER TABLE virtual_videos ADD COLUMN thumbnail_url TEXT")
            if 'duration' not in columns:
                conn.execute("ALTER TABLE virtual_videos ADD COLUMN duration TEXT")
            if 'metadata_fetched_at' not in columns:
                conn.execute("ALTER TABLE virtual_videos ADD COLUMN metadata_fetched_at TIMESTAMP")
            
            # Update the video metadata
            result = conn.execute("""
                UPDATE virtual_videos
                SET title = ?,
                    channel_title = ?,
                    description = ?,
                    thumbnail_url = ?,
                    duration = ?,
                    metadata_fetched_at = CURRENT_TIMESTAMP
                WHERE video_id = ?
            """, (
                metadata.get('title', ''),
                metadata.get('channel_title', ''),
                metadata.get('description', ''),
                metadata.get('thumbnail_url', ''),
                metadata.get('duration', ''),
                video_id
            ))
            
            conn.commit()
            return result.rowcount > 0
    
    def get_virtual_videos_without_metadata(self, playlist_id: Optional[str] = None, 
                                           limit: Optional[int] = None,
                                           since_date: Optional[datetime] = None) -> List[str]:
        """Get video IDs that don't have metadata yet.
        
        Args:
            playlist_id: Optional playlist ID to filter by
            limit: Optional limit on number of IDs to return
            since_date: Optional date filter - only return videos added after this date
            
        Returns:
            List of video IDs that need metadata
        """
        with sqlite3.connect(self.db_path) as conn:
            query = """
                SELECT DISTINCT video_id 
                FROM virtual_videos 
                WHERE (title IS NULL OR title = '')
            """
            params = []
            
            if playlist_id:
                query += " AND playlist_id = ?"
                params.append(playlist_id)
            
            if since_date:
                query += " AND added_at >= ?"
                params.append(since_date.isoformat())
            
            # Sort by added_at to prioritize newer videos
            query += " ORDER BY added_at DESC"
            
            if limit:
                query += f" LIMIT {limit}"
            
            cursor = conn.execute(query, params)
            return [row[0] for row in cursor.fetchall()]
    
    def deduplicate_virtual_playlists(self) -> int:
        """Remove duplicate virtual playlists, keeping the oldest.
        
        Returns:
            Number of duplicates removed
        """
        with sqlite3.connect(self.db_path) as conn:
            # Find duplicates (same title)
            cursor = conn.execute("""
                SELECT title, COUNT(*) as count, MIN(imported_at) as oldest
                FROM virtual_playlists
                WHERE is_active = 1
                GROUP BY title
                HAVING count > 1
            """)
            
            duplicates_removed = 0
            
            for row in cursor.fetchall():
                title = row[0]
                
                # Get all playlists with this title
                dup_cursor = conn.execute("""
                    SELECT id FROM virtual_playlists
                    WHERE title = ? AND is_active = 1
                    ORDER BY imported_at ASC
                """, (title,))
                
                playlist_ids = [r[0] for r in dup_cursor.fetchall()]
                
                if len(playlist_ids) > 1:
                    # Keep the first (oldest), mark others as inactive
                    keep_id = playlist_ids[0]
                    remove_ids = playlist_ids[1:]
                    
                    # Merge videos from duplicates into the keeper
                    for remove_id in remove_ids:
                        # Get videos from duplicate
                        videos = conn.execute("""
                            SELECT video_id, title, channel_title, added_at, position
                            FROM virtual_videos
                            WHERE playlist_id = ?
                        """, (remove_id,)).fetchall()
                        
                        # Add unique videos to keeper playlist
                        for video in videos:
                            conn.execute("""
                                INSERT OR IGNORE INTO virtual_videos
                                (playlist_id, video_id, title, channel_title, added_at, position)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (keep_id, video[0], video[1], video[2], video[3], video[4]))
                        
                        # Mark duplicate playlist as inactive
                        conn.execute("""
                            UPDATE virtual_playlists
                            SET is_active = 0
                            WHERE id = ?
                        """, (remove_id,))
                        
                        duplicates_removed += 1
                        logger.info(f"Merged and removed duplicate playlist: {title} (id: {remove_id})")
                    
                    # Update video count for keeper
                    conn.execute("""
                        UPDATE virtual_playlists
                        SET video_count = (
                            SELECT COUNT(DISTINCT video_id)
                            FROM virtual_videos
                            WHERE playlist_id = ?
                        )
                        WHERE id = ?
                    """, (keep_id, keep_id))
            
            conn.commit()
            
            if duplicates_removed > 0:
                logger.info(f"Removed {duplicates_removed} duplicate playlists")
            
            return duplicates_removed
        
    def has_playlist(self, playlist_id: str) -> bool:
        """Check if a playlist is in cache.
        
        Args:
            playlist_id: ID of the playlist
            
        Returns:
            True if playlist is cached and not expired
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT cached_at FROM playlists WHERE id = ?
            """, (playlist_id,))
            
            row = cursor.fetchone()
            if row is None:
                return False
                
            # Check if expired
            cached_at = datetime.fromisoformat(row[0])
            if datetime.now() - cached_at > timedelta(days=self.ttl_days):
                return False
                
            return True


# Keep the old PlaylistCache for backwards compatibility during migration
class PlaylistCache(PersistentCache):
    """Backwards compatibility wrapper for PersistentCache."""
    
    def __init__(self, ttl_seconds: int = 300, max_entries: int = 50):
        # Convert seconds to days for persistent cache
        ttl_days = max(1, ttl_seconds // 86400)  # At least 1 day
        super().__init__(ttl_days=ttl_days)
        logger.info("Using PersistentCache with backwards compatibility mode")
    
    def get(self, playlist_id: str) -> Optional[List[Video]]:
        """Compatibility wrapper for get_videos."""
        return self.get_videos(playlist_id)
    
    def set(self, playlist_id: str, videos: List[Video]) -> None:
        """Compatibility wrapper for set_videos."""
        self.set_videos(playlist_id, videos)
    
    def invalidate(self, playlist_id: str) -> None:
        """Compatibility wrapper for invalidate_playlist."""
        self.invalidate_playlist(playlist_id)
    
    def invalidate_all(self) -> None:
        """Compatibility wrapper for clear."""
        self.clear()