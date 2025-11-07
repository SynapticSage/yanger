"""Bulk edit functionality for reorganizing playlists and videos.

Allows editing playlist structure in a text editor using markdown format.
"""
# Created: 2025-09-22

import os
import re
import tempfile
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set
from pathlib import Path
import logging

from .models import Playlist, Video

logger = logging.getLogger(__name__)


@dataclass
class PlaylistNode:
    """Represents a playlist in the edit structure."""
    playlist: Playlist
    videos: List[Video] = field(default_factory=list)
    original_position: int = 0
    new_position: Optional[int] = None


@dataclass
class VideoMove:
    """Represents a video move operation."""
    video: Video
    source_playlist_id: str
    target_playlist_id: str
    new_position: int


@dataclass
class VideoReorder:
    """Represents reordering within a playlist."""
    video: Video
    playlist_id: str
    old_position: int
    new_position: int


@dataclass
class ItemRename:
    """Represents a rename operation."""
    item_type: str  # 'playlist' or 'video'
    item_id: str
    old_name: str
    new_name: str


@dataclass
class BulkEditChanges:
    """Container for all changes detected in bulk edit."""
    moves: List[VideoMove] = field(default_factory=list)
    reorders: List[VideoReorder] = field(default_factory=list)
    renames: List[ItemRename] = field(default_factory=list)
    deletions: List[Tuple[Video, str]] = field(default_factory=list)  # (video, playlist_id)

    def is_empty(self) -> bool:
        """Check if there are any changes."""
        return not (self.moves or self.reorders or self.renames or self.deletions)

    def summary(self) -> str:
        """Get a summary of changes."""
        parts = []
        if self.moves:
            parts.append(f"{len(self.moves)} moves")
        if self.reorders:
            parts.append(f"{len(self.reorders)} reorders")
        if self.renames:
            parts.append(f"{len(self.renames)} renames")
        if self.deletions:
            parts.append(f"{len(self.deletions)} deletions")
        return ", ".join(parts) if parts else "No changes"


class BulkEditGenerator:
    """Generates markdown representation of playlists and videos."""

    @staticmethod
    def generate(playlists: List[Playlist],
                 videos_by_playlist: Dict[str, List[Video]]) -> str:
        """Generate markdown from playlists and videos.

        Args:
            playlists: List of playlists
            videos_by_playlist: Dict mapping playlist IDs to video lists

        Returns:
            Markdown string
        """
        lines = ["# YouTube Playlists", ""]
        lines.append("# Edit the structure below to reorganize your playlists")
        lines.append("# - Move videos between playlists by cutting and pasting lines")
        lines.append("# - Reorder videos by moving lines up or down")
        lines.append("# - Delete videos by removing lines")
        lines.append("# - Rename items by editing the text (before the <!--)")
        lines.append("# - DO NOT modify the <!-- id:... --> comments")
        lines.append("")

        for playlist in playlists:
            # Skip virtual playlists
            if playlist.is_virtual:
                continue

            # Playlist header
            lines.append(f"- {playlist.title} <!-- id:{playlist.id} -->")

            # Videos in playlist
            videos = videos_by_playlist.get(playlist.id, [])
            for i, video in enumerate(videos):
                # Include both video ID and playlist item ID for tracking
                lines.append(
                    f"  - {video.title} "
                    f"<!-- id:{video.id},item:{video.playlist_item_id} -->"
                )

        return "\n".join(lines)


