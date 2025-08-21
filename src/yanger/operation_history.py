"""Operation history management for undo/redo functionality.

Implements the Command pattern for reversible operations.
"""
# Created: 2025-08-20

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict
from datetime import datetime
import logging

from .models import Video, Playlist

logger = logging.getLogger(__name__)


class Operation(ABC):
    """Abstract base class for reversible operations."""
    
    def __init__(self, description: str = ""):
        """Initialize operation.
        
        Args:
            description: Human-readable description of the operation
        """
        self.description = description
        self.timestamp = datetime.now()
        self.executed = False
        
    @abstractmethod
    def execute(self) -> bool:
        """Execute the operation.
        
        Returns:
            True if successful, False otherwise
        """
        pass
    
    @abstractmethod
    def undo(self) -> bool:
        """Undo the operation.
        
        Returns:
            True if successful, False otherwise
        """
        pass
    
    def __str__(self) -> str:
        """String representation of the operation."""
        return self.description or self.__class__.__name__


@dataclass
class PasteOperation(Operation):
    """Operation for pasting videos to a playlist."""
    
    api_client: Any  # YouTubeAPIClient
    videos: List[Video]
    target_playlist_id: str
    source_playlist_id: Optional[str] = None
    is_cut: bool = False
    added_item_ids: List[str] = field(default_factory=list)
    
    def __init__(self, api_client: Any, videos: List[Video], 
                 target_playlist_id: str, source_playlist_id: Optional[str] = None,
                 is_cut: bool = False):
        """Initialize paste operation."""
        video_count = len(videos)
        action = "Move" if is_cut else "Copy"
        super().__init__(f"{action} {video_count} video(s)")
        
        self.api_client = api_client
        self.videos = videos
        self.target_playlist_id = target_playlist_id
        self.source_playlist_id = source_playlist_id
        self.is_cut = is_cut
        self.added_item_ids = []
        
    def execute(self) -> bool:
        """Execute the paste operation."""
        try:
            # Add videos to target playlist
            for video in self.videos:
                item_id = self.api_client.add_video_to_playlist(
                    video.id, 
                    self.target_playlist_id
                )
                self.added_item_ids.append(item_id)
            
            # If cut operation, remove from source
            if self.is_cut and self.source_playlist_id:
                for video in self.videos:
                    if hasattr(video, 'playlist_item_id'):
                        self.api_client.remove_video_from_playlist(
                            video.playlist_item_id
                        )
            
            self.executed = True
            logger.info(f"Executed: {self.description}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute paste operation: {e}")
            return False
    
    def undo(self) -> bool:
        """Undo the paste operation."""
        if not self.executed:
            return False
            
        try:
            # Remove added videos from target
            for item_id in self.added_item_ids:
                self.api_client.remove_video_from_playlist(item_id)
            
            # If cut operation, restore to source
            if self.is_cut and self.source_playlist_id:
                for video in self.videos:
                    self.api_client.add_video_to_playlist(
                        video.id,
                        self.source_playlist_id
                    )
            
            self.executed = False
            logger.info(f"Undone: {self.description}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to undo paste operation: {e}")
            return False


@dataclass
class CreatePlaylistOperation(Operation):
    """Operation for creating a new playlist."""
    
    api_client: Any
    title: str
    description: str = ""
    privacy_status: str = "private"
    created_playlist_id: Optional[str] = None
    
    def __init__(self, api_client: Any, title: str, 
                 description: str = "", privacy_status: str = "private"):
        """Initialize create playlist operation."""
        super().__init__(f"Create playlist: {title}")
        self.api_client = api_client
        self.title = title
        self.description = description
        self.privacy_status = privacy_status
        self.created_playlist_id = None
    
    def execute(self) -> bool:
        """Create the playlist."""
        try:
            playlist = self.api_client.create_playlist(
                self.title,
                self.description,
                self.privacy_status
            )
            self.created_playlist_id = playlist['id']
            self.executed = True
            logger.info(f"Created playlist: {self.title}")
            return True
        except Exception as e:
            logger.error(f"Failed to create playlist: {e}")
            return False
    
    def undo(self) -> bool:
        """Delete the created playlist."""
        if not self.executed or not self.created_playlist_id:
            return False
            
        try:
            self.api_client.delete_playlist(self.created_playlist_id)
            self.executed = False
            logger.info(f"Deleted playlist: {self.title}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete playlist: {e}")
            return False


