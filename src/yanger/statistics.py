"""Playlist statistics and analytics for YouTube Ranger.

Provides comprehensive analysis of playlists including duration, channel
distribution, temporal patterns, and more.
"""
# Created: 2025-09-13

import re
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from collections import Counter, defaultdict
from dataclasses import dataclass
import logging

from .models import Video, Playlist


logger = logging.getLogger(__name__)


@dataclass
class PlaylistStats:
    """Container for playlist statistics."""
    total_videos: int = 0
    total_duration_seconds: int = 0
    average_duration_seconds: float = 0
    median_duration_seconds: float = 0
    shortest_video: Optional[Video] = None
    longest_video: Optional[Video] = None
    
    # Channel statistics
    unique_channels: int = 0
    top_channels: List[Tuple[str, int]] = None
    channel_distribution: Dict[str, int] = None
    
    # Temporal statistics
    oldest_video: Optional[Video] = None
    newest_video: Optional[Video] = None
    videos_by_year: Dict[int, int] = None
    videos_by_month: Dict[str, int] = None
    
    # View statistics (if available)
    total_views: int = 0
    average_views: float = 0
    most_viewed: Optional[Video] = None
    least_viewed: Optional[Video] = None
    
    # Duration distribution
    duration_buckets: Dict[str, int] = None
    
    def __post_init__(self):
        """Initialize empty collections if not provided."""
        if self.top_channels is None:
            self.top_channels = []
        if self.channel_distribution is None:
            self.channel_distribution = {}
        if self.videos_by_year is None:
            self.videos_by_year = {}
        if self.videos_by_month is None:
            self.videos_by_month = {}
        if self.duration_buckets is None:
            self.duration_buckets = {}


