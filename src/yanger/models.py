"""Data models for YouTube playlists and videos.

Defines the core data structures used throughout the application.
"""
# Created: 2025-08-03

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from enum import Enum


class PrivacyStatus(Enum):
    """YouTube playlist/video privacy status."""
    PUBLIC = "public"
    PRIVATE = "private"
    UNLISTED = "unlisted"


@dataclass
class Playlist:
    """Represents a YouTube playlist."""
    id: str
    title: str
    description: str = ""
    item_count: int = 0
    privacy_status: PrivacyStatus = PrivacyStatus.PUBLIC
    published_at: Optional[datetime] = None
    thumbnail_url: Optional[str] = None
    channel_id: Optional[str] = None
    channel_title: Optional[str] = None
    
    # Special playlist flag (Watch Later, History, etc.)
    is_special: bool = False
    
    # Virtual playlist flag (local only, not synced to YouTube)
    is_virtual: bool = False
    source: Optional[str] = None  # 'takeout', 'manual', etc.
    imported_at: Optional[datetime] = None  # When imported from takeout
    
    # UI state
    is_selected: bool = False
    is_focused: bool = False
    
    @classmethod
    def from_youtube_response(cls, item: Dict[str, Any]) -> 'Playlist':
        """Create a Playlist from YouTube API response.
        
        Args:
            item: Single item from playlists.list() response
            
        Returns:
            Playlist instance
        """
        snippet = item.get('snippet', {})
        status = item.get('status', {})
        content_details = item.get('contentDetails', {})
        
        # Parse published date
        published_at = None
        if pub_str := snippet.get('publishedAt'):
            try:
                published_at = datetime.fromisoformat(
                    pub_str.replace('Z', '+00:00')
                )
            except ValueError:
                pass
        
        # Get thumbnail URL (prefer high quality)
        thumbnail_url = None
        if thumbnails := snippet.get('thumbnails', {}):
            for quality in ['high', 'medium', 'default']:
                if quality in thumbnails:
                    thumbnail_url = thumbnails[quality]['url']
                    break
        
        return cls(
            id=item['id'],
            title=snippet.get('title', 'Untitled'),
            description=snippet.get('description', ''),
            item_count=content_details.get('itemCount', 0),
            privacy_status=PrivacyStatus(
                status.get('privacyStatus', 'private')
            ),
            published_at=published_at,
            thumbnail_url=thumbnail_url,
            channel_id=snippet.get('channelId'),
            channel_title=snippet.get('channelTitle')
        )
    
    def __str__(self) -> str:
        """String representation for display."""
        return f"{self.title} ({self.item_count} videos)"


@dataclass
class Video:
    """Represents a video in a playlist."""
    id: str  # Video ID
    playlist_item_id: str  # ID for this video in the playlist
    title: str
    channel_title: str
    description: str = ""
    position: int = 0
    added_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    duration: Optional[str] = None  # ISO 8601 duration
    view_count: Optional[int] = None
    like_count: Optional[int] = None
    thumbnail_url: Optional[str] = None
    privacy_status: PrivacyStatus = PrivacyStatus.PUBLIC
    
    # Playlist relationship
    playlist_id: Optional[str] = None
    
    # UI state
    is_selected: bool = False
    is_marked: bool = False
    is_focused: bool = False
    
    @classmethod
    def from_playlist_item(cls, item: Dict[str, Any]) -> 'Video':
        """Create a Video from playlistItems.list() response.
        
        Args:
            item: Single item from playlistItems.list() response
            
        Returns:
            Video instance
        """
        snippet = item.get('snippet', {})
        resource_id = snippet.get('resourceId', {})
        status = item.get('status', {})
        
        # Parse dates
        added_at = None
        published_at = None
        
        if add_str := snippet.get('publishedAt'):
            try:
                added_at = datetime.fromisoformat(
                    add_str.replace('Z', '+00:00')
                )
            except ValueError:
                pass
                
        if pub_str := snippet.get('videoPublishedAt'):
            try:
                published_at = datetime.fromisoformat(
                    pub_str.replace('Z', '+00:00')
                )
            except ValueError:
                pass
        
        # Get thumbnail
        thumbnail_url = None
        if thumbnails := snippet.get('thumbnails', {}):
            for quality in ['high', 'medium', 'default']:
                if quality in thumbnails:
                    thumbnail_url = thumbnails[quality]['url']
                    break
        
        return cls(
            id=resource_id.get('videoId', ''),
            playlist_item_id=item['id'],
            title=snippet.get('title', 'Untitled'),
            channel_title=snippet.get('videoOwnerChannelTitle', 'Unknown'),
            description=snippet.get('description', ''),
            position=snippet.get('position', 0),
            added_at=added_at,
            published_at=published_at,
            thumbnail_url=thumbnail_url,
            privacy_status=PrivacyStatus(
                status.get('privacyStatus', 'public')
            ),
            playlist_id=snippet.get('playlistId')
        )
    
    def format_duration(self) -> str:
        """Format ISO 8601 duration to human readable format.
        
        Returns:
            Formatted duration string (e.g., "10:23" or "1:02:15")
        """
        if not self.duration:
            return "--:--"
            
        # Simple parser for ISO 8601 duration
        # Format: PT#H#M#S
        duration = self.duration
        if not duration.startswith('PT'):
            return duration
            
        duration = duration[2:]  # Remove 'PT'
        
        hours = 0
        minutes = 0
        seconds = 0
        
        # Extract hours
        if 'H' in duration:
            h_pos = duration.index('H')
            hours = int(duration[:h_pos])
            duration = duration[h_pos + 1:]
        
        # Extract minutes
        if 'M' in duration:
            m_pos = duration.index('M')
            minutes = int(duration[:m_pos])
            duration = duration[m_pos + 1:]
        
        # Extract seconds
        if 'S' in duration:
            s_pos = duration.index('S')
            seconds = int(duration[:s_pos])
        
        # Format output
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    def format_view_count(self) -> str:
        """Format view count for display.
        
        Returns:
            Formatted view count (e.g., "1.2M views")
        """
        if self.view_count is None:
            return "-- views"
            
        count = self.view_count
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B views"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M views"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K views"
        else:
            return f"{count} views"
    
    def __str__(self) -> str:
        """String representation for display."""
        duration = self.format_duration()
        return f"{self.title} [{duration}]"


@dataclass
class ClipboardItem:
    """Item stored in the clipboard."""
    video: Video
    source_playlist_id: str
    operation: str = "copy"  # "copy" or "cut"
    

@dataclass
class Clipboard:
    """Clipboard for copy/cut/paste operations."""
    items: List[ClipboardItem] = field(default_factory=list)
    
    def copy(self, videos: List[Video], source_playlist_id: str) -> None:
        """Copy videos to clipboard."""
        self.items = [
            ClipboardItem(video, source_playlist_id, "copy")
            for video in videos
        ]
    
    def cut(self, videos: List[Video], source_playlist_id: str) -> None:
        """Cut videos to clipboard."""
        self.items = [
            ClipboardItem(video, source_playlist_id, "cut")
            for video in videos
        ]
    
    def clear(self) -> None:
        """Clear clipboard."""
        self.items = []
    
    def is_empty(self) -> bool:
        """Check if clipboard is empty."""
        return len(self.items) == 0
    
    def get_operation_type(self) -> Optional[str]:
        """Get the operation type if clipboard has items."""
        if self.items:
            return self.items[0].operation
        return None
    
    def __len__(self) -> int:
        """Get number of items in clipboard."""
        return len(self.items)