@dataclass
class RenameOperation(Operation):
    """Operation for renaming a playlist or video."""
    
    api_client: Any
    item_type: str  # "playlist" or "video"
    item_id: str
    old_title: str
    new_title: str
    playlist_id: Optional[str] = None  # For video renames
    
    def __init__(self, api_client: Any, item_type: str, item_id: str,
                 old_title: str, new_title: str, playlist_id: Optional[str] = None):
        """Initialize rename operation."""
        super().__init__(f"Rename {item_type}: {old_title} → {new_title}")
        self.api_client = api_client
        self.item_type = item_type
        self.item_id = item_id
        self.old_title = old_title
        self.new_title = new_title
        self.playlist_id = playlist_id
    
    def execute(self) -> bool:
        """Execute the rename."""
        try:
            if self.item_type == "playlist":
                self.api_client.rename_playlist(self.item_id, self.new_title)
            else:  # video
                self.api_client.update_video_title(
                    self.item_id, 
                    self.new_title,
                    self.playlist_id
                )
            self.executed = True
            logger.info(f"Renamed {self.item_type}: {self.old_title} → {self.new_title}")
            return True
        except Exception as e:
            logger.error(f"Failed to rename {self.item_type}: {e}")
            return False
    
    def undo(self) -> bool:
        """Restore the original name."""
        if not self.executed:
            return False
            
        try:
            if self.item_type == "playlist":
                self.api_client.rename_playlist(self.item_id, self.old_title)
            else:  # video
                self.api_client.update_video_title(
                    self.item_id,
                    self.old_title,
                    self.playlist_id
                )
            self.executed = False
            logger.info(f"Restored {self.item_type} name: {self.new_title} → {self.old_title}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore {self.item_type} name: {e}")
            return False


class OperationStack:
    """Manages undo and redo stacks for operations."""
    
    def __init__(self, max_size: int = 100):
        """Initialize operation stack.
        
        Args:
            max_size: Maximum number of operations to keep in history
        """
        self.undo_stack: List[Operation] = []
        self.redo_stack: List[Operation] = []
        self.max_size = max_size
    
    def execute(self, operation: Operation) -> bool:
        """Execute an operation and add to undo stack.
        
        Args:
            operation: Operation to execute
            
        Returns:
            True if successful, False otherwise
        """
        if operation.execute():
            # Add to undo stack
            self.undo_stack.append(operation)
            
            # Limit stack size
            if len(self.undo_stack) > self.max_size:
                self.undo_stack.pop(0)
            
            # Clear redo stack (new operation invalidates redo history)
            self.redo_stack.clear()
            
            logger.debug(f"Operation executed: {operation}")
            return True
        return False
    
    def undo(self) -> Optional[Operation]:
        """Undo the last operation.
        
        Returns:
            The undone operation, or None if nothing to undo
        """
        if not self.undo_stack:
            return None
        
        operation = self.undo_stack.pop()
        if operation.undo():
            self.redo_stack.append(operation)
            logger.debug(f"Operation undone: {operation}")
            return operation
        else:
            # Failed to undo, put it back
            self.undo_stack.append(operation)
            return None
    
    def redo(self) -> Optional[Operation]:
        """Redo the last undone operation.
        
        Returns:
            The redone operation, or None if nothing to redo
        """
        if not self.redo_stack:
            return None
        
        operation = self.redo_stack.pop()
        if operation.execute():
            self.undo_stack.append(operation)
            logger.debug(f"Operation redone: {operation}")
            return operation
        else:
            # Failed to redo, put it back
            self.redo_stack.append(operation)
            return None
    
    def can_undo(self) -> bool:
        """Check if undo is available."""
        return len(self.undo_stack) > 0
    
    def can_redo(self) -> bool:
        """Check if redo is available."""
        return len(self.redo_stack) > 0
    
    def get_undo_description(self) -> Optional[str]:
        """Get description of operation that would be undone."""
        if self.undo_stack:
            return str(self.undo_stack[-1])
        return None
    
    def get_redo_description(self) -> Optional[str]:
        """Get description of operation that would be redone."""
        if self.redo_stack:
            return str(self.redo_stack[-1])
        return None
    
    def clear(self) -> None:
        """Clear all operation history."""
        self.undo_stack.clear()
        self.redo_stack.clear()
        logger.debug("Operation history cleared")
    
    def get_history_size(self) -> Dict[str, int]:
        """Get the size of undo/redo stacks."""
        return {
            "undo": len(self.undo_stack),
            "redo": len(self.redo_stack)
        }