class BulkEditParser:
    """Parses edited markdown back into structure."""

    # Regex patterns
    PLAYLIST_PATTERN = re.compile(
        r'^- (.+?) <!-- id:([^>]+) -->$'
    )
    VIDEO_PATTERN = re.compile(
        r'^  - (.+?) <!-- id:([^,]+),item:([^>]+) -->$'
    )

    def parse(self, content: str,
              original_playlists: List[Playlist],
              original_videos_by_playlist: Dict[str, List[Video]]) -> BulkEditChanges:
        """Parse edited markdown and detect changes.

        Args:
            content: Edited markdown content
            original_playlists: Original playlist objects
            original_videos_by_playlist: Original video organization

        Returns:
            BulkEditChanges with all detected changes
        """
        changes = BulkEditChanges()

        # Build lookup maps
        playlists_by_id = {p.id: p for p in original_playlists}
        all_videos = {}  # video_id -> (Video, original_playlist_id)
        for playlist_id, videos in original_videos_by_playlist.items():
            for video in videos:
                all_videos[video.id] = (video, playlist_id)

        # Parse edited structure
        edited_structure = {}  # playlist_id -> list of (video_id, new_title, position)
        current_playlist_id = None
        current_position = 0
        edited_playlist_titles = {}  # playlist_id -> new_title

        lines = content.split('\n')
        for line in lines:
            # Skip empty lines and comments
            if not line.strip() or line.strip().startswith('#'):
                continue

            # Check for playlist line
            playlist_match = self.PLAYLIST_PATTERN.match(line)
            if playlist_match:
                new_title = playlist_match.group(1)
                playlist_id = playlist_match.group(2)
                current_playlist_id = playlist_id
                current_position = 0

                if playlist_id not in edited_structure:
                    edited_structure[playlist_id] = []

                # Check for playlist rename
                if playlist_id in playlists_by_id:
                    original_title = playlists_by_id[playlist_id].title
                    if new_title != original_title:
                        edited_playlist_titles[playlist_id] = new_title
                        changes.renames.append(ItemRename(
                            item_type='playlist',
                            item_id=playlist_id,
                            old_name=original_title,
                            new_name=new_title
                        ))
                continue

            # Check for video line
            video_match = self.VIDEO_PATTERN.match(line)
            if video_match and current_playlist_id:
                new_title = video_match.group(1)
                video_id = video_match.group(2)
                item_id = video_match.group(3)

                if video_id in all_videos:
                    video, original_playlist_id = all_videos[video_id]

                    # Check for video rename
                    if new_title != video.title:
                        changes.renames.append(ItemRename(
                            item_type='video',
                            item_id=video_id,
                            old_name=video.title,
                            new_name=new_title
                        ))

                    # Track video in new structure
                    edited_structure[current_playlist_id].append(
                        (video_id, current_position)
                    )
                    current_position += 1

        # Detect moves and reorders
        seen_videos = set()

        for playlist_id, video_positions in edited_structure.items():
            for video_id, new_pos in video_positions:
                if video_id in seen_videos:
                    # Skip duplicate (shouldn't happen in valid edit)
                    logger.warning(f"Duplicate video {video_id} in edited structure")
                    continue

                seen_videos.add(video_id)
                video, original_playlist_id = all_videos[video_id]

                if playlist_id != original_playlist_id:
                    # Video moved to different playlist
                    changes.moves.append(VideoMove(
                        video=video,
                        source_playlist_id=original_playlist_id,
                        target_playlist_id=playlist_id,
                        new_position=new_pos
                    ))
                else:
                    # Check if reordered within same playlist
                    original_videos = original_videos_by_playlist.get(playlist_id, [])
                    original_pos = None
                    for i, orig_video in enumerate(original_videos):
                        if orig_video.id == video_id:
                            original_pos = i
                            break

                    if original_pos is not None and original_pos != new_pos:
                        changes.reorders.append(VideoReorder(
                            video=video,
                            playlist_id=playlist_id,
                            old_position=original_pos,
                            new_position=new_pos
                        ))

        # Detect deletions
        for video_id, (video, playlist_id) in all_videos.items():
            if video_id not in seen_videos:
                changes.deletions.append((video, playlist_id))

        return changes


class BulkEditExecutor:
    """Executes bulk edit changes via YouTube API."""

    def __init__(self, api_client):
        """Initialize executor.

        Args:
            api_client: YouTubeAPIClient instance
        """
        self.api_client = api_client

    async def execute(self, changes: BulkEditChanges,
                     dry_run: bool = False) -> Dict[str, any]:
        """Execute bulk edit changes.

        Args:
            changes: Changes to apply
            dry_run: If True, don't actually make changes

        Returns:
            Dict with execution results
        """
        results = {
            'success': [],
            'failed': [],
            'skipped': []
        }

        if dry_run:
            logger.info("DRY RUN - No changes will be made")

        # Process deletions first
        for video, playlist_id in changes.deletions:
            try:
                if not dry_run:
                    await self.api_client.remove_from_playlist(
                        video.playlist_item_id
                    )
                results['success'].append(
                    f"Deleted '{video.title}' from playlist"
                )
            except Exception as e:
                results['failed'].append(
                    f"Failed to delete '{video.title}': {e}"
                )

        # Process moves (these implicitly handle position)
        for move in changes.moves:
            try:
                if not dry_run:
                    # Add to target playlist
                    await self.api_client.add_video_to_playlist(
                        move.video.id,
                        move.target_playlist_id,
                        position=move.new_position
                    )
                    # Remove from source playlist
                    await self.api_client.remove_from_playlist(
                        move.video.playlist_item_id
                    )
                results['success'].append(
                    f"Moved '{move.video.title}' to different playlist"
                )
            except Exception as e:
                results['failed'].append(
                    f"Failed to move '{move.video.title}': {e}"
                )

        # Process reorders within playlists
        for reorder in sorted(changes.reorders, key=lambda r: r.new_position):
            try:
                if not dry_run:
                    await self.api_client.update_video_position(
                        reorder.video.playlist_item_id,
                        reorder.new_position
                    )
                results['success'].append(
                    f"Reordered '{reorder.video.title}' to position {reorder.new_position}"
                )
            except Exception as e:
                results['failed'].append(
                    f"Failed to reorder '{reorder.video.title}': {e}"
                )

        # Process renames
        for rename in changes.renames:
            # Note: YouTube API doesn't support renaming videos/playlists directly
            # This would need special handling or could be skipped
            results['skipped'].append(
                f"Rename of {rename.item_type} '{rename.old_name}' to '{rename.new_name}' "
                f"(not supported by YouTube API)"
            )

        return results


class BulkEditor:
    """Main bulk edit coordinator."""

    def __init__(self, api_client):
        """Initialize bulk editor.

        Args:
            api_client: YouTubeAPIClient instance
        """
        self.api_client = api_client
        self.generator = BulkEditGenerator()
        self.parser = BulkEditParser()
        self.executor = BulkEditExecutor(api_client)

    def launch_editor(self, content: str) -> Optional[str]:
        """Launch text editor with content.

        Args:
            content: Initial content

        Returns:
            Edited content or None if cancelled
        """
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md',
                                        delete=False) as f:
            f.write(content)
            temp_path = f.name

        try:
            # Get editor from environment or use default
            editor = os.environ.get('EDITOR', 'vim')

            # Launch editor
            result = subprocess.run([editor, temp_path])

            if result.returncode == 0:
                # Read edited content
                with open(temp_path, 'r') as f:
                    edited = f.read()
                return edited
            else:
                logger.warning(f"Editor exited with code {result.returncode}")
                return None

        finally:
            # Clean up temp file
            try:
                os.unlink(temp_path)
            except:
                pass

    async def bulk_edit(self, playlists: List[Playlist],
                       videos_by_playlist: Dict[str, List[Video]],
                       dry_run: bool = False) -> Tuple[BulkEditChanges, Dict[str, any]]:
        """Perform bulk edit operation.

        Args:
            playlists: List of playlists
            videos_by_playlist: Videos organized by playlist
            dry_run: If True, don't apply changes

        Returns:
            Tuple of (changes, execution_results)
        """
        # Generate markdown
        original = self.generator.generate(playlists, videos_by_playlist)

        # Launch editor
        edited = self.launch_editor(original)

        if edited is None or edited == original:
            # Cancelled or no changes
            return BulkEditChanges(), {}

        # Parse changes
        changes = self.parser.parse(edited, playlists, videos_by_playlist)

        if changes.is_empty():
            return changes, {}

        # Execute changes
        results = await self.executor.execute(changes, dry_run=dry_run)

        return changes, results