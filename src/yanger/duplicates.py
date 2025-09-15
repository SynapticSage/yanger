"""Duplicate detection system for YouTube Ranger.

Finds duplicate videos within and across playlists using various detection methods.
"""
# Created: 2025-09-13

import re
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass
from difflib import SequenceMatcher
import logging

from .models import Video, Playlist


logger = logging.getLogger(__name__)


@dataclass
class DuplicateGroup:
    """Group of duplicate videos."""
    video_id: str  # The YouTube video ID (same for all duplicates)
    videos: List[Tuple[Video, str]]  # List of (video, playlist_name) tuples
    similarity_score: float = 1.0  # 1.0 for exact matches, < 1.0 for fuzzy matches
    match_type: str = "exact"  # "exact", "fuzzy_title", "channel_duration"


class DuplicateDetector:
    """Detects duplicate videos in playlists."""
    
    def __init__(self, fuzzy_threshold: float = 0.85):
        """Initialize duplicate detector.
        
        Args:
            fuzzy_threshold: Minimum similarity score for fuzzy title matching (0.0-1.0)
        """
        self.fuzzy_threshold = fuzzy_threshold
    
    def find_duplicates(self, videos: List[Video], 
                       playlist_name: str = "Current Playlist") -> List[DuplicateGroup]:
        """Find duplicates within a single playlist.
        
        Args:
            videos: List of videos to check
            playlist_name: Name of the playlist
            
        Returns:
            List of DuplicateGroup objects
        """
        duplicates = []
        
        # Find exact duplicates (same video ID)
        exact_dupes = self._find_exact_duplicates(videos, playlist_name)
        duplicates.extend(exact_dupes)
        
        # Find fuzzy duplicates (similar titles)
        fuzzy_dupes = self._find_fuzzy_duplicates(videos, playlist_name)
        duplicates.extend(fuzzy_dupes)
        
        return duplicates
    
    def find_duplicates_across(self, playlists: List[Tuple[Playlist, List[Video]]]) -> List[DuplicateGroup]:
        """Find duplicates across multiple playlists.
        
        Args:
            playlists: List of (playlist, videos) tuples
            
        Returns:
            List of DuplicateGroup objects for videos appearing in multiple playlists
        """
        # Build a map of video_id -> [(video, playlist_name)]
        video_map: Dict[str, List[Tuple[Video, str]]] = {}
        
        for playlist, videos in playlists:
            for video in videos:
                if video.id not in video_map:
                    video_map[video.id] = []
                video_map[video.id].append((video, playlist.title))
        
        # Find videos that appear in multiple playlists
        duplicates = []
        for video_id, occurrences in video_map.items():
            if len(occurrences) > 1:
                # This video appears in multiple playlists
                group = DuplicateGroup(
                    video_id=video_id,
                    videos=occurrences,
                    similarity_score=1.0,
                    match_type="exact_cross_playlist"
                )
                duplicates.append(group)
        
        # Sort by number of occurrences
        duplicates.sort(key=lambda g: len(g.videos), reverse=True)
        
        return duplicates
    
    def _find_exact_duplicates(self, videos: List[Video], playlist_name: str) -> List[DuplicateGroup]:
        """Find exact duplicates based on video ID."""
        video_id_map: Dict[str, List[Video]] = {}
        
        for video in videos:
            if video.id not in video_id_map:
                video_id_map[video.id] = []
            video_id_map[video.id].append(video)
        
        duplicates = []
        for video_id, video_list in video_id_map.items():
            if len(video_list) > 1:
                # Found duplicates
                group = DuplicateGroup(
                    video_id=video_id,
                    videos=[(v, playlist_name) for v in video_list],
                    similarity_score=1.0,
                    match_type="exact"
                )
                duplicates.append(group)
        
        return duplicates
    
    def _find_fuzzy_duplicates(self, videos: List[Video], playlist_name: str) -> List[DuplicateGroup]:
        """Find potential duplicates based on similar titles."""
        duplicates = []
        processed: Set[int] = set()
        
        for i, video1 in enumerate(videos):
            if i in processed:
                continue
            
            similar_videos = []
            
            for j, video2 in enumerate(videos[i+1:], start=i+1):
                if j in processed:
                    continue
                
                # Skip if same video ID (already handled in exact duplicates)
                if video1.id == video2.id:
                    continue
                
                # Calculate title similarity
                similarity = self._calculate_title_similarity(video1.title, video2.title)
                
                if similarity >= self.fuzzy_threshold:
                    similar_videos.append((video2, similarity))
                    processed.add(j)
            
            if similar_videos:
                # Create a duplicate group
                all_videos = [(video1, playlist_name)]
                all_videos.extend([(v, playlist_name) for v, _ in similar_videos])
                
                avg_similarity = sum(s for _, s in similar_videos) / len(similar_videos)
                
                group = DuplicateGroup(
                    video_id=video1.id,  # Use first video's ID as reference
                    videos=all_videos,
                    similarity_score=avg_similarity,
                    match_type="fuzzy_title"
                )
                duplicates.append(group)
                processed.add(i)
        
        return duplicates
    
    def _calculate_title_similarity(self, title1: str, title2: str) -> float:
        """Calculate similarity between two titles.
        
        Returns:
            Similarity score between 0.0 and 1.0
        """
        # Normalize titles
        norm1 = self._normalize_title(title1)
        norm2 = self._normalize_title(title2)
        
        # Use SequenceMatcher for fuzzy matching
        return SequenceMatcher(None, norm1, norm2).ratio()
    
    def _normalize_title(self, title: str) -> str:
        """Normalize title for comparison."""
        # Convert to lowercase
        title = title.lower()
        
        # Remove common video suffixes
        patterns = [
            r'\(official.*?\)',
            r'\[official.*?\]',
            r'\(lyrics?\)',
            r'\[lyrics?\]',
            r'\(audio\)',
            r'\[audio\]',
            r'\(hd\)',
            r'\[hd\]',
            r'\(4k\)',
            r'\[4k\]',
            r'\(full.*?\)',
            r'\[full.*?\]',
            r'- official.*?video',
            r'ft\.',
            r'feat\.',
        ]
        
        for pattern in patterns:
            title = re.sub(pattern, '', title, flags=re.IGNORECASE)
        
        # Remove extra whitespace
        title = ' '.join(title.split())
        
        return title.strip()
    
    def format_duplicates(self, duplicates: List[DuplicateGroup], 
                         show_positions: bool = True) -> str:
        """Format duplicate groups for display.
        
        Args:
            duplicates: List of DuplicateGroup objects
            show_positions: Whether to show playlist positions
            
        Returns:
            Formatted string for display
        """
        if not duplicates:
            return "✨ No duplicates found!"
        
        lines = []
        lines.append("═" * 60)
        lines.append("🔍 DUPLICATE VIDEOS FOUND")
        lines.append("═" * 60)
        
        for i, group in enumerate(duplicates, 1):
            lines.append(f"\n{i}. Duplicate Group ({group.match_type})")
            
            if group.match_type == "fuzzy_title":
                lines.append(f"   Similarity: {group.similarity_score:.0%}")
            
            # Show first video as reference
            first_video, first_playlist = group.videos[0]
            lines.append(f"   📹 {first_video.title}")
            if first_video.channel_title:
                lines.append(f"   📺 {first_video.channel_title}")
            
            lines.append(f"\n   Found in:")
            for video, playlist in group.videos:
                pos_info = f" (position {video.position})" if show_positions and video.position else ""
                lines.append(f"   • {playlist}{pos_info}")
                if video.title != first_video.title:
                    # Show title if different (for fuzzy matches)
                    lines.append(f"     Title: {video.title}")
        
        lines.append("\n" + "═" * 60)
        lines.append(f"Total: {len(duplicates)} duplicate groups found")
        
        # Calculate total redundant videos
        total_redundant = sum(len(g.videos) - 1 for g in duplicates)
        if total_redundant > 0:
            lines.append(f"Redundant videos that could be removed: {total_redundant}")
        
        return "\n".join(lines)
    
    def get_videos_to_remove(self, group: DuplicateGroup, 
                            keep_strategy: str = "first") -> List[Tuple[Video, str]]:
        """Get list of videos to remove from a duplicate group.
        
        Args:
            group: DuplicateGroup to process
            keep_strategy: Strategy for which video to keep
                          "first" - Keep first occurrence
                          "last" - Keep last occurrence
                          "shortest_title" - Keep video with shortest title
                          
        Returns:
            List of (video, playlist_name) tuples to remove
        """
        if len(group.videos) <= 1:
            return []
        
        if keep_strategy == "first":
            return group.videos[1:]
        elif keep_strategy == "last":
            return group.videos[:-1]
        elif keep_strategy == "shortest_title":
            # Sort by title length and keep the shortest
            sorted_videos = sorted(group.videos, key=lambda x: len(x[0].title))
            return sorted_videos[1:]
        else:
            # Default to keeping first
            return group.videos[1:]