class PlaylistAnalyzer:
    """Analyzes playlists to generate statistics."""
    
    def analyze(self, videos: List[Video], playlist_name: str = "Current Playlist") -> PlaylistStats:
        """Analyze a list of videos and generate statistics.
        
        Args:
            videos: List of videos to analyze
            playlist_name: Name of the playlist (for display)
            
        Returns:
            PlaylistStats object with comprehensive statistics
        """
        stats = PlaylistStats()
        
        if not videos:
            return stats
        
        stats.total_videos = len(videos)
        
        # Parse durations and calculate basic stats
        self._calculate_duration_stats(videos, stats)
        
        # Analyze channels
        self._analyze_channels(videos, stats)
        
        # Analyze temporal distribution
        self._analyze_temporal(videos, stats)
        
        # Analyze views (if available)
        self._analyze_views(videos, stats)
        
        # Create duration distribution buckets
        self._create_duration_buckets(videos, stats)
        
        return stats
    
    def _calculate_duration_stats(self, videos: List[Video], stats: PlaylistStats):
        """Calculate duration-related statistics."""
        durations = []
        
        for video in videos:
            if video.duration:
                seconds = self._parse_duration(video.duration)
                if seconds > 0:
                    durations.append((seconds, video))
        
        if not durations:
            return
        
        durations.sort(key=lambda x: x[0])
        
        # Basic statistics
        total_seconds = sum(d[0] for d in durations)
        stats.total_duration_seconds = total_seconds
        stats.average_duration_seconds = total_seconds / len(durations)
        
        # Median
        mid = len(durations) // 2
        if len(durations) % 2 == 0:
            stats.median_duration_seconds = (durations[mid-1][0] + durations[mid][0]) / 2
        else:
            stats.median_duration_seconds = durations[mid][0]
        
        # Shortest and longest
        stats.shortest_video = durations[0][1]
        stats.longest_video = durations[-1][1]
    
    def _analyze_channels(self, videos: List[Video], stats: PlaylistStats):
        """Analyze channel distribution."""
        channel_counts = Counter()
        
        for video in videos:
            if video.channel_title:
                channel_counts[video.channel_title] += 1
        
        if channel_counts:
            stats.unique_channels = len(channel_counts)
            stats.channel_distribution = dict(channel_counts)
            stats.top_channels = channel_counts.most_common(10)
    
    def _analyze_temporal(self, videos: List[Video], stats: PlaylistStats):
        """Analyze temporal distribution of videos."""
        dated_videos = []
        year_counts = defaultdict(int)
        month_counts = defaultdict(int)
        
        for video in videos:
            if video.published_at:
                dated_videos.append(video)
                year_counts[video.published_at.year] += 1
                month_key = video.published_at.strftime("%Y-%m")
                month_counts[month_key] += 1
        
        if dated_videos:
            # Sort by date
            dated_videos.sort(key=lambda v: v.published_at)
            
            stats.oldest_video = dated_videos[0]
            stats.newest_video = dated_videos[-1]
            stats.videos_by_year = dict(year_counts)
            stats.videos_by_month = dict(month_counts)
    
    def _analyze_views(self, videos: List[Video], stats: PlaylistStats):
        """Analyze view counts if available."""
        viewed_videos = []
        
        for video in videos:
            if video.view_count is not None and video.view_count >= 0:
                viewed_videos.append((video.view_count, video))
        
        if not viewed_videos:
            return
        
        viewed_videos.sort(key=lambda x: x[0])
        
        total_views = sum(v[0] for v in viewed_videos)
        stats.total_views = total_views
        stats.average_views = total_views / len(viewed_videos)
        stats.least_viewed = viewed_videos[0][1]
        stats.most_viewed = viewed_videos[-1][1]
    
    def _create_duration_buckets(self, videos: List[Video], stats: PlaylistStats):
        """Create duration distribution buckets."""
        buckets = {
            "< 1 min": 0,
            "1-5 min": 0,
            "5-10 min": 0,
            "10-30 min": 0,
            "30-60 min": 0,
            "> 1 hour": 0
        }
        
        for video in videos:
            if video.duration:
                seconds = self._parse_duration(video.duration)
                if seconds < 60:
                    buckets["< 1 min"] += 1
                elif seconds < 300:
                    buckets["1-5 min"] += 1
                elif seconds < 600:
                    buckets["5-10 min"] += 1
                elif seconds < 1800:
                    buckets["10-30 min"] += 1
                elif seconds < 3600:
                    buckets["30-60 min"] += 1
                else:
                    buckets["> 1 hour"] += 1
        
        stats.duration_buckets = buckets
    
    def _parse_duration(self, duration_str: str) -> int:
        """Parse ISO 8601 duration to seconds."""
        match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
        if match:
            hours = int(match.group(1) or 0)
            minutes = int(match.group(2) or 0)
            seconds = int(match.group(3) or 0)
            return hours * 3600 + minutes * 60 + seconds
        return 0
    
    def format_stats(self, stats: PlaylistStats, detailed: bool = False) -> str:
        """Format statistics for display.
        
        Args:
            stats: PlaylistStats object
            detailed: Whether to include detailed breakdown
            
        Returns:
            Formatted string for display
        """
        lines = []
        lines.append("═" * 60)
        lines.append("📊 PLAYLIST STATISTICS")
        lines.append("═" * 60)
        
        # Basic stats
        lines.append(f"\n📹 Videos: {stats.total_videos}")
        
        if stats.total_duration_seconds > 0:
            total_time = self._format_duration(stats.total_duration_seconds)
            avg_time = self._format_duration(stats.average_duration_seconds)
            median_time = self._format_duration(stats.median_duration_seconds)
            
            lines.append(f"⏱️  Total Duration: {total_time}")
            lines.append(f"   Average: {avg_time} | Median: {median_time}")
            
            if stats.shortest_video and stats.longest_video:
                shortest_duration = self._parse_duration(stats.shortest_video.duration)
                longest_duration = self._parse_duration(stats.longest_video.duration)
                lines.append(f"   Shortest: {self._format_duration(shortest_duration)} - {stats.shortest_video.title[:40]}")
                lines.append(f"   Longest: {self._format_duration(longest_duration)} - {stats.longest_video.title[:40]}")
        
        # Channel stats
        if stats.unique_channels > 0:
            lines.append(f"\n📺 Channels: {stats.unique_channels} unique")
            if stats.top_channels:
                lines.append("   Top Channels:")
                for channel, count in stats.top_channels[:5]:
                    percentage = (count / stats.total_videos) * 100
                    lines.append(f"   • {channel}: {count} videos ({percentage:.1f}%)")
        
        # Temporal stats
        if stats.oldest_video and stats.newest_video:
            lines.append(f"\n📅 Date Range:")
            lines.append(f"   Oldest: {stats.oldest_video.published_at.strftime('%Y-%m-%d')} - {stats.oldest_video.title[:40]}")
            lines.append(f"   Newest: {stats.newest_video.published_at.strftime('%Y-%m-%d')} - {stats.newest_video.title[:40]}")
            
            if detailed and stats.videos_by_year:
                lines.append("   Videos by Year:")
                for year in sorted(stats.videos_by_year.keys(), reverse=True)[:5]:
                    count = stats.videos_by_year[year]
                    lines.append(f"   • {year}: {count} videos")
        
        # View stats
        if stats.total_views > 0:
            lines.append(f"\n👁️  Views:")
            lines.append(f"   Total: {self._format_number(stats.total_views)}")
            lines.append(f"   Average: {self._format_number(int(stats.average_views))}")
            
            if stats.most_viewed and stats.least_viewed:
                lines.append(f"   Most Viewed: {self._format_number(stats.most_viewed.view_count)} - {stats.most_viewed.title[:40]}")
                lines.append(f"   Least Viewed: {self._format_number(stats.least_viewed.view_count)} - {stats.least_viewed.title[:40]}")
        
        # Duration distribution
        if detailed and stats.duration_buckets:
            lines.append(f"\n⏱️  Duration Distribution:")
            for bucket, count in stats.duration_buckets.items():
                if count > 0:
                    percentage = (count / stats.total_videos) * 100
                    bar = "█" * int(percentage / 2)
                    lines.append(f"   {bucket:12} {count:4} videos {bar} {percentage:.1f}%")
        
        lines.append("\n" + "═" * 60)
        
        return "\n".join(lines)
    
    def _format_duration(self, seconds: float) -> str:
        """Format duration in seconds to human-readable string."""
        seconds = int(seconds)
        if seconds < 60:
            return f"{seconds}s"
        elif seconds < 3600:
            minutes = seconds // 60
            secs = seconds % 60
            return f"{minutes}m {secs}s"
        else:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    
    def _format_number(self, num: int) -> str:
        """Format large numbers with commas or shorthand."""
        if num >= 1000000000:
            return f"{num / 1000000000:.1f}B"
        elif num >= 1000000:
            return f"{num / 1000000:.1f}M"
        elif num >= 1000:
            return f"{num / 1000:.1f}K"
        else:
            return str(num)