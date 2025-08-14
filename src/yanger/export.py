"""Export functionality for YouTube Ranger.

Exports playlists (real and virtual) to various formats.
"""
# Modified: 2025-08-14

import csv
import json
import yaml
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from .models import Playlist, Video
from .cache import PersistentCache
from .api_client import YouTubeAPIClient
from .auth import YouTubeAuth

logger = logging.getLogger(__name__)


class PlaylistExporter:
    """Export playlists to various formats."""
    
    def __init__(self, api_client: Optional[YouTubeAPIClient] = None,
                 cache: Optional[PersistentCache] = None):
        """Initialize exporter.
        
        Args:
            api_client: YouTube API client for real playlists
            cache: Cache for virtual playlists
        """
        self.api_client = api_client
        self.cache = cache or PersistentCache()
    
    def export_all(self, output_path: Path, format: str = 'json',
                   include_virtual: bool = True, 
                   include_real: bool = True) -> Dict[str, Any]:
        """Export all playlists to file.
        
        Args:
            output_path: Output file path
            format: Export format ('json', 'csv', 'yaml')
            include_virtual: Include virtual playlists
            include_real: Include real YouTube playlists
            
        Returns:
            Export summary dictionary
        """
        data = {
            'export_date': datetime.now().isoformat(),
            'format': format,
            'playlists': {
                'real': [],
                'virtual': []
            }
        }
        
        # Export real playlists
        if include_real and self.api_client:
            try:
                real_playlists = self._export_real_playlists()
                data['playlists']['real'] = real_playlists
                logger.info(f"Exported {len(real_playlists)} real playlists")
            except Exception as e:
                logger.error(f"Error exporting real playlists: {e}")
        
        # Export virtual playlists
        if include_virtual:
            virtual_playlists = self._export_virtual_playlists()
            data['playlists']['virtual'] = virtual_playlists
            logger.info(f"Exported {len(virtual_playlists)} virtual playlists")
        
        # Calculate statistics
        data['statistics'] = {
            'real_playlist_count': len(data['playlists']['real']),
            'virtual_playlist_count': len(data['playlists']['virtual']),
            'total_real_videos': sum(p['video_count'] for p in data['playlists']['real']),
            'total_virtual_videos': sum(p['video_count'] for p in data['playlists']['virtual'])
        }
        
        # Write to file based on format
        if format == 'json':
            self._write_json(data, output_path)
        elif format == 'yaml':
            self._write_yaml(data, output_path)
        elif format == 'csv':
            self._write_csv(data, output_path)
        else:
            raise ValueError(f"Unsupported format: {format}")
        
        return data['statistics']
    
    def _export_real_playlists(self) -> List[Dict]:
        """Export real YouTube playlists.
        
        Returns:
            List of playlist dictionaries
        """
        playlists = []
        
        # Get all playlists from API
        api_playlists = self.api_client.get_playlists(include_special=False)
        
        for playlist in api_playlists:
            # Skip special/virtual playlists
            if playlist.is_special or playlist.is_virtual:
                continue
            
            playlist_data = {
                'id': playlist.id,
                'title': playlist.title,
                'description': playlist.description,
                'video_count': playlist.item_count,
                'channel': playlist.channel_title,
                'privacy': playlist.privacy_status.value if playlist.privacy_status else 'private',
                'videos': []
            }
            
            # Get videos for this playlist
            try:
                videos = self.api_client.get_playlist_items(playlist.id)
                playlist_data['videos'] = [
                    {
                        'video_id': v.id,
                        'title': v.title,
                        'channel': v.channel_title,
                        'position': v.position
                    }
                    for v in videos
                ]
                playlist_data['video_count'] = len(videos)
            except Exception as e:
                logger.warning(f"Could not fetch videos for {playlist.title}: {e}")
            
            playlists.append(playlist_data)
        
        return playlists
    
    def _export_virtual_playlists(self) -> List[Dict]:
        """Export virtual playlists from database.
        
        Returns:
            List of playlist dictionaries
        """
        playlists = []
        
        # Get all virtual playlists
        virtual_playlists = self.cache.get_virtual_playlists()
        
        for vp in virtual_playlists:
            playlist_data = {
                'id': vp['id'],
                'title': vp['title'],
                'description': vp['description'],
                'source': vp['source'],
                'imported_at': vp['imported_at'],
                'video_count': vp['video_count'],
                'videos': []
            }
            
            # Get videos for this playlist
            videos = self.cache.get_virtual_videos(vp['id'])
            playlist_data['videos'] = [
                {
                    'video_id': v['video_id'],
                    'title': v['title'] or '',
                    'channel': v['channel_title'] or '',
                    'added_at': v['added_at'],
                    'position': v['position']
                }
                for v in videos
            ]
            
            playlists.append(playlist_data)
        
        return playlists
    
    def _write_json(self, data: Dict, output_path: Path) -> None:
        """Write data to JSON file.
        
        Args:
            data: Data to export
            output_path: Output file path
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Exported to JSON: {output_path}")
    
    def _write_yaml(self, data: Dict, output_path: Path) -> None:
        """Write data to YAML file.
        
        Args:
            data: Data to export
            output_path: Output file path
        """
        with open(output_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
        logger.info(f"Exported to YAML: {output_path}")
    
    def _write_csv(self, data: Dict, output_path: Path) -> None:
        """Write data to CSV files.
        
        Creates multiple CSV files:
        - playlists.csv: Playlist metadata
        - videos.csv: All videos with playlist info
        
        Args:
            data: Data to export
            output_path: Output directory path
        """
        # Ensure output path is a directory
        if output_path.suffix:
            output_path = output_path.parent / output_path.stem
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Write playlists CSV
        playlists_csv = output_path / "playlists.csv"
        with open(playlists_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'playlist_id', 'title', 'type', 'video_count', 'description'
            ])
            writer.writeheader()
            
            # Write real playlists
            for p in data['playlists']['real']:
                writer.writerow({
                    'playlist_id': p['id'],
                    'title': p['title'],
                    'type': 'real',
                    'video_count': p['video_count'],
                    'description': p['description'][:100]  # Truncate long descriptions
                })
            
            # Write virtual playlists
            for p in data['playlists']['virtual']:
                writer.writerow({
                    'playlist_id': p['id'],
                    'title': p['title'],
                    'type': 'virtual',
                    'video_count': p['video_count'],
                    'description': p['description'][:100]
                })
        
        # Write videos CSV
        videos_csv = output_path / "videos.csv"
        with open(videos_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'playlist_title', 'video_id', 'video_title', 'channel', 'position'
            ])
            writer.writeheader()
            
            # Write videos from all playlists
            for playlist_type in ['real', 'virtual']:
                for p in data['playlists'][playlist_type]:
                    for v in p['videos']:
                        writer.writerow({
                            'playlist_title': p['title'],
                            'video_id': v['video_id'],
                            'video_title': v.get('title', ''),
                            'channel': v.get('channel', ''),
                            'position': v.get('position', 0)
                        })
        
        logger.info(f"Exported to CSV: {output_path}/")
    
    def export_single_playlist(self, playlist_id: str, output_path: Path,
                              format: str = 'json', is_virtual: bool = False) -> int:
        """Export a single playlist.
        
        Args:
            playlist_id: Playlist ID
            output_path: Output file path
            format: Export format
            is_virtual: Whether this is a virtual playlist
            
        Returns:
            Number of videos exported
        """
        if is_virtual:
            # Get virtual playlist
            playlists = self.cache.get_virtual_playlists()
            playlist = next((p for p in playlists if p['id'] == playlist_id), None)
            
            if not playlist:
                raise ValueError(f"Virtual playlist {playlist_id} not found")
            
            videos = self.cache.get_virtual_videos(playlist_id)
            
            data = {
                'playlist': playlist,
                'videos': videos,
                'export_date': datetime.now().isoformat()
            }
        else:
            # Get real playlist
            if not self.api_client:
                raise ValueError("API client required for real playlists")
            
            videos = self.api_client.get_playlist_items(playlist_id)
            
            data = {
                'playlist_id': playlist_id,
                'videos': [
                    {
                        'video_id': v.id,
                        'title': v.title,
                        'channel': v.channel_title,
                        'position': v.position
                    }
                    for v in videos
                ],
                'export_date': datetime.now().isoformat()
            }
        
        # Write to file
        if format == 'json':
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        elif format == 'csv':
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                if data.get('videos'):
                    writer = csv.DictWriter(f, fieldnames=data['videos'][0].keys())
                    writer.writeheader()
                    writer.writerows(data['videos'])
        
        return len(data.get('videos', []))