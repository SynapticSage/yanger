"""Central keybinding registry for YouTube Ranger.

Provides a single source of truth for all keybindings and commands.

Every keybinding has a stable *action id* (``cut``, ``refresh_all``...). Its keys are written
as space-separated *tokens*, one per keypress: ``"g R"`` is g then R, ``"up"`` is the arrow key.
A token is a single printable character or a Textual key name (``enter``, ``ctrl+r``).
Two kinds of action exist:

- ``BINDING``: a single key handled by Textual's ``App.BINDINGS`` (quit, help, ``:``...).
- ``SEQUENCE``: one or more keys handled by ``App.on_key`` through ``KeySequenceMatcher``
  (the ranger-style prefixes gn/dd/cw/u*, and the keys forwarded to the miller view).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from enum import Enum

import yaml

try:
    from textual.keys import Keys as _TextualKeys, REPLACED_KEYS as _REPLACED_KEYS, key_to_character
except ImportError:  # fail closed: without Textual's key tables only characters/named keys validate
    _TextualKeys, _REPLACED_KEYS = (), {}

    def key_to_character(key: str) -> Optional[str]:
        return None


class KeyContext(Enum):
    """Context where a keybinding is active."""
    GLOBAL = "global"
    PLAYLIST = "playlist"
    VIDEO = "video"
    PREVIEW = "preview"
    VISUAL = "visual"
    SEARCH = "search"
    COMMAND = "command"


BINDING = "binding"    # handled by Textual App.BINDINGS (single key, rebound via set_keymap)
SEQUENCE = "sequence"  # handled by App.on_key through KeySequenceMatcher

# How token names display in help: ["h", "left"] -> "h/←", ["g R"] -> "gR".
_DISPLAY_TOKENS = {"left": "←", "right": "→", "up": "↑", "down": "↓"}


def display_keys(keys: Sequence[str]) -> str:
    """Help-display form of a binding's key sequences: tokens concatenated, alternatives '/'."""
    return "/".join(
        "".join(_DISPLAY_TOKENS.get(tok, tok) for tok in seq.split()) for seq in keys
    )


# Named keys a token may use (what Textual reports as event.key for non-printing keys).
_NAMED_KEYS = {
    "enter", "escape", "space", "tab", "backspace", "delete", "insert", "home", "end",
    "pageup", "pagedown", "up", "down", "left", "right",
} | {f"f{i}" for i in range(1, 25)}
# Modifier combos Textual can deliver ("ctrl+r", "shift+up"...).
_MODIFIER_KEYS = {k.value for k in _TextualKeys if "+" in k.value}
# Keys Textual itself owns (focus, copy / quit hint, command palette, quit): never user-bound.
RESERVED_KEYS = {"tab", "shift+tab", "ctrl+c", "ctrl+p", "ctrl+q"}


def canonical_token(token: str) -> Optional[str]:
    """The token as the matcher sees it, or None if no keypress can produce it.

    Single printable characters stand for themselves; Textual's names for them
    ("question_mark", "slash") map back to the character, so "?" and "question_mark" compare
    equal. Named keys and Textual's modifier combos pass through.
    """
    if len(token) == 1:
        return token if token.isprintable() and not token.isspace() else None
    if token in _NAMED_KEYS or token in _MODIFIER_KEYS:
        return token
    if "+" not in token:
        char = key_to_character(_REPLACED_KEYS.get(token, token))
        if char and len(char) == 1 and char.isprintable() and not char.isspace():
            return char
    return None


def _bad_token_warning(action: str, token: str) -> str:
    hint = 'a key is one character or a key name such as "enter", "pageup", "ctrl+r"'
    if "+" not in token and token.isprintable() and len(token) > 1:
        hint += f'; separate the keys of a sequence with spaces (did you mean "{" ".join(token)}"?)'
    return f'keybindings.yaml: "{action}": unknown key "{token}" ({hint}). Keeping its defaults.'


