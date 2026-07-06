"""YouTube API client wrapper with quota tracking.

Provides a high-level interface to YouTube Data API v3 with built-in
quota management and error handling.
"""
# Created: 2025-08-03

from typing import List, Optional, Dict, Any, Generator
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import logging

from googleapiclient.errors import HttpError
from googleapiclient.discovery import Resource

from .models import Playlist, Video, PrivacyStatus
from .auth import YouTubeAuth


logger = logging.getLogger(__name__)


def current_quota_reset_key() -> str:
    """Key for the current quota window: the Pacific-time date (YYYY-MM-DD).

    The YouTube Data API daily quota resets at midnight Pacific Time; when the Pacific day
    rolls over the key changes and the shared counter starts a fresh window at 0 — no reset
    job needed. The ZoneInfo lookup is done HERE (not at import) and guarded so a platform
    with no IANA tz database (Windows / slim containers) degrades to a UTC-8 approximation
    instead of failing to import the whole app. The `tzdata` dependency makes the exact
    (DST-aware) path the normal one.
    """
    try:
        now_pacific = datetime.now(ZoneInfo("America/Los_Angeles"))
    except ZoneInfoNotFoundError:
        now_pacific = datetime.now(timezone.utc) - timedelta(hours=8)
    return now_pacific.date().isoformat()


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
        'videos.update': 50,
        'playlists.insert': 50,
        'playlists.update': 50,
        'playlists.delete': 50,
        'playlistItems.insert': 50,
        'playlistItems.update': 50,
        'playlistItems.delete': 50,
    }
    
    def __init__(self, auth: YouTubeAuth, daily_quota: int = 10000, quota_store=None):
        """Initialize the API client.

        Args:
            auth: Authenticated YouTubeAuth instance
            daily_quota: Daily quota limit (default: 10000)
            quota_store: Optional object exposing get_quota_used(reset_key) and
                add_quota_used(units, reset_key) (e.g. PersistentCache). When provided, quota
                is PERSISTED and SHARED across processes (TUI + MCP) and reset at the Pacific
                window; otherwise it is tracked in-memory per process (the legacy behaviour).
        """
        self.auth = auth
        self.youtube: Resource = auth.get_youtube_service()
        self.daily_quota = daily_quota
        self._quota_store = quota_store
        self._quota_used = 0  # in-memory fallback when no store is provided
        self.quota_reset_time = None

    @property
    def quota_used(self) -> int:
        """Units used in the current window — the shared store's count if present, else in-memory."""
        if self._quota_store is not None:
            return self._quota_store.get_quota_used(current_quota_reset_key())
        return self._quota_used

    @quota_used.setter
    def quota_used(self, value: int) -> None:
        # Kept so an explicit in-memory reset (client.quota_used = 0) still works without a store.
        self._quota_used = value

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

        if self._quota_store is not None:
            self._quota_store.add_quota_used(cost, current_quota_reset_key())
        else:
            self._quota_used += cost
        logger.debug(f"Quota +{cost} for {operation} (limit {self.daily_quota})")

    def get_quota_remaining(self) -> int:
        """Get remaining quota for today.
        
        Returns:
            Remaining quota units
        """
        return self.daily_quota - self.quota_used
    
    def get_playlists(self, 
                      mine: bool = True,
                      channel_id: Optional[str] = None,
                      max_results: int = 50,
                      include_special: bool = True,
                      progress_callback: Optional[callable] = None) -> List[Playlist]:
        """Get playlists for the authenticated user or a channel.
        
        Args:
            mine: If True, get authenticated user's playlists
            channel_id: Channel ID to get playlists for (if mine=False)
            max_results: Maximum results per page (max 50)
            include_special: If True, append special playlists (Watch Later, History)
            progress_callback: Optional callback for progress updates (page_num, total_so_far)
            
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
            page_count = 0
            while request:
                page_count += 1
                self._track_quota('playlists.list')
                response = request.execute()
                
                # Log pagination progress
                items_in_page = len(response.get('items', []))
                logger.info(f"Fetched page {page_count} of playlists: {items_in_page} items")
                
                # Convert response items to Playlist objects
                for item in response.get('items', []):
                    playlist = Playlist.from_youtube_response(item)
                    playlists.append(playlist)
                
                # Call progress callback if provided
                if progress_callback:
                    progress_callback(page_count, len(playlists))
                
                # Get next page
                request = self.youtube.playlists().list_next(request, response)
                
            logger.info(f"Total playlists fetched: {len(playlists)} across {page_count} pages")
                
        except HttpError as e:
            logger.error(f"Error fetching playlists: {e}")
            if e.resp.status == 403 and 'quotaExceeded' in str(e):
                raise QuotaExceededError("YouTube API quota exceeded")
            raise
        
        # Note: Special playlists (WL, HL) are now handled in the app layer
        # due to API restrictions since 2016
            
        return playlists
    
    def get_playlist_items(self,
                          playlist_id: str,
                          max_results: int = 50,
                          progress_callback: Optional[callable] = None) -> List[Video]:
        """Get all videos in a playlist.
        
        Args:
            playlist_id: ID of the playlist (including special playlists like 'WL', 'HL')
            max_results: Maximum results per page (max 50)
            progress_callback: Optional callback for progress updates (videos_loaded, total_expected)
            
        Returns:
            List of Video objects
        """
        videos = []
        
        # Handle special playlists with API limitations
        if playlist_id == "HL":
            # History playlist is no longer available via API
            logger.info("History playlist (HL) is not available via YouTube API v3. Use Google Takeout instead.")
            return []  # Not accessible via API
        
        if playlist_id == "WL":
            # Watch Later playlist returns empty since 2016 due to API restrictions
            logger.info("Watch Later playlist (WL) access is restricted by YouTube API since 2016. Will return empty.")
            # Continue with normal API call but it will return empty
        
        try:
            request = self.youtube.playlistItems().list(
                part='snippet,status',
                playlistId=playlist_id,
                maxResults=min(max_results, 50)
            )
            
            # Get total count if possible
            total_results = None
            page_count = 0
            
            # Handle pagination
            while request:
                self._track_quota('playlistItems.list')
                response = request.execute()
                page_count += 1
                
                # Get total results from first response
                if total_results is None and 'pageInfo' in response:
                    total_results = response['pageInfo'].get('totalResults', 0)
                
                # Convert response items to Video objects
                for item in response.get('items', []):
                    video = Video.from_playlist_item(item)
                    videos.append(video)
                
                # Call progress callback if provided
                if progress_callback and total_results:
                    progress_callback(len(videos), total_results)
                
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

    def update_video_position(self,
                             playlist_item_id: str,
                             playlist_id: str,
                             video_id: str,
                             new_position: int) -> None:
        """Update the position of a video within a playlist.

        Args:
            playlist_item_id: ID of the playlist item to reposition
            playlist_id: ID of the playlist containing the item
            video_id: ID of the video referenced by the item
            new_position: New zero-based position within the playlist
        """
        try:
            self._track_quota('playlistItems.update')

            # playlistItems.update requires the full snippet (playlistId +
            # resourceId), not just the new position, so YouTube can identify
            # which item to move and where.
            self.youtube.playlistItems().update(
                part='snippet',
                body={
                    'id': playlist_item_id,
                    'snippet': {
                        'playlistId': playlist_id,
                        'resourceId': {
                            'kind': 'youtube#video',
                            'videoId': video_id
                        },
                        'position': new_position
                    }
                }
            ).execute()

        except HttpError as e:
            logger.error(f"Error updating video position: {e}")
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
    
    def rename_playlist(self, playlist_id: str, new_title: str) -> None:
        """Rename a playlist.
        
        Args:
            playlist_id: ID of the playlist to rename
            new_title: New title for the playlist
        """
        self.update_playlist(playlist_id, title=new_title)
    
    def update_video_title(self, video_id: str, new_title: str, 
                          playlist_id: Optional[str] = None) -> None:
        """Update the title of a video.
        
        Note: YouTube API doesn't allow directly editing video titles unless you own
        the video. This method updates the video's metadata if you have permission,
        or can update the note/description in a playlist item if playlist_id is provided.
        
        Args:
            video_id: ID of the video to update
            new_title: New title for the video
            playlist_id: Optional playlist ID for playlist-specific title update
        """
        try:
            # First, try to update the video directly (only works if user owns it)
            try:
                self._track_quota('videos.list')
                # Get current video data
                response = self.youtube.videos().list(
                    part='snippet',
                    id=video_id
                ).execute()
                
                if response.get('items'):
                    video_data = response['items'][0]
                    video_data['snippet']['title'] = new_title
                    
                    self._track_quota('videos.update')
                    self.youtube.videos().update(
                        part='snippet',
                        body={
                            'id': video_id,
                            'snippet': video_data['snippet']
                        }
                    ).execute()
                    
                    logger.info(f"Updated video title: {video_id}")
                    return
                    
            except HttpError as e:
                if e.resp.status == 403:
                    logger.debug(f"Cannot update video {video_id} directly (not owner)")
                    # If playlist_id provided, we could potentially update playlist item note
                    if playlist_id:
                        logger.warning(
                            f"Cannot rename videos you don't own. "
                            f"Video {video_id} title cannot be changed."
                        )
                else:
                    raise
                    
        except HttpError as e:
            logger.error(f"Error updating video title: {e}")
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
    
    def get_videos_by_ids(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch video metadata for a list of video IDs.
        
        Args:
            video_ids: List of YouTube video IDs (max 50 per call)
            
        Returns:
            List of video metadata dictionaries
        """
        if not video_ids:
            return []
        
        # YouTube API allows max 50 IDs per request
        batch_size = 50
        all_videos = []
        
        for i in range(0, len(video_ids), batch_size):
            batch = video_ids[i:i + batch_size]
            
            try:
                self._track_quota('videos.list')
                
                response = self.youtube.videos().list(
                    part='snippet,contentDetails',
                    id=','.join(batch)
                ).execute()
                
                for item in response.get('items', []):
                    video_data = {
                        'video_id': item['id'],
                        'title': item['snippet'].get('title', ''),
                        'channel_title': item['snippet'].get('channelTitle', ''),
                        'description': item['snippet'].get('description', ''),
                        'published_at': item['snippet'].get('publishedAt', ''),
                        'duration': item['contentDetails'].get('duration', ''),
                        'thumbnail_url': item['snippet'].get('thumbnails', {}).get('default', {}).get('url', '')
                    }
                    all_videos.append(video_data)
                    
            except HttpError as e:
                logger.error(f"Error fetching video metadata: {e}")
                if e.resp.status == 403 and 'quotaExceeded' in str(e):
                    raise QuotaExceededError("YouTube API quota exceeded")
                # Continue with next batch even if one fails
                continue
        
        return all_videos