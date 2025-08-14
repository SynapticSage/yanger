"""Google Takeout data parser for YouTube playlists and history.

Extracts Watch Later and History data from Google Takeout exports.
"""
# Modified: 2025-08-14

import csv
import json
import logging
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TakeoutVideo:
    """Represents a video from Google Takeout data."""
    video_id: str
    added_at: Optional[datetime] = None
    title: Optional[str] = None
    channel: Optional[str] = None
    duration_ms: Optional[int] = None
    playlist_name: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON export."""
        return {
            'video_id': self.video_id,
            'added_at': self.added_at.isoformat() if self.added_at else None,
            'title': self.title,
            'channel': self.channel,
            'duration_ms': self.duration_ms,
            'playlist_name': self.playlist_name
        }


@dataclass
class TakeoutPlaylist:
    """Represents a playlist extracted from Google Takeout."""
    name: str
    source: str  # 'watch_later', 'history', or playlist name
    videos: List[TakeoutVideo] = field(default_factory=list)
    extracted_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON export."""
        return {
            'name': self.name,
            'source': self.source,
            'video_count': len(self.videos),
            'videos': [v.to_dict() for v in self.videos],
            'extracted_at': self.extracted_at.isoformat()
        }


class TakeoutParser:
    """Parser for Google Takeout YouTube data."""
    
    # Paths within YouTube takeout structure
    YOUTUBE_FOLDER = "YouTube and YouTube Music"
    PLAYLISTS_FOLDER = "playlists"
    HISTORY_FOLDER = "history"
    METADATA_FOLDER = "video metadata"
    
    # Special file names
    WATCH_LATER_FILE = "Watch later-videos.csv"
    WATCH_HISTORY_FILE = "watch-history.html"
    SEARCH_HISTORY_FILE = "search-history.html"
    VIDEOS_METADATA_FILE = "videos.csv"
    
    def __init__(self):
        """Initialize the parser."""
        self.watch_later_videos: List[TakeoutVideo] = []
        self.history_videos: List[TakeoutVideo] = []
        self.playlist_videos: Dict[str, List[TakeoutVideo]] = {}
        self.video_metadata: Dict[str, Dict] = {}
        
    def process_path(self, path: Union[str, Path]) -> Dict[str, TakeoutPlaylist]:
        """Process a takeout zip file or directory.
        
        Args:
            path: Path to zip file or extracted directory
            
        Returns:
            Dictionary of playlist name to TakeoutPlaylist objects
        """
        path = Path(path)
        
        if not path.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")
        
        if path.is_file() and path.suffix == '.zip':
            return self._process_zip(path)
        elif path.is_dir():
            return self._process_directory(path)
        else:
            raise ValueError(f"Path must be a .zip file or directory: {path}")
    
    def process_multiple(self, paths: List[Union[str, Path]]) -> Dict[str, TakeoutPlaylist]:
        """Process multiple takeout exports and merge results.
        
        Args:
            paths: List of paths to zip files or directories
            
        Returns:
            Merged dictionary of playlists
        """
        all_playlists = {}
        
        for path in paths:
            try:
                logger.info(f"Processing takeout: {path}")
                playlists = self.process_path(path)
                
                # Merge playlists
                for name, playlist in playlists.items():
                    if name in all_playlists:
                        # Merge videos, avoiding duplicates
                        existing_ids = {v.video_id for v in all_playlists[name].videos}
                        new_videos = [v for v in playlist.videos if v.video_id not in existing_ids]
                        all_playlists[name].videos.extend(new_videos)
                        logger.info(f"Merged {len(new_videos)} new videos into {name}")
                    else:
                        all_playlists[name] = playlist
                        
            except Exception as e:
                logger.error(f"Error processing {path}: {e}")
                continue
        
        return all_playlists
    
    def _process_zip(self, zip_path: Path) -> Dict[str, TakeoutPlaylist]:
        """Process a takeout zip file.
        
        Args:
            zip_path: Path to the zip file
            
        Returns:
            Dictionary of playlists
        """
        logger.info(f"Extracting zip file: {zip_path}")
        
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Find YouTube folder in zip
            youtube_folder = None
            for name in zf.namelist():
                if self.YOUTUBE_FOLDER in name:
                    youtube_folder = name.split(self.YOUTUBE_FOLDER)[0] + self.YOUTUBE_FOLDER
                    break
            
            if not youtube_folder:
                logger.warning(f"No YouTube data found in {zip_path}")
                return {}
            
            # Extract relevant files
            playlists = {}
            
            # Process Watch Later
            watch_later_path = f"{youtube_folder}/{self.PLAYLISTS_FOLDER}/{self.WATCH_LATER_FILE}"
            if watch_later_path in zf.namelist():
                with zf.open(watch_later_path) as f:
                    content = f.read().decode('utf-8')
                    videos = self._parse_playlist_csv_content(content, "Watch Later")
                    if videos:
                        playlists['Watch Later (Imported)'] = TakeoutPlaylist(
                            name='Watch Later (Imported)',
                            source='watch_later',
                            videos=videos
                        )
                        logger.info(f"Found {len(videos)} videos in Watch Later")
            
            # Process Watch History
            history_path = f"{youtube_folder}/{self.HISTORY_FOLDER}/{self.WATCH_HISTORY_FILE}"
            if history_path in zf.namelist():
                with zf.open(history_path) as f:
                    content = f.read().decode('utf-8')
                    videos = self._parse_watch_history_content(content)
                    if videos:
                        playlists['History (Imported)'] = TakeoutPlaylist(
                            name='History (Imported)',
                            source='history',
                            videos=videos
                        )
                        logger.info(f"Found {len(videos)} videos in History")
            
            # Process other playlists
            playlist_folder = f"{youtube_folder}/{self.PLAYLISTS_FOLDER}/"
            for file_name in zf.namelist():
                if file_name.startswith(playlist_folder) and file_name.endswith('-videos.csv'):
                    if self.WATCH_LATER_FILE not in file_name:  # Skip Watch Later
                        playlist_name = Path(file_name).stem.replace('-videos', '')
                        with zf.open(file_name) as f:
                            content = f.read().decode('utf-8')
                            videos = self._parse_playlist_csv_content(content, playlist_name)
                            if videos:
                                playlists[playlist_name] = TakeoutPlaylist(
                                    name=playlist_name,
                                    source='playlist',
                                    videos=videos
                                )
                                logger.info(f"Found {len(videos)} videos in playlist: {playlist_name}")
            
            return playlists
    
    def _process_directory(self, dir_path: Path) -> Dict[str, TakeoutPlaylist]:
        """Process an extracted takeout directory.
        
        Args:
            dir_path: Path to the directory
            
        Returns:
            Dictionary of playlists
        """
        logger.info(f"Processing directory: {dir_path}")
        
        # Find YouTube folder
        youtube_path = dir_path / self.YOUTUBE_FOLDER
        if not youtube_path.exists():
            # Try Takeout/YouTube structure
            if (dir_path / "Takeout" / self.YOUTUBE_FOLDER).exists():
                youtube_path = dir_path / "Takeout" / self.YOUTUBE_FOLDER
            else:
                logger.warning(f"No YouTube data found in {dir_path}")
                return {}
        
        playlists = {}
        
        # Process Watch Later
        watch_later_path = youtube_path / self.PLAYLISTS_FOLDER / self.WATCH_LATER_FILE
        if watch_later_path.exists():
            videos = self._parse_playlist_csv(watch_later_path, "Watch Later")
            if videos:
                playlists['Watch Later (Imported)'] = TakeoutPlaylist(
                    name='Watch Later (Imported)',
                    source='watch_later',
                    videos=videos
                )
                logger.info(f"Found {len(videos)} videos in Watch Later")
        
        # Process Watch History
        history_path = youtube_path / self.HISTORY_FOLDER / self.WATCH_HISTORY_FILE
        if history_path.exists():
            videos = self._parse_watch_history(history_path)
            if videos:
                playlists['History (Imported)'] = TakeoutPlaylist(
                    name='History (Imported)',
                    source='history',
                    videos=videos
                )
                logger.info(f"Found {len(videos)} videos in History")
        
        # Process other playlists
        playlists_dir = youtube_path / self.PLAYLISTS_FOLDER
        if playlists_dir.exists():
            for csv_file in playlists_dir.glob("*-videos.csv"):
                if self.WATCH_LATER_FILE not in csv_file.name:  # Skip Watch Later
                    playlist_name = csv_file.stem.replace('-videos', '')
                    videos = self._parse_playlist_csv(csv_file, playlist_name)
                    if videos:
                        playlists[playlist_name] = TakeoutPlaylist(
                            name=playlist_name,
                            source='playlist',
                            videos=videos
                        )
                        logger.info(f"Found {len(videos)} videos in playlist: {playlist_name}")
        
        # Load video metadata if available
        metadata_path = youtube_path / self.METADATA_FOLDER / self.VIDEOS_METADATA_FILE
        if metadata_path.exists():
            self._load_video_metadata(metadata_path)
            self._enrich_with_metadata(playlists)
        
        return playlists
    
    def _parse_playlist_csv(self, csv_path: Path, playlist_name: str) -> List[TakeoutVideo]:
        """Parse a playlist CSV file.
        
        Args:
            csv_path: Path to the CSV file
            playlist_name: Name of the playlist
            
        Returns:
            List of TakeoutVideo objects
        """
        with open(csv_path, 'r', encoding='utf-8') as f:
            return self._parse_playlist_csv_content(f.read(), playlist_name)
    
    def _parse_playlist_csv_content(self, content: str, playlist_name: str) -> List[TakeoutVideo]:
        """Parse playlist CSV content.
        
        Args:
            content: CSV content as string
            playlist_name: Name of the playlist
            
        Returns:
            List of TakeoutVideo objects
        """
        videos = []
        
        try:
            reader = csv.DictReader(content.splitlines())
            for row in reader:
                video_id = row.get('Video ID', '').strip()
                if video_id and self._is_valid_video_id(video_id):
                    timestamp_str = row.get('Playlist Video Creation Timestamp', '')
                    added_at = None
                    if timestamp_str:
                        try:
                            added_at = datetime.fromisoformat(timestamp_str.replace('+00:00', '+00:00'))
                        except:
                            pass
                    
                    videos.append(TakeoutVideo(
                        video_id=video_id,
                        added_at=added_at,
                        playlist_name=playlist_name
                    ))
        except Exception as e:
            logger.error(f"Error parsing playlist CSV: {e}")
        
        return videos
    
    def _parse_watch_history(self, html_path: Path) -> List[TakeoutVideo]:
        """Parse watch history HTML file.
        
        Args:
            html_path: Path to the HTML file
            
        Returns:
            List of TakeoutVideo objects
        """
        with open(html_path, 'r', encoding='utf-8') as f:
            return self._parse_watch_history_content(f.read())
    
    def _parse_watch_history_content(self, content: str) -> List[TakeoutVideo]:
        """Parse watch history HTML content.
        
        Args:
            content: HTML content as string
            
        Returns:
            List of TakeoutVideo objects
        """
        videos = []
        
        # Extract video IDs from watch URLs
        pattern = r'watch\?v=([a-zA-Z0-9_-]{11})'
        matches = re.findall(pattern, content)
        
        # Remove duplicates while preserving order
        seen = set()
        for video_id in matches:
            if video_id not in seen and self._is_valid_video_id(video_id):
                seen.add(video_id)
                videos.append(TakeoutVideo(
                    video_id=video_id,
                    playlist_name="History"
                ))
        
        return videos
    
    def _load_video_metadata(self, metadata_path: Path) -> None:
        """Load video metadata from CSV.
        
        Args:
            metadata_path: Path to videos.csv metadata file
        """
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    video_id = row.get('Video ID', '')
                    if video_id:
                        self.video_metadata[video_id] = {
                            'title': row.get('Video Title (Original)', ''),
                            'channel_id': row.get('Channel ID', ''),
                            'duration_ms': row.get('Approx Duration (ms)', ''),
                            'privacy': row.get('Privacy', ''),
                            'created_at': row.get('Video Create Timestamp', '')
                        }
            logger.info(f"Loaded metadata for {len(self.video_metadata)} videos")
        except Exception as e:
            logger.error(f"Error loading video metadata: {e}")
    
    def _enrich_with_metadata(self, playlists: Dict[str, TakeoutPlaylist]) -> None:
        """Enrich videos with metadata if available.
        
        Args:
            playlists: Dictionary of playlists to enrich
        """
        if not self.video_metadata:
            return
        
        for playlist in playlists.values():
            for video in playlist.videos:
                if video.video_id in self.video_metadata:
                    metadata = self.video_metadata[video.video_id]
                    video.title = metadata.get('title')
                    if metadata.get('duration_ms'):
                        try:
                            video.duration_ms = int(metadata['duration_ms'])
                        except:
                            pass
    
    def _is_valid_video_id(self, video_id: str) -> bool:
        """Check if a video ID is valid.
        
        Args:
            video_id: YouTube video ID
            
        Returns:
            True if valid
        """
        # YouTube video IDs are 11 characters long
        return bool(video_id and len(video_id) == 11 and re.match(r'^[a-zA-Z0-9_-]+$', video_id))
    
    def export_to_json(self, playlists: Dict[str, TakeoutPlaylist], output_path: Path) -> None:
        """Export playlists to JSON file.
        
        Args:
            playlists: Dictionary of playlists
            output_path: Path for output JSON file
        """
        data = {
            'export_date': datetime.now().isoformat(),
            'playlist_count': len(playlists),
            'total_videos': sum(len(p.videos) for p in playlists.values()),
            'playlists': {name: p.to_dict() for name, p in playlists.items()}
        }
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Exported {len(playlists)} playlists to {output_path}")