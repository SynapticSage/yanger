#!/usr/bin/env python3
"""Minimal test app for debugging command input widget."""
# Modified: 2025-08-10

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Input, Static, Header
from textual import events


class TestCommandInput(Container):
    """Test command input widget."""
    
    DEFAULT_CSS = """
    TestCommandInput {
        dock: bottom;
        width: 100%;
        height: 3;
        display: none;
        background: $surface;
        border-top: solid $accent;
        padding: 0 1;
    }
    
    TestCommandInput.visible {
        display: block;
    }
    
    TestCommandInput > Input {
        width: 100%;
        height: 1;
        margin-top: 1;
        color: $text;  /* Explicit text color */
        background: $background;
        border: tall $accent;
    }
    
    TestCommandInput > Input:focus {
        border: tall $primary;
        color: $text;
        background: $background;
    }
    
    TestCommandInput .hint {
        height: 1;
        color: $text-muted;
        text-align: left;
    }
    """
    
    def compose(self) -> ComposeResult:
        """Create the widget."""
        yield Static("Type command here:", classes="hint")
        yield Input(placeholder="Enter command...", id="test-input")
    
    def show(self):
        """Show the input."""
        self.add_class("visible")
        input_widget = self.query_one("#test-input", Input)
        input_widget.focus()
    
    def hide(self):
        """Hide the input."""
        self.remove_class("visible")
        input_widget = self.query_one("#test-input", Input)
        input_widget.value = ""


class CommandTestApp(App):
    """Test application for command input."""
    
    CSS = """
    Screen {
        background: $background;
    }
    
    #main {
        width: 100%;
        height: 100%;
        padding: 1;
    }
    
    #output {
        width: 100%;
        height: 100%;
        border: solid $primary;
        padding: 1;
    }
    
    #status {
        dock: bottom;
        height: 1;
        background: $panel;
        color: $text;
        text-align: center;
    }
    """
    
    def compose(self) -> ComposeResult:
        """Create the app layout."""
        yield Header(show_clock=True)
        
        with Container(id="main"):
            yield Static(
                "Press ':' to open command input\n"
                "Press 'Escape' to close it\n"
                "Press 'q' to quit\n\n"
                "Output will appear below:",
                id="output"
            )
        
        yield Static("Ready", id="status")
        
        self.command_input = TestCommandInput()
        yield self.command_input
    
    def on_key(self, event: events.Key) -> None:
        """Handle key events."""
        if event.key == "colon":
            self.command_input.show()
            self.query_one("#status", Static).update("Command mode active")
        elif event.key == "escape":
            self.command_input.hide()
            self.query_one("#status", Static).update("Ready")
        elif event.key == "q":
            self.exit()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        value = event.value
        output = self.query_one("#output", Static)
        current = output.renderable
        output.update(f"{current}\n> Command: {value}")
        self.command_input.hide()
        self.query_one("#status", Static).update(f"Executed: {value}")


if __name__ == "__main__":
    app = CommandTestApp()
    app.run()