def load_keybinding_overrides(path: Path) -> Tuple[Dict[str, List[str]], List[str]]:
    """Read ``action_id: "keys" | ["keys", ...]`` from a user keybindings.yaml.

    Shape checks only (``registry.apply_overrides`` validates ids and keys). A missing file
    means no overrides; an unreadable/unparsable file or a non-mapping top level means no
    overrides plus one warning; an entry of the wrong type is skipped with a warning.
    """
    if not path.exists():
        return {}, []
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
        return {}, [f"{path}: could not be read ({e}); using the default keys."]
    if data is None:
        return {}, []
    if not isinstance(data, dict):
        return {}, [f"{path}: expected a mapping of action: keys; using the default keys."]
    overrides: Dict[str, List[str]] = {}
    warnings: List[str] = []
    for action, value in data.items():
        if isinstance(value, str):
            value = [value]
        if not (isinstance(action, str) and isinstance(value, list)
                and all(isinstance(v, str) for v in value)):
            warnings.append(
                f'{path}: "{action}" must map to a quoted key string or a list of them; ignored.')
            continue
        overrides[action] = value
    return overrides, warnings


@dataclass
class Keybinding:
    """Represents a single keybinding."""
    key: str  # Display form of the effective keys (e.g. "gR", "h/←")
    description: str  # Human-readable description
    context: KeyContext = KeyContext.GLOBAL  # Where this binding is active
    category: str = "General"  # Category for grouping in help
    hidden: bool = False  # Whether to show in help menu
    action: str = ""  # Stable action id, the name users write in keybindings.yaml
    keys: List[str] = field(default_factory=list)  # Effective key sequences, token form ("g R")
    kind: str = SEQUENCE  # BINDING or SEQUENCE

    
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
        self.keybindings: Dict[str, Keybinding] = {}  # display key -> binding (help reads this)
        self._actions: Dict[str, Keybinding] = {}     # action id -> binding
        self._defaults: Dict[str, List[str]] = {}     # action id -> default key sequences
        self._overridden: set = set()                 # action ids whose keys are user overrides
        self.commands: Dict[str, Command] = {}
        self._initialize_default_bindings()
        self._initialize_default_commands()

    def _initialize_default_bindings(self):
        """Initialize default keybindings: action id, default key sequences (token form)."""

        # Global bindings
        self.register("quit", ["q"], "Quit application", KeyContext.GLOBAL, "Application", kind=BINDING)
        self.register("help", ["?"], "Show this help", KeyContext.GLOBAL, "Application", kind=BINDING)
        self.register("command_mode", [":"], "Enter command mode", KeyContext.GLOBAL, "Application", kind=BINDING)
        self.register("refresh", ["ctrl+r"], "Refresh current view", KeyContext.GLOBAL, "Application", kind=BINDING)
        self.register("refresh_all", ["g R"], "Refresh all playlists", KeyContext.GLOBAL, "Application")
        self.register("force_quit", ["ctrl+q"], "Force quit", KeyContext.GLOBAL, "Application", hidden=True, kind=BINDING)

        # Navigation
        self.register("move_left", ["h", "left"], "Move to left column", KeyContext.GLOBAL, "Navigation")
        self.register("move_down", ["j", "down"], "Move down", KeyContext.GLOBAL, "Navigation")
        self.register("move_up", ["k", "up"], "Move up", KeyContext.GLOBAL, "Navigation")
        self.register("move_right", ["l", "right"], "Move to right column", KeyContext.GLOBAL, "Navigation")
        self.register("go_top", ["g g"], "Jump to top", KeyContext.GLOBAL, "Navigation")
        self.register("go_bottom", ["G"], "Jump to bottom", KeyContext.GLOBAL, "Navigation")
        self.register("select", ["enter"], "Select item", KeyContext.GLOBAL, "Navigation")
        self.register("page_up", ["pageup"], "Previous page of videos", KeyContext.VIDEO, "Navigation")
        self.register("page_down", ["pagedown"], "Next page of videos", KeyContext.VIDEO, "Navigation")

        # Video column specific
        self.register("toggle_mark", ["space"], "Toggle mark on current video", KeyContext.VIDEO, "Selection")
        self.register("visual_mode", ["V"], "Visual mode (range selection)", KeyContext.VIDEO, "Selection")
        self.register("invert_selection", ["v"], "Invert selection", KeyContext.VIDEO, "Selection")
        self.register("unmark_all", ["u v"], "Unmark all videos", KeyContext.VIDEO, "Selection")
        self.register("visual_unmark", ["u V"], "Visual unmark mode", KeyContext.VIDEO, "Selection")

        # Ranger commands (double-key)
        self.register("cut", ["d d"], "Cut selected/marked videos", KeyContext.VIDEO, "Operations")
        self.register("delete_videos", ["d D"], "Delete selected/marked videos (permanent)", KeyContext.VIDEO, "Operations")
        self.register("copy", ["y y"], "Copy selected/marked videos", KeyContext.VIDEO, "Operations")
        self.register("paste", ["p p"], "Paste videos from clipboard", KeyContext.VIDEO, "Operations")

        # Undo/Redo
        self.register("undo", ["u"], "Undo last operation", KeyContext.GLOBAL, "Operations")
        self.register("redo", ["U"], "Redo last undone operation", KeyContext.GLOBAL, "Operations", kind=BINDING)

        # Search
        self.register("search", ["/"], "Search in current list", KeyContext.GLOBAL, "Search", kind=BINDING)
        self.register("next_match", ["n"], "Next search result", KeyContext.SEARCH, "Search")
        self.register("prev_match", ["N"], "Previous search result", KeyContext.SEARCH, "Search")
        self.register("cancel", ["escape"], "Cancel search/visual mode", KeyContext.SEARCH, "Search")

        # Playlist operations
        self.register("new_playlist", ["g n"], "Create new playlist", KeyContext.GLOBAL, "Playlist")
        self.register("delete_playlist", ["g d"], "Delete playlist", KeyContext.PLAYLIST, "Playlist")
        self.register("rename", ["c w"], "Rename playlist/video", KeyContext.GLOBAL, "Operations")
        self.register("sort_menu", ["o"], "Open sort menu", KeyContext.VIDEO, "Operations")
        self.register("open_in_browser", ["r"], "Open in browser", KeyContext.GLOBAL, "Operations", kind=BINDING)
        self.register("bulk_edit", ["B"], "Bulk edit playlists and videos", KeyContext.GLOBAL, "Operations")

        # Transcript operations
        self.register("fetch_transcript", ["g t"], "Fetch transcript for current video", KeyContext.VIDEO, "Transcript")
        self.register("toggle_auto_transcript", ["g T"], "Toggle auto-fetch transcript mode", KeyContext.GLOBAL, "Transcript")
        self.register("export_transcript", ["g e"], "Export transcript to files", KeyContext.VIDEO, "Transcript")
        
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
            "run",
            "Run a configured custom command on the current/marked videos",
            ":run <name>",
            [
                ":run dl",
                ":run sum",
            ]
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
            "Run your configured transcript command on the current video",
            ":transcript",
            [
                ":transcript",
                ':set transcript_command "yeet {url} | fabric -sp summarize"',
            ]
        )

        self.register_command(
            "set",
            "Set and persist a config value (e.g. transcript_command)",
            ":set <key> <value>",
            [
                ':set transcript_command "summarize {url}"',
                ':set transcript_command "yeet {url} | fabric -sp summarize"',
            ]
        )

        self.register_command(
            "duplicates",
            "Find duplicate videos in the current playlist (alias: :dupes)",
            ":duplicates",
            [
                ":duplicates",
                ":dupes",
            ]
        )

        self.register_command(
            "fetch-metadata",
            "Fetch titles/metadata for videos in a virtual playlist",
            ":fetch-metadata",
            [":fetch-metadata"]
        )

        self.register_command(
            "delete",
            "Delete selected videos or the current playlist",
            ":delete [videos|playlist]",
            [
                ":delete videos",
                ":delete playlist",
            ]
        )

    def register(self, action: str, keys: List[str], description: str,
                 context: KeyContext = KeyContext.GLOBAL,
                 category: str = "General",
                 hidden: bool = False,
                 kind: str = SEQUENCE) -> None:
        """Register a keybinding under a stable action id with its default key sequences."""
        binding = Keybinding(
            key=display_keys(keys),
            description=description,
            context=context,
            category=category,
            hidden=hidden,
            action=action,
            keys=list(keys),
            kind=kind,
        )
        self._actions[action] = binding
        self._defaults[action] = list(keys)
        self.keybindings[binding.key] = binding

    def get_action(self, action: str) -> Keybinding:
        """The binding registered under ``action`` (KeyError if unknown)."""
        return self._actions[action]

    def apply_overrides(self, overrides: Dict[str, List[str]],
                        rebind_bindings: bool = True) -> List[str]:
        """Make ``overrides`` (action id -> key sequences) the effective keys; return warnings.

        Always recomputed from the defaults, so ``apply_overrides({})`` restores them. A listed
        action's keys REPLACE its defaults. Fail closed: an entry that is unknown, malformed,
        undeliverable, reserved, or (for BINDING kind) a multi-key sequence, or that
        ``rebind_bindings=False`` forbids, is skipped and the action keeps its defaults.
        Overrides involved in a conflict are reverted until no conflict remains; the defaults
        are conflict-free, so this ends.
        """
        warnings: List[str] = []
        accepted: Dict[str, List[Tuple[str, ...]]] = {}
        for action, values in overrides.items():
            if action not in self._actions:
                warnings.append(f'keybindings.yaml: unknown action "{action}"; ignored.')
                continue
            seqs = self._parse_override(action, values, rebind_bindings, warnings)
            if seqs is not None:
                accepted[action] = seqs

        while True:
            effective = {a: accepted.get(a) or [tuple(s.split()) for s in d]
                         for a, d in self._defaults.items()}
            reverted = set()
            for a, sa, b, sb in self._conflicts(effective):
                involved = {x for x in (a, b) if x in accepted}
                if not involved:
                    continue
                reverted |= involved
                warnings.append(
                    f'keybindings.yaml: "{a}" ({" ".join(sa)}) conflicts with "{b}" '
                    f'({" ".join(sb)}); keeping the default keys of '
                    f'{" and ".join(sorted(involved))}.')
            if not reverted:
                break
            for a in reverted:
                del accepted[a]

        for action, binding in self._actions.items():
            binding.keys = [" ".join(seq) for seq in effective[action]]
            binding.key = display_keys(binding.keys)
        self._overridden = set(accepted)
        self.keybindings = {b.key: b for b in self._actions.values()}
        return warnings

    def _parse_override(self, action: str, values, rebind_bindings: bool,
                        warnings: List[str]) -> Optional[List[Tuple[str, ...]]]:
        """One override entry as canonical token tuples, or None (with a warning)."""
        binding = self._actions[action]
        if binding.kind == BINDING and not rebind_bindings:
            warnings.append(f'keybindings.yaml: "{action}" cannot be rebound with this Textual '
                            '(no keymap support); keeping its defaults.')
            return None
        if not (isinstance(values, list) and values and all(isinstance(v, str) for v in values)):
            warnings.append(f'keybindings.yaml: "{action}" needs at least one key string '
                            '(an empty list cannot unbind); keeping its defaults.')
            return None
        defaults = {tok for seq in self._defaults[action] for tok in seq.split()}
        seqs: List[Tuple[str, ...]] = []
        for value in values:
            tokens = value.split()
            if not tokens:
                warnings.append(f'keybindings.yaml: "{action}" has an empty key; '
                                'keeping its defaults.')
                return None
            canon = []
            for tok in tokens:
                c = canonical_token(tok)
                if c is None:
                    warnings.append(_bad_token_warning(action, tok))
                    return None
                if c in RESERVED_KEYS and c not in defaults:
                    warnings.append(f'keybindings.yaml: "{action}": "{c}" is reserved by '
                                    'Textual; keeping its defaults.')
                    return None
                canon.append(c)
            if binding.kind == BINDING and len(canon) > 1:
                warnings.append(f'keybindings.yaml: "{action}" takes a single key, not the '
                                f'sequence "{value}"; keeping its defaults.')
                return None
            if tuple(canon) not in seqs:
                seqs.append(tuple(canon))
        return seqs

    def _conflicts(self, effective: Dict[str, List[Tuple[str, ...]]]):
        """(action, seq, other action, other seq) for every clash in ``effective``.

        Clashes: the same sequence on two actions, the same BINDING key on two actions, and a
        BINDING key equal to ANY token of a SEQUENCE: Textual runs App BINDINGS even after
        on_key calls event.stop(), so that key would fire both actions.
        """
        seq_owner: Dict[Tuple[str, ...], str] = {}
        binding_owner: Dict[str, str] = {}
        token_users: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {}
        for action, seqs in effective.items():
            is_binding = self._actions[action].kind == BINDING
            for seq in seqs:
                owners = binding_owner if is_binding else seq_owner
                key = seq[0] if is_binding else seq
                other = owners.setdefault(key, action)
                if other != action:
                    yield other, seq, action, seq
                if not is_binding:
                    for tok in seq:
                        token_users.setdefault(tok, []).append((action, seq))
        for key, action in binding_owner.items():
            for other, seq in token_users.get(key, []):
                yield action, (key,), other, seq

    def binding_keymap(self) -> Dict[str, str]:
        """Textual keymap ({binding id: "k1,k2"}) for the overridden BINDING-kind actions."""
        return {
            a: ",".join(self._actions[a].keys)
            for a in sorted(self._overridden) if self._actions[a].kind == BINDING
        }

    def sequence_bindings(self) -> List[Tuple[Tuple[str, ...], str]]:
        """(token tuple, action id) for every effective SEQUENCE-kind key sequence."""
        return [
            (tuple(seq.split()), b.action)
            for b in self._actions.values() if b.kind == SEQUENCE
            for seq in b.keys
        ]

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


