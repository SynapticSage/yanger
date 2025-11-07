"""Search input widget for YouTube Ranger.

Provides an overlay input field for searching videos.
"""
# Modified: 2025-09-14

from typing import Optional, Callable
import logging
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Static
from textual.widget import Widget
from textual.reactive import reactive
from textual import events
from textual.binding import Binding

logger = logging.getLogger(__name__)


class SearchInput(Container):
    """Search input overlay widget."""
    
    DEFAULT_CSS = """
    SearchInput {
        layer: search;
        dock: top;
        height: 3;
        background: $panel;
        border: tall $primary;
        padding: 0 1;
        display: none;
    }
    
    SearchInput.visible {
        display: block;
    }
    
    SearchInput Input {
        width: 100%;
        background: black !important;
        color: white !important;
        text-style: not dim !important;
        border: tall $accent;
    }
    
    SearchInput Input:focus {
        border: tall $primary;
        color: white !important;
        background: rgb(20, 20, 20) !important;
        text-style: not dim !important;
    }
    
    /* Override ALL internal Input elements */
    SearchInput Input * {
        color: white !important;
    }
    
    /* Target internal input elements */
    SearchInput Input > .input--placeholder {
        color: gray !important;
    }
    
    SearchInput Input > .input--cursor {
        color: white !important;
        background: white !important;
    }
    
    SearchInput Input .input--suggestion {
        color: darkgray !important;
    }
    
    SearchInput .search-label {
        width: auto;
        margin-right: 1;
        color: $text-muted;
    }
    
    SearchInput .search-hint {
        width: auto;
        margin-left: 1;
        color: $text-muted;
        text-style: italic;
    }
    """
    
    BINDINGS = [
        Binding("escape", "cancel", "Cancel search"),
        Binding("enter", "submit", "Search"),
    ]
    
    def __init__(
        self,
        on_search: Optional[Callable[[str], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        *args,
        **kwargs
    ):
        """Initialize search input.
        
        Args:
            on_search: Callback when search is submitted
            on_cancel: Callback when search is cancelled
        """
        super().__init__(*args, **kwargs)
        self.on_search = on_search
        self.on_cancel = on_cancel
        self.input_field: Optional[Input] = None
        
    def compose(self) -> ComposeResult:
        """Create search input layout."""
        with Container(classes="search-container"):
            yield Static("/", classes="search-label")
            self.input_field = Input(
                placeholder="Search videos...",
                id="search-input"
            )
            # Force inline styles to ensure text visibility
            self.input_field.styles.color = "white"
            self.input_field.styles.background = "black"
            self.input_field.styles.text_style = "not dim"
            yield self.input_field
            yield Static("ESC to cancel", classes="search-hint")
            
    def show(self, placeholder: str = "Search videos...") -> None:
        """Show the search input and focus it.

        Args:
            placeholder: Placeholder text for the input field
        """
        self.add_class("visible")
        if self.input_field:
            self.input_field.placeholder = placeholder
            self.input_field.value = ""

            # Force styles directly on the Input widget after showing
            self.input_field.styles.color = "white"
            self.input_field.styles.background = "rgb(0,0,0)"
            self.input_field.styles.text_style = "bold"

            self.input_field.focus()

            # Debug: Log actual styles
            logger.debug(f"Search input value: '{self.input_field.value}'")
            logger.debug(f"Search color: {self.input_field.styles.color}")
            logger.debug(f"Search background: {self.input_field.styles.background}")
            logger.debug(f"Search has focus: {self.input_field.has_focus}")
            
    def hide(self) -> None:
        """Hide the search input."""
        self.remove_class("visible")
        if self.input_field:
            self.input_field.blur()
            
    def action_cancel(self) -> None:
        """Cancel search and hide input."""
        self.hide()
        if self.on_cancel:
            self.on_cancel()
            
    def action_submit(self) -> None:
        """Submit search query."""
        if self.input_field and self.input_field.value:
            if self.on_search:
                self.on_search(self.input_field.value)
            # Keep the search bar visible for n/N navigation
            
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        self.action_submit()
        # Hide the search input after submission so n/N navigation works
        self.hide()


class SearchHighlighter:
    """Helper class to highlight search matches in text."""
    
    @staticmethod
    def highlight(text: str, query: str, highlight_style: str = "bold yellow") -> str:
        """Highlight search query in text.
        
        Args:
            text: Text to search in
            query: Search query
            highlight_style: Style to apply to matches
            
        Returns:
            Text with Rich markup for highlighting
        """
        if not query:
            return text
            
        # Case-insensitive search
        import re
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        
        # Find all matches
        matches = list(pattern.finditer(text))
        if not matches:
            return text
            
        # Build result with highlighting
        result = []
        last_end = 0
        
        for match in matches:
            # Add text before match
            result.append(text[last_end:match.start()])
            # Add highlighted match
            result.append(f"[{highlight_style}]{text[match.start():match.end()]}[/{highlight_style}]")
            last_end = match.end()
            
        # Add remaining text
        result.append(text[last_end:])
        
        return "".join(result)