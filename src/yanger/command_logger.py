"""Command and keyboard logging for YouTube Ranger.

Provides structured logging of user interactions for debugging and auditing.
"""
# Created: 2025-08-21

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Union
import atexit


class CommandLogger:
    """Handles logging of keyboard commands and user actions to a file."""
    
    def __init__(self, log_file: Union[str, Path], log_level: str = "INFO"):
        """Initialize the command logger.
        
        Args:
            log_file: Path to the log file
            log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        """
        self.log_file = Path(log_file)
        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self._lock = threading.Lock()
        self._file_handle = None
        self._session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Ensure log directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Open log file
        try:
            self._file_handle = open(self.log_file, 'a', encoding='utf-8')
            self._write_session_header()
        except IOError as e:
            logging.error(f"Failed to open log file {self.log_file}: {e}")
            self._file_handle = None
        
        # Register cleanup on exit
        atexit.register(self.close)
    
    def _write_session_header(self) -> None:
        """Write session header to log file."""
        header = {
            "type": "SESSION_START",
            "session_id": self._session_id,
            "timestamp": datetime.now().isoformat(),
            "app_version": "0.1.0",  # TODO: Get from __version__
        }
        self._write_entry(header)
    
    def _write_entry(self, entry: Dict[str, Any]) -> None:
        """Write a log entry to file.
        
        Args:
            entry: Dictionary containing log data
        """
        if not self._file_handle:
            return
        
        with self._lock:
            try:
                # Add timestamp if not present
                if "timestamp" not in entry:
                    entry["timestamp"] = datetime.now().isoformat()
                
                # Add session ID
                entry["session_id"] = self._session_id
                
                # Write as JSON line
                json_line = json.dumps(entry, ensure_ascii=False)
                self._file_handle.write(json_line + "\n")
                self._file_handle.flush()
            except IOError as e:
                logging.error(f"Failed to write to log file: {e}")
    
    def log_key(self, key: str, context: Optional[str] = None, 
                modifiers: Optional[Dict[str, bool]] = None) -> None:
        """Log a keyboard input event.
        
        Args:
            key: The key pressed
            context: Current UI context (e.g., "video_list", "playlist_list")
            modifiers: Dictionary of modifier keys (ctrl, shift, alt, meta)
        """
        entry = {
            "type": "KEY",
            "key": key,
            "context": context or "unknown",
        }
        
        if modifiers:
            entry["modifiers"] = modifiers
        
        if self.log_level <= logging.DEBUG:
            self._write_entry(entry)
    
    def log_command(self, command: str, args: Optional[str] = None, 
                   result: Optional[str] = None, success: bool = True) -> None:
        """Log a command execution.
        
        Args:
            command: The command executed (e.g., "sort", "filter")
            args: Command arguments
            result: Command result or error message
            success: Whether the command succeeded
        """
        entry = {
            "type": "COMMAND",
            "command": command,
            "success": success,
        }
        
        if args:
            entry["args"] = args
        if result:
            entry["result"] = result
        
        self._write_entry(entry)
    
    def log_action(self, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Log a high-level user action.
        
        Args:
            action: Action name (e.g., "navigate_down", "cut_videos")
            details: Additional action details
        """
        entry = {
            "type": "ACTION",
            "action": action,
        }
        
        if details:
            entry["details"] = details
        
        self._write_entry(entry)
    
    def log_operation(self, operation: str, success: bool, 
                     details: Optional[Dict[str, Any]] = None,
                     error: Optional[str] = None) -> None:
        """Log an operation (e.g., API call, file operation).
        
        Args:
            operation: Operation name
            success: Whether the operation succeeded
            details: Operation details
            error: Error message if operation failed
        """
        entry = {
            "type": "OPERATION",
            "operation": operation,
            "success": success,
        }
        
        if details:
            entry["details"] = details
        if error:
            entry["error"] = error
        
        self._write_entry(entry)
    
    def log_navigation(self, from_item: str, to_item: str, 
                      navigation_type: str = "select") -> None:
        """Log navigation between items.
        
        Args:
            from_item: Item navigated from
            to_item: Item navigated to
            navigation_type: Type of navigation (select, move, etc.)
        """
        entry = {
            "type": "NAVIGATION",
            "from": from_item,
            "to": to_item,
            "navigation_type": navigation_type,
        }
        
        self._write_entry(entry)
    
    def log_search(self, query: str, results_count: int = 0, 
                  context: Optional[str] = None) -> None:
        """Log a search operation.
        
        Args:
            query: Search query
            results_count: Number of results found
            context: Search context (playlist, videos, etc.)
        """
        entry = {
            "type": "SEARCH",
            "query": query,
            "results_count": results_count,
            "context": context or "unknown",
        }
        
        self._write_entry(entry)
    
    def log_api_call(self, endpoint: str, quota_cost: int, 
                    success: bool, error: Optional[str] = None) -> None:
        """Log a YouTube API call.
        
        Args:
            endpoint: API endpoint called
            quota_cost: Quota units consumed
            success: Whether the call succeeded
            error: Error message if call failed
        """
        entry = {
            "type": "API_CALL",
            "endpoint": endpoint,
            "quota_cost": quota_cost,
            "success": success,
        }
        
        if error:
            entry["error"] = error
        
        if self.log_level <= logging.DEBUG:
            self._write_entry(entry)
    
    def log_clipboard(self, operation: str, count: int, 
                     source: Optional[str] = None, target: Optional[str] = None) -> None:
        """Log clipboard operations.
        
        Args:
            operation: Operation type (cut, copy, paste)
            count: Number of items
            source: Source playlist
            target: Target playlist
        """
        entry = {
            "type": "CLIPBOARD",
            "operation": operation,
            "count": count,
        }
        
        if source:
            entry["source"] = source
        if target:
            entry["target"] = target
        
        self._write_entry(entry)
    
    def log_error(self, error: str, context: Optional[str] = None, 
                 details: Optional[Dict[str, Any]] = None) -> None:
        """Log an error.
        
        Args:
            error: Error message
            context: Error context
            details: Additional error details
        """
        entry = {
            "type": "ERROR",
            "error": error,
            "context": context or "unknown",
        }
        
        if details:
            entry["details"] = details
        
        self._write_entry(entry)
    
    def close(self) -> None:
        """Close the log file."""
        if self._file_handle:
            # Write session end marker
            entry = {
                "type": "SESSION_END",
                "session_id": self._session_id,
                "timestamp": datetime.now().isoformat(),
            }
            self._write_entry(entry)
            
            # Close file
            try:
                self._file_handle.close()
            except IOError:
                pass
            finally:
                self._file_handle = None