class MatchResult:
    """Outcome of feeding one key token to ``KeySequenceMatcher``.

    ``kind`` is one of:
    - PENDING: a prefix of a longer sequence. ``action`` is what fires if nothing follows
      (a node that is both a full sequence and a prefix, like ``u``); ``children`` holds the
      (next token, action) pairs still reachable.
    - MATCH: ``action`` fires now.
    - MISMATCH: the key broke a pending prefix and is consumed; ``action`` is the pending
      node's own action to fire instead (``u`` then ``j`` = undo), or None.
    - NONE: no sequence starts with this key; it is not ours to handle.
    """

    PENDING, MATCH, MISMATCH, NONE = "pending", "match", "mismatch", "none"

    def __init__(self, kind: str, action: Optional[str] = None,
                 children: Optional[List[Tuple[str, str]]] = None):
        self.kind = kind
        self.action = action
        self.children = children or []


class KeySequenceMatcher:
    """Match keypress tokens against multi-key sequences (pure: no Textual, no timers).

    ``enabled(action)`` filters candidates at every keypress, so a prefix only arms while an
    action below it can fire (``d`` arms dd/dD in the video column and falls through elsewhere).
    """

    def __init__(self, sequences: List[Tuple[Tuple[str, ...], str]]):
        self._sequences = sequences
        self._buffer: Tuple[str, ...] = ()

    @property
    def pending(self) -> bool:
        return bool(self._buffer)

    def reset(self) -> None:
        self._buffer = ()

    def _terminal(self, buffer: Tuple[str, ...], enabled: Callable[[str], bool]) -> Optional[str]:
        return next((a for seq, a in self._sequences if seq == buffer and enabled(a)), None)

    def feed(self, token: str, enabled: Callable[[str], bool]) -> MatchResult:
        buffer = self._buffer + (token,)
        n = len(buffer)
        candidates = [(seq, a) for seq, a in self._sequences if seq[:n] == buffer and enabled(a)]
        if not candidates:
            if not self._buffer:
                return MatchResult(MatchResult.NONE)
            fallback = self._terminal(self._buffer, enabled)
            self.reset()
            return MatchResult(MatchResult.MISMATCH, fallback)
        exact = next((a for seq, a in candidates if len(seq) == n), None)
        longer = [(seq[n], a) for seq, a in candidates if len(seq) > n]
        if not longer:
            self.reset()
            return MatchResult(MatchResult.MATCH, exact)
        self._buffer = buffer
        return MatchResult(MatchResult.PENDING, exact, longer)

    def timeout(self, enabled: Callable[[str], bool]) -> Optional[str]:
        """Resolve a pending prefix with no follow-up key: its own action, or None."""
        action = self._terminal(self._buffer, enabled) if self._buffer else None
        self.reset()
        return action


# Global registry instance
registry = KeybindingRegistry()