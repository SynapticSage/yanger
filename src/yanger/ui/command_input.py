"""Command input widget for YouTube Ranger.

Provides a command line interface similar to vim/ranger.
"""
# Modified: 2025-08-08

from typing import Callable, Optional, List
import shlex

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Static
from textual import events
from textual.suggester import Suggester

from ..keybindings import registry


class CommandSuggester(Suggester):
    """Provides command suggestions based on registered commands."""
    
    async def get_suggestion(self, value: str) -> Optional[str]:
        """Get suggestion for current input."""
        if not value or not value.startswith(":"):
            return None
            
        # Remove the : prefix
        cmd_text = value[1:].strip()
        if not cmd_text:
            return None
            
        # Split command and args
        parts = cmd_text.split(maxsplit=1)
        if not parts:
            return None
            
        cmd_name = parts[0].lower()
        
        # Find matching commands
        for name, cmd in registry.commands.items():
            if name.startswith(cmd_name) and name != cmd_name:
                # Suggest the full command
                if len(parts) == 1:
                    return ":" + name
                else:
                    # Keep the arguments
                    return ":" + name + " " + parts[1]
                    
        return None


class CommandInput(Container):
    """Command input widget for entering commands."""
    
    DEFAULT_CSS = """
    CommandInput {
        dock: bottom;
        width: 100%;
        height: 3;
        display: none;
        background: $surface;
        border-top: solid $accent;
        padding: 0 1;
    }
    
    CommandInput.visible {
        display: block;
    }
    
    CommandInput > Input {
        width: 100%;
        height: 1;
        margin-top: 1;
        background: $background;
        border: tall $accent;
    }
    
    CommandInput .command-hint {
        height: 1;
        color: $text-muted;
        text-align: left;
    }
    """
    
    def __init__(self, 
                 on_submit: Optional[Callable[[str], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_submit_callback = on_submit
        self.on_cancel_callback = on_cancel
        self.command_history: List[str] = []
        self.history_index = -1
        self.input_widget: Optional[Input] = None
        self.hint_widget: Optional[Static] = None
        
    def compose(self) -> ComposeResult:
        """Create command input layout."""
        self.input_widget = Input(
            placeholder="Enter command...",
            suggester=CommandSuggester(),
            id="command-input-field"
        )
        self.hint_widget = Static("", classes="command-hint")
        
        yield self.hint_widget
        yield self.input_widget
        
    def show(self, initial_text: str = ":") -> None:
        """Show the command input."""
        self.add_class("visible")
        if self.input_widget:
            self.input_widget.value = initial_text
            self.input_widget.focus()
            self._update_hint(initial_text)
            
    def hide(self) -> None:
        """Hide the command input."""
        self.remove_class("visible")
        if self.input_widget:
            self.input_widget.value = ""
        if self.hint_widget:
            self.hint_widget.update("")
            
    def _update_hint(self, value: str) -> None:
        """Update hint based on current input."""
        if not self.hint_widget:
            return
            
        if not value or not value.startswith(":"):
            self.hint_widget.update("")
            return
            
        # Remove : prefix
        cmd_text = value[1:].strip()
        if not cmd_text:
            # Show available commands
            commands = ", ".join(sorted(registry.commands.keys()))
            self.hint_widget.update(f"Commands: {commands}")
            return
            
        # Parse command
        parts = cmd_text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        
        # Find exact or partial match
        exact_match = registry.get_command(cmd_name)
        if exact_match:
            # Show syntax for exact match
            self.hint_widget.update(f"Syntax: {exact_match.syntax}")
        else:
            # Show matching commands
            matches = [name for name in registry.commands.keys() 
                      if name.startswith(cmd_name)]
            if matches:
                self.hint_widget.update(f"Did you mean: {', '.join(matches)}?")
            else:
                self.hint_widget.update("Unknown command")
                
    async def on_input_changed(self, event: Input.Changed) -> None:
        """Handle input changes."""
        if event.input.id == "command-input-field":
            self._update_hint(event.value)
            
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle command submission."""
        if event.input.id == "command-input-field":
            command = event.value.strip()
            
            if command and command.startswith(":"):
                # Add to history
                if command not in self.command_history:
                    self.command_history.append(command)
                self.history_index = -1
                
                # Execute callback
                if self.on_submit_callback:
                    self.on_submit_callback(command)
                    
            self.hide()
            
    async def on_key(self, event: events.Key) -> None:
        """Handle key events."""
        if event.key == "escape":
            if self.on_cancel_callback:
                self.on_cancel_callback()
            self.hide()
            event.stop()
            
        elif event.key == "up":
            # Navigate command history
            if self.command_history and self.input_widget:
                if self.history_index < len(self.command_history) - 1:
                    self.history_index += 1
                    self.input_widget.value = self.command_history[
                        -(self.history_index + 1)
                    ]
            event.stop()
            
        elif event.key == "down":
            # Navigate command history
            if self.command_history and self.input_widget:
                if self.history_index > 0:
                    self.history_index -= 1
                    self.input_widget.value = self.command_history[
                        -(self.history_index + 1)
                    ]
                elif self.history_index == 0:
                    self.history_index = -1
                    self.input_widget.value = ":"
            event.stop()
            
        elif event.key == "tab":
            # Accept suggestion
            if self.input_widget and self.input_widget.suggestion:
                self.input_widget.value = self.input_widget.suggestion
                self.input_widget.cursor_position = len(self.input_widget.value)
            event.stop()


def parse_command(command: str) -> tuple[str, List[str]]:
    """Parse a command string into name and arguments.
    
    Args:
        command: Command string starting with ':'
        
    Returns:
        Tuple of (command_name, arguments)
    """
    if not command or not command.startswith(":"):
        return "", []
        
    # Remove : prefix
    cmd_text = command[1:].strip()
    if not cmd_text:
        return "", []
        
    # Use shlex to properly handle quoted arguments
    try:
        parts = shlex.split(cmd_text)
    except ValueError:
        # Fallback to simple split if shlex fails
        parts = cmd_text.split()
        
    if not parts:
        return "", []
        
    return parts[0].lower(), parts[1:]