#!/usr/bin/env python3
"""Test script to debug input visibility issues."""

from textual.app import App, ComposeResult
from textual.widgets import Input, Static
from textual.containers import Container

class TestInputApp(App):
    """Test app to debug input visibility."""
    
    CSS = """
    Screen {
        background: $surface;
    }
    
    Container {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    
    Input {
        width: 100%;
        background: $background;
        color: $text;
        border: tall $accent;
    }
    
    Input:focus {
        border: tall $primary;
        color: $text;
    }
    
    /* Try explicit colors */
    .test-input {
        color: white;
        background: black;
    }
    """
    
    def compose(self) -> ComposeResult:
        """Compose the test UI."""
        with Container():
            yield Static("Default Textual Input:")
            yield Input(placeholder="Type here - default styling", id="default")
            
            yield Static("\nExplicit colors Input:")
            yield Input(
                placeholder="Type here - explicit colors",
                id="explicit",
                classes="test-input"
            )
            
            yield Static("\nDifferent approach:")
            input3 = Input(placeholder="Type here - different approach", id="different")
            # Try setting styles programmatically
            yield input3
    
    def on_mount(self) -> None:
        """Focus first input on mount."""
        self.query_one("#default", Input).focus()
    
    def on_input_changed(self, event: Input.Changed) -> None:
        """Log when input changes."""
        print(f"Input changed: '{event.value}' from {event.input.id}")


if __name__ == "__main__":
    app = TestInputApp()
    app.run()