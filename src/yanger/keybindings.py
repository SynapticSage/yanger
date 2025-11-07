"""Central keybinding registry for YouTube Ranger.

Provides a single source of truth for all keybindings and commands.
"""
# Modified: 2025-08-08

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Callable
from enum import Enum


class KeyContext(Enum):
    """Context where a keybinding is active."""
    GLOBAL = "global"
    PLAYLIST = "playlist"
    VIDEO = "video"
    PREVIEW = "preview"
    VISUAL = "visual"
    SEARCH = "search"
    COMMAND = "command"


@dataclass
class Keybinding:
    """Represents a single keybinding."""
    key: str  # The key or key combination
    description: str  # Human-readable description
    context: KeyContext = KeyContext.GLOBAL  # Where this binding is active
    category: str = "General"  # Category for grouping in help
    hidden: bool = False  # Whether to show in help menu
    
    
@dataclass
class Command:
    """Represents a command (accessible via : mode)."""
    name: str  # Command name (e.g., "sort", "filter")
    description: str  # Human-readable description
    syntax: str  # Command syntax (e.g., ":sort [field] [order]")
    examples: List[str]  # Usage examples
    handler: Optional[Callable] = None  # Function to handle the command


class KeybindingRegistry:
    """Central registry for all keybindings and commands."""
    
    def __init__(self):
        self.keybindings: Dict[str, Keybinding] = {}
        self.commands: Dict[str, Command] = {}
        self._initialize_default_bindings()
        self._initialize_default_commands()
        
    def _initialize_default_bindings(self):
        """Initialize default keybindings."""
        
        # Global bindings
        self.register("q", "Quit application", KeyContext.GLOBAL, "Application")
        self.register("?", "Show this help", KeyContext.GLOBAL, "Application")
        self.register(":", "Enter command mode", KeyContext.GLOBAL, "Application")
        self.register("ctrl+r", "Refresh current view", KeyContext.GLOBAL, "Application")
        self.register("ctrl+shift+r", "Refresh all playlists", KeyContext.GLOBAL, "Application")
        self.register("ctrl+q", "Force quit", KeyContext.GLOBAL, "Application", hidden=True)
        
        # Navigation
        self.register("h", "Move to left column", KeyContext.GLOBAL, "Navigation")
        self.register("j", "Move down", KeyContext.GLOBAL, "Navigation")
        self.register("k", "Move up", KeyContext.GLOBAL, "Navigation")
        self.register("l", "Move to right column", KeyContext.GLOBAL, "Navigation")
        self.register("gg", "Jump to top", KeyContext.GLOBAL, "Navigation")
        self.register("G", "Jump to bottom", KeyContext.GLOBAL, "Navigation")
        self.register("enter", "Select item", KeyContext.GLOBAL, "Navigation")
        
        # Video column specific
        self.register("space", "Toggle mark on current video", KeyContext.VIDEO, "Selection")
        self.register("V", "Visual mode (range selection)", KeyContext.VIDEO, "Selection")
        self.register("v", "Invert selection", KeyContext.VIDEO, "Selection")
        self.register("uv", "Unmark all videos", KeyContext.VIDEO, "Selection")
        self.register("uV", "Visual unmark mode", KeyContext.VIDEO, "Selection")
        
        # Ranger commands (double-key)
        self.register("dd", "Cut selected/marked videos", KeyContext.VIDEO, "Operations")
        self.register("yy", "Copy selected/marked videos", KeyContext.VIDEO, "Operations")
        self.register("pp", "Paste videos from clipboard", KeyContext.VIDEO, "Operations")
        
        # Undo/Redo
        self.register("u", "Undo last operation", KeyContext.GLOBAL, "Operations")
        self.register("U", "Redo last undone operation", KeyContext.GLOBAL, "Operations")
        
        # Search
        self.register("/", "Search in current list", KeyContext.GLOBAL, "Search")
        self.register("n", "Next search result", KeyContext.SEARCH, "Search")
        self.register("N", "Previous search result", KeyContext.SEARCH, "Search")
        self.register("escape", "Cancel search/visual mode", KeyContext.SEARCH, "Search")
        
        # Playlist operations
        self.register("gn", "Create new playlist", KeyContext.GLOBAL, "Playlist")
        self.register("gd", "Delete empty playlist", KeyContext.PLAYLIST, "Playlist", hidden=True)
        self.register("cw", "Rename playlist/video", KeyContext.GLOBAL, "Operations")
        self.register("o", "Open sort menu", KeyContext.VIDEO, "Operations")
        self.register("r", "Open in browser", KeyContext.GLOBAL, "Operations")
        self.register("B", "Bulk edit playlists and videos", KeyContext.GLOBAL, "Operations")

        # Transcript operations
        self.register("gt", "Fetch transcript for current video", KeyContext.VIDEO, "Transcript")
        self.register("gT", "Toggle auto-fetch transcript mode", KeyContext.GLOBAL, "Transcript")
        self.register("ge", "Export transcript to files", KeyContext.VIDEO, "Transcript")
        
    def _initialize_default_commands(self):
        """Initialize default commands."""
        
        self.register_command(
            "sort",
            "Sort videos by field",
            ":sort [field] [order]",
            [
                ":sort title asc",
                ":sort date desc",
                ":sort duration",
                ":sort views desc"
            ]
        )
        
        self.register_command(
            "filter",
            "Filter videos by criteria",
            ":filter [criteria]",
            [
                ":filter music",
                ":filter channel:\"Channel Name\"",
                ":filter duration>10:00"
            ]
        )
        
        self.register_command(
            "clear",
            "Clear marks/filters",
            ":clear [what]",
            [
                ":clear marks",
                ":clear filter",
                ":clear search"
            ]
        )
        
        self.register_command(
            "refresh",
            "Refresh playlist data",
            ":refresh [all]",
            [
                ":refresh",
                ":refresh all"
            ]
        )
        
        self.register_command(
            "cache",
            "Manage cache",
            ":cache [status|clear|expire <playlist_id>]",
            [
                ":cache",
                ":cache status",
                ":cache clear",
                ":cache expire PLxxxxxxx"
            ]
        )
        
        self.register_command(
            "quota",
            "Show API quota usage",
            ":quota",
            [":quota"]
        )
        
        self.register_command(
            "help",
            "Show help for commands",
            ":help [command]",
            [
                ":help",
                ":help sort",
                ":help filter"
            ]
        )
        
        self.register_command(
            "export",
            "Export playlist to file",
            ":export [format] [filename]",
            [
                ":export json playlist.json",
                ":export csv videos.csv",
                ":export m3u playlist.m3u"
            ]
        )
        
        self.register_command(
            "stats",
            "Show playlist statistics",
            ":stats",
            [":stats"]
        )

        self.register_command(
            "bulkedit",
            "Bulk edit playlists and videos in text editor",
            ":bulkedit [--dry-run]",
            [
                ":bulkedit",
                ":bulkedit --dry-run"
            ]
        )

        self.register_command(
            "transcript",
            "Manage video transcripts",
            ":transcript [fetch|export|clear] [options]",
            [
                ":transcript fetch",
                ":transcript export ~/transcripts",
                ":transcript clear"
            ]
        )
        
    def register(self, key: str, description: str, 
                 context: KeyContext = KeyContext.GLOBAL,
                 category: str = "General",
                 hidden: bool = False) -> None:
        """Register a keybinding."""
        self.keybindings[key] = Keybinding(
            key=key,
            description=description,
            context=context,
            category=category,
            hidden=hidden
        )
        
    def register_command(self, name: str, description: str,
                        syntax: str, examples: List[str],
                        handler: Optional[Callable] = None) -> None:
        """Register a command."""
        self.commands[name] = Command(
            name=name,
            description=description,
            syntax=syntax,
            examples=examples,
            handler=handler
        )
        
    def get_bindings_by_category(self) -> Dict[str, List[Keybinding]]:
        """Get keybindings organized by category."""
        result = {}
        for binding in self.keybindings.values():
            if not binding.hidden:
                if binding.category not in result:
                    result[binding.category] = []
                result[binding.category].append(binding)
        return result
        
    def get_bindings_for_context(self, context: KeyContext) -> List[Keybinding]:
        """Get keybindings active in a specific context."""
        result = []
        for binding in self.keybindings.values():
            if not binding.hidden and (
                binding.context == context or 
                binding.context == KeyContext.GLOBAL
            ):
                result.append(binding)
        return result
        
    def get_command(self, name: str) -> Optional[Command]:
        """Get a command by name."""
        return self.commands.get(name)
        
    def get_all_commands(self) -> List[Command]:
        """Get all registered commands."""
        return list(self.commands.values())
        
    def format_help_text(self) -> str:
        """Format help text for display."""
        lines = []
        lines.append("YouTube Ranger - Keyboard Shortcuts\n")
        lines.append("=" * 40 + "\n")
        
        # Group by category
        categories = self.get_bindings_by_category()
        for category in sorted(categories.keys()):
            lines.append(f"\n{category}:")
            lines.append("-" * len(category) + "-")
            
            bindings = sorted(categories[category], key=lambda b: b.key)
            for binding in bindings:
                # Format key with padding
                key_str = binding.key.ljust(12)
                lines.append(f"  {key_str} {binding.description}")
                
        # Add commands section
        lines.append("\n\nCommands (access with ':'):")
        lines.append("-" * 28)
        
        for cmd in sorted(self.commands.values(), key=lambda c: c.name):
            lines.append(f"  :{cmd.name.ljust(10)} {cmd.description}")
            
        lines.append("\n" + "=" * 40)
        lines.append("Press '?' to toggle this help")
        
        return "\n".join(lines)


# Global registry instance
registry = KeybindingRegistry()