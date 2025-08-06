"""YouTube API client wrapper with quota tracking.

Provides a high-level interface to YouTube Data API v3 with built-in
quota management and error handling.
"""
# Created: 2025-08-03

from typing import List, Optional, Dict, Any, Generator
from datetime import datetime
import logging

from googleapiclient.errors import HttpError
from googleapiclient.discovery import Resource

from .models import Playlist, Video
from .auth import YouTubeAuth


logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    """Raised when API quota is exceeded."""
    pass


class YouTubeAPIClient:
    """High-level YouTube API client with quota tracking."""
    
    # Quota costs for different operations
    QUOTA_COSTS = {
        'playlists.list': 1,
        'playlistItems.list': 1,
        'videos.list': 1,
        'playlists.insert': 50,
        'playlists.update': 50,
        'playlists.delete': 50,
        'playlistItems.insert': 50,
        'playlistItems.update': 50,
        'playlistItems.delete': 50,
    }
    
    def __init__(self, auth: YouTubeAuth, daily_quota: int = 10000):
        """Initialize the API client.
        
        Args:
            auth: Authenticated YouTubeAuth instance
            daily_quota: Daily quota limit (default: 10000)
        """
        self.auth = auth
        self.youtube: Resource = auth.get_youtube_service()
        self.daily_quota = daily_quota
        self.quota_used = 0
        self.quota_reset_time = None
        
    def _track_quota(self, operation: str, count: int = 1) -> None:
        """Track quota usage for an operation.
        
        Args:
            operation: The API operation name
            count: Number of times the operation was performed
            
        Raises:
            QuotaExceededError: If quota would be exceeded
        """
        cost = self.QUOTA_COSTS.get(operation, 1) * count
        
        if self.quota_used + cost > self.daily_quota:
            raise QuotaExceededError(
                f"Operation would exceed daily quota. "
                f"Used: {self.quota_used}, Cost: {cost}, "
                f"Limit: {self.daily_quota}"
            )
        
        self.quota_used += cost
        logger.debug(f"Quota used: {self.quota_used}/{self.daily_quota} "
                    f"(+{cost} for {operation})")
    
    def get_quota_remaining(self) -> int:
        """Get remaining quota for today.
        
        Returns:
            Remaining quota units
        """
        return self.daily_quota - self.quota_used
    
    def get_playlists(self, 
                      mine: bool = True,
                      channel_id: Optional[str] = None,
                      max_results: int = 50) -> List[Playlist]:
        """Get playlists for the authenticated user or a channel.
        
        Args:
            mine: If True, get authenticated user's playlists
            channel_id: Channel ID to get playlists for (if mine=False)
            max_results: Maximum results per page (max 50)
            
        Returns:
            List of Playlist objects
        """
        playlists = []
        
        try:
            # Build the request
            request_params = {
                'part': 'snippet,contentDetails,status',
                'maxResults': min(max_results, 50)
            }
            
            if mine:
                request_params['mine'] = True
            elif channel_id:
                request_params['channelId'] = channel_id
            else:
                raise ValueError("Either 'mine' or 'channel_id' must be provided")
            
            request = self.youtube.playlists().list(**request_params)
            
            # Handle pagination
            while request:
                self._track_quota('playlists.list')
                response = request.execute()
                
                # Convert response items to Playlist objects
                for item in response.get('items', []):
                    playlist = Playlist.from_youtube_response(item)
                    playlists.append(playlist)
                
                # Get next page
                request = self.youtube.playlists().list_next(request, response)
                
        except HttpError as e:
            logger.error(f"Error fetching playlists: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
            
        return playlists
    
    def get_playlist_items(self,
                          playlist_id: str,
                          max_results: int = 50) -> List[Video]:
        """Get all videos in a playlist.
        
        Args:
            playlist_id: ID of the playlist
            max_results: Maximum results per page (max 50)
            
        Returns:
            List of Video objects
        """
        videos = []
        
        try:
            request = self.youtube.playlistItems().list(
                part='snippet,status',
                playlistId=playlist_id,
                maxResults=min(max_results, 50)
            )
            
            # Handle pagination
            while request:
                self._track_quota('playlistItems.list')
                response = request.execute()
                
                # Convert response items to Video objects
                for item in response.get('items', []):
                    video = Video.from_playlist_item(item)
                    videos.append(video)
                
                # Get next page
                request = self.youtube.playlistItems().list_next(request, response)
                
        except HttpError as e:
            logger.error(f"Error fetching playlist items: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
            
        return videos
    
    def add_video_to_playlist(self,
                             video_id: str,
                             playlist_id: str,
                             position: Optional[int] = None) -> str:
        """Add a video to a playlist.
        
        Args:
            video_id: ID of the video to add
            playlist_id: ID of the target playlist
            position: Position in playlist (optional)
            
        Returns:
            ID of the created playlist item
        """
        try:
            self._track_quota('playlistItems.insert')
            
            body = {
                'snippet': {
                    'playlistId': playlist_id,
                    'resourceId': {
                        'kind': 'youtube#video',
                        'videoId': video_id
                    }
                }
            }
            
            if position is not None:
                body['snippet']['position'] = position
            
            response = self.youtube.playlistItems().insert(
                part='snippet',
                body=body
            ).execute()
            
            return response['id']
            
        except HttpError as e:
            logger.error(f"Error adding video to playlist: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
    
    def remove_video_from_playlist(self, playlist_item_id: str) -> None:
        """Remove a video from a playlist.
        
        Args:
            playlist_item_id: ID of the playlist item to remove
        """
        try:
            self._track_quota('playlistItems.delete')
            
            self.youtube.playlistItems().delete(
                id=playlist_item_id
            ).execute()
            
        except HttpError as e:
            logger.error(f"Error removing video from playlist: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
    
    def move_video(self,
                   video: Video,
                   target_playlist_id: str) -> str:
        """Move a video from one playlist to another.
        
        This is a convenience method that adds to target and removes from source.
        
        Args:
            video: Video object (must have playlist_item_id)
            target_playlist_id: ID of the target playlist
            
        Returns:
            ID of the new playlist item
            
        Raises:
            QuotaExceededError: If operation would exceed quota (100 units)
        """
        # Check if we have enough quota for both operations
        if self.get_quota_remaining() < 100:
            raise QuotaExceededError(
                "Not enough quota to move video (requires 100 units)"
            )
        
        # Add to target playlist
        new_item_id = self.add_video_to_playlist(
            video.id,
            target_playlist_id
        )
        
        # Remove from source playlist
        if video.playlist_item_id:
            self.remove_video_from_playlist(video.playlist_item_id)
        
        return new_item_id
    
    def create_playlist(self,
                       title: str,
                       description: str = "",
                       privacy_status: str = "private") -> Playlist:
        """Create a new playlist.
        
        Args:
            title: Playlist title
            description: Playlist description
            privacy_status: Privacy setting (public, private, unlisted)
            
        Returns:
            Created Playlist object
        """
        try:
            self._track_quota('playlists.insert')
            
            body = {
                'snippet': {
                    'title': title,
                    'description': description
                },
                'status': {
                    'privacyStatus': privacy_status
                }
            }
            
            response = self.youtube.playlists().insert(
                part='snippet,status',
                body=body
            ).execute()
            
            return Playlist.from_youtube_response(response)
            
        except HttpError as e:
            logger.error(f"Error creating playlist: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
    
    def update_playlist(self,
                       playlist_id: str,
                       title: Optional[str] = None,
                       description: Optional[str] = None,
                       privacy_status: Optional[str] = None) -> None:
        """Update playlist metadata.
        
        Args:
            playlist_id: ID of the playlist to update
            title: New title (optional)
            description: New description (optional)
            privacy_status: New privacy setting (optional)
        """
        try:
            # First get the current playlist data
            self._track_quota('playlists.list')
            current = self.youtube.playlists().list(
                part='snippet,status',
                id=playlist_id
            ).execute()
            
            if not current.get('items'):
                raise ValueError(f"Playlist {playlist_id} not found")
            
            playlist_data = current['items'][0]
            
            # Update fields if provided
            if title is not None:
                playlist_data['snippet']['title'] = title
            if description is not None:
                playlist_data['snippet']['description'] = description
            if privacy_status is not None:
                playlist_data['status']['privacyStatus'] = privacy_status
            
            # Update the playlist
            self._track_quota('playlists.update')
            self.youtube.playlists().update(
                part='snippet,status',
                body=playlist_data
            ).execute()
            
        except HttpError as e:
            logger.error(f"Error updating playlist: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
    
    def delete_playlist(self, playlist_id: str) -> None:
        """Delete a playlist.
        
        Args:
            playlist_id: ID of the playlist to delete
        """
        try:
            self._track_quota('playlists.delete')
            
            self.youtube.playlists().delete(
                id=playlist_id
            ).execute()
            
        except HttpError as e:
            logger.error(f"Error deleting playlist: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise