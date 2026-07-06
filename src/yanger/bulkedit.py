"""Bulk edit functionality for reorganizing playlists and videos.

Allows editing playlist structure in a text editor using markdown format.
"""
# Created: 2025-09-22

import os
import re
import shlex
import tempfile
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple, Set
from pathlib import Path
import logging

from .models import Playlist, Video

logger = logging.getLogger(__name__)

# Parse-coverage guard thresholds (fix #2): the executor deletes first, so we
# refuse to infer mass deletions from a parse we don't fully understand. A
# reindent / smart-quote / wrapped line that fails the strict patterns would
# otherwise read as "user removed these videos" and wipe them out.
MAX_DELETION_FRACTION = 0.5
MIN_VIDEOS_FOR_DELETION_GUARD = 10


class BulkEditError(Exception):
    """Base error for bulk edit failures surfaced to the user."""


class BulkEditParseError(BulkEditError):
    """Raised when the edited markdown cannot be parsed safely.

    Aborts the apply instead of inferring destructive intent (deletions) from a
    parse failure. Callers (app.py ``execute_bulkedit``) already wrap bulk edit
    in try/except and notify the user with the message.
    """


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

        # Build lookup maps.
        playlists_by_id = {p.id: p for p in original_playlists}

        # Key every original occurrence by its UNIQUE playlist_item_id, not the
        # bare video id (fix #1). The same video can legitimately live in
        # several playlists; each appearance has its own item id. Keying by
        # video.id let the second occurrence clobber the first, fabricating a
        # cross-playlist move that deleted the user's other copy even on a no-op.
        occ_by_item = {}  # playlist_item_id -> (Video, original_playlist_id)
        for playlist_id, videos in original_videos_by_playlist.items():
            for video in videos:
                occ_by_item[video.playlist_item_id] = (video, playlist_id)

        # Parse edited structure, tracking each surviving line by its item id.
        edited_structure = {}  # playlist_id -> list of item_ids (in edited order)
        current_playlist_id = None
        parsed_item_ids = set()    # guards against a copy-pasted (duplicate) line
        unmatched_lines = []       # content lines matching NEITHER pattern (fix #2)

        for line in content.split('\n'):
            stripped = line.strip()
            # Skip blank lines and comments / instructions.
            if not stripped or stripped.startswith('#'):
                continue

            # Playlist header line.
            playlist_match = self.PLAYLIST_PATTERN.match(line)
            if playlist_match:
                new_title = playlist_match.group(1)
                playlist_id = playlist_match.group(2)
                current_playlist_id = playlist_id
                edited_structure.setdefault(playlist_id, [])

                # Playlist rename.
                if playlist_id in playlists_by_id:
                    original_title = playlists_by_id[playlist_id].title
                    if new_title != original_title:
                        changes.renames.append(ItemRename(
                            item_type='playlist',
                            item_id=playlist_id,
                            old_name=original_title,
                            new_name=new_title
                        ))
                continue

            # Video line. Must sit under a playlist and carry a known item id.
            video_match = self.VIDEO_PATTERN.match(line)
            if video_match and current_playlist_id:
                new_title = video_match.group(1)
                item_id = video_match.group(3)

                if item_id not in occ_by_item:
                    # Unknown / garbled item id: don't guess, let the guard see it.
                    unmatched_lines.append(line)
                    continue
                if item_id in parsed_item_ids:
                    logger.warning(f"Duplicate item {item_id} in edited structure; skipping")
                    continue

                parsed_item_ids.add(item_id)
                video, _orig_pl = occ_by_item[item_id]

                # Video rename.
                if new_title != video.title:
                    changes.renames.append(ItemRename(
                        item_type='video',
                        item_id=video.id,
                        old_name=video.title,
                        new_name=new_title
                    ))

                edited_structure[current_playlist_id].append(item_id)
                continue

            # Any other non-blank, non-comment line failed to parse.
            unmatched_lines.append(line)

        # Parse-coverage guard #1 (fix #2): if we couldn't account for a line,
        # abort rather than read its absence as "delete these videos".
        if unmatched_lines:
            sample = unmatched_lines[0].strip()[:60]
            raise BulkEditParseError(
                f"Bulk edit aborted: {len(unmatched_lines)} line(s) could not be parsed "
                f"(e.g. {sample!r}). Refusing to apply, since unparsed lines would be "
                f"treated as deletions. Re-edit without reindenting or altering the "
                f"<!-- id:...,item:... --> markers."
            )

        # Detect moves and reorders, occurrence by occurrence.
        seen_items = set()
        for playlist_id, edited_item_ids in edited_structure.items():
            # Stayers keep their original playlist; movers-in came from elsewhere.
            stayer_set = {iid for iid in edited_item_ids
                          if occ_by_item[iid][1] == playlist_id}

            # Original order of the survivors only (deleted / moved-away items
            # dropped) so a single deletion doesn't cascade into a reorder for
            # every following video (fix #5).
            orig_rel = {
                v.playlist_item_id: i
                for i, v in enumerate(
                    vp for vp in original_videos_by_playlist.get(playlist_id, [])
                    if vp.playlist_item_id in stayer_set
                )
            }
            new_rel = {iid: i for i, iid in enumerate(
                iid for iid in edited_item_ids if iid in stayer_set
            )}

            for abs_pos, item_id in enumerate(edited_item_ids):
                seen_items.add(item_id)
                video, original_playlist_id = occ_by_item[item_id]

                if original_playlist_id != playlist_id:
                    # Moved to a different playlist.
                    changes.moves.append(VideoMove(
                        video=video,
                        source_playlist_id=original_playlist_id,
                        target_playlist_id=playlist_id,
                        new_position=abs_pos
                    ))
                elif orig_rel[item_id] != new_rel[item_id]:
                    # Stayed, but its rank among surviving siblings changed.
                    changes.reorders.append(VideoReorder(
                        video=video,
                        playlist_id=playlist_id,
                        old_position=orig_rel[item_id],
                        new_position=new_rel[item_id]
                    ))

        # Detect deletions: any original occurrence not seen in the edit.
        for item_id, (video, playlist_id) in occ_by_item.items():
            if item_id not in seen_items:
                changes.deletions.append((video, playlist_id))

        # Parse-coverage guard #2 (fix #2): a suspiciously large deletion ratio
        # on a non-trivial library is more likely a parse glitch than intent.
        total_original = len(occ_by_item)
        if (total_original >= MIN_VIDEOS_FOR_DELETION_GUARD
                and len(changes.deletions) > total_original * MAX_DELETION_FRACTION):
            raise BulkEditParseError(
                f"Bulk edit aborted: {len(changes.deletions)} of {total_original} videos "
                f"would be deleted (> {int(MAX_DELETION_FRACTION * 100)}%). This looks like a "
                f"parse error, not intent. If deliberate, delete fewer videos per edit."
            )

        return changes


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
        # NB: the app applies bulk edits via operation_history.BulkEditOperation (so they
        # participate in undo/redo). There is deliberately no executor here — a former
        # BulkEditExecutor duplicated that apply logic on a path the app never called.

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
            # Get editor from environment or use default.
            editor = os.environ.get('EDITOR', 'vim')

            # shlex.split so EDITOR values carrying flags (e.g. "code --wait")
            # are launched as command + args instead of one bogus executable.
            try:
                result = subprocess.run(shlex.split(editor) + [temp_path])
            except FileNotFoundError as e:
                raise BulkEditError(
                    f"Could not launch editor '{editor}': {e}. "
                    f"Set $EDITOR to an installed editor."
                ) from e

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
            except OSError:
                pass

    def bulk_edit(self, playlists: List[Playlist],
                  videos_by_playlist: Dict[str, List[Video]],
                  dry_run: bool = False) -> Tuple[BulkEditChanges, Dict[str, any]]:
        """Perform bulk edit operation.

        Synchronous by design: this only generates markdown, launches a blocking
        external editor, and parses the result -- it does NO async API I/O. The
        caller (app.py execute_bulkedit) runs it in a worker thread under
        ``app.suspend()`` so the editor owns the terminal and the event loop
        doesn't freeze.

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

        # Parse changes. Parsing only -- the app shows BulkEditPreview and then
        # applies via BulkEditOperation on confirm, so executing here too would
        # double-apply every change (and the second pass would fail on already
        # deleted items). Execution is the caller's responsibility.
        changes = self.parser.parse(edited, playlists, videos_by_playlist)

        return changes, {}