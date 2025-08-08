"""Status bar widget for YouTube Ranger.

Shows current context, quota usage, and keyboard hints.
"""
# Created: 2025-08-03

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static
from textual.widget import Widget
from textual.reactive import reactive


class StatusBar(Widget):
    """Status bar showing context and quota information."""
    
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $panel;
        color: $text;
        dock: bottom;
    }
    
    StatusBar > Horizontal {
        width: 100%;
        height: 1;
    }
    
    StatusBar .status-left {
        width: 1fr;
        padding: 0 1;
    }
    
    StatusBar .status-center {
        width: 2fr;
        text-align: center;
        padding: 0 1;
        color: $text-muted;
    }
    
    StatusBar .status-right {
        width: 1fr;
        text-align: right;
        padding: 0 1;
    }
    
    StatusBar .quota-warning {
        color: $warning;
        text-style: bold;
    }
    
    StatusBar .quota-critical {
        color: $error;
        text-style: bold;
    }
    """
    
    # Reactive properties
    context = reactive("")
    status = reactive("")
    quota = reactive("")
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.left_widget: Optional[Static] = None
        self.center_widget: Optional[Static] = None
        self.right_widget: Optional[Static] = None
        
    def compose(self) -> ComposeResult:
        """Create status bar layout."""
        with Horizontal():
            self.left_widget = Static("", classes="status-left")
            self.center_widget = Static("", classes="status-center")
            self.right_widget = Static("", classes="status-right")
            
            yield self.left_widget
            yield self.center_widget
            yield self.right_widget
            
    def on_mount(self) -> None:
        """Initialize status bar with default values."""
        self.update_hints()
        
    def update_context(self, context: str, marked_count: int = 0) -> None:
        """Update the current context (left side).
        
        Args:
            context: Context string to display
            marked_count: Number of marked items
        """
        self.context = context
        display_text = context
        
        # Add Mrk indicator if items are marked
        if marked_count > 0:
            display_text = f"[yellow]Mrk[/yellow] {marked_count} | {context}"
            
        if self.left_widget:
            self.left_widget.update(display_text)
            
    def update_status(self, status: str, quota: str = "") -> None:
        """Update status message and quota info."""
        self.status = status
        self.quota = quota
        
        if self.center_widget:
            self.center_widget.update(status)
            
        if self.right_widget and quota:
            # Parse quota to add warning colors
            if "/" in quota:
                used, total = quota.split("/")
                try:
                    used_int = int(used)
                    total_int = int(total)
                    percentage = (used_int / total_int) * 100
                    
                    if percentage >= 90:
                        self.right_widget.add_class("quota-critical")
                        self.right_widget.remove_class("quota-warning")
                    elif percentage >= 75:
                        self.right_widget.add_class("quota-warning")
                        self.right_widget.remove_class("quota-critical")
                    else:
                        self.right_widget.remove_class("quota-warning")
                        self.right_widget.remove_class("quota-critical")
                except ValueError:
                    pass
                    
            self.right_widget.update(f"Quota: {quota}")
            
    def update_hints(self, custom_hints: Optional[str] = None) -> None:
        """Update keyboard hints based on current mode.
        
        Args:
            custom_hints: Custom hint text to display
        """
        if custom_hints:
            hints = custom_hints
        else:
            # Default hints - corrected to match ranger behavior
            hints = "q:quit /:search V:visual v:invert space:mark yy:copy dd:cut pp:paste"
        
        if self.center_widget:
            self.center_widget.update(hints)
            
    def show_message(self, message: str, duration: int = 3) -> None:
        """Show a temporary message in the center."""
        if self.center_widget:
            original = self.center_widget.renderable
            self.center_widget.update(message)
            
            # Reset after duration
            from textual.timer import Timer
            
            def reset():
                self.center_widget.update(original)
                
            self.set_timer(duration, reset)