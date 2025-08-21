"""Modal dialog for renaming playlists and videos.

Provides a simple form for entering a new name.
"""
# Created: 2025-08-21

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Input, Button
from textual.screen import ModalScreen
from textual.message import Message
from textual.validation import Length


class ItemRenamed(Message):
    """Message sent when an item is renamed."""
    
    def __init__(self, item_type: str, item_id: str, old_name: str, new_name: str) -> None:
        """Initialize the message.
        
        Args:
            item_type: Type of item ('playlist' or 'video')
            item_id: ID of the item
            old_name: Original name
            new_name: New name
        """
        super().__init__()
        self.item_type = item_type
        self.item_id = item_id
        self.old_name = old_name
        self.new_name = new_name


class RenameModal(ModalScreen):
    """Modal dialog for renaming items."""
    
    DEFAULT_CSS = """
    RenameModal {
        align: center middle;
    }
    
    RenameModal > Container {
        width: 50;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    
    RenameModal Container#header {
        height: 3;
        margin-bottom: 1;
    }
    
    RenameModal Static#title {
        text-align: center;
        text-style: bold;
        color: $primary;
    }
    
    RenameModal Static#current_name {
        color: $text-muted;
        margin-bottom: 1;
    }
    
    RenameModal Input {
        margin: 1 0;
    }
    
    RenameModal Container#buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    
    RenameModal Button {
        margin: 0 1;
    }
    """
    
    def __init__(self, item_type: str, item_id: str, current_name: str) -> None:
        """Initialize the rename modal.
        
        Args:
            item_type: Type of item being renamed ('playlist' or 'video')
            item_id: ID of the item
            current_name: Current name of the item
        """
        super().__init__()
        self.item_type = item_type
        self.item_id = item_id
        self.current_name = current_name
    
    def compose(self) -> ComposeResult:
        """Compose the modal layout."""
        title_text = f"Rename {self.item_type.capitalize()}"
        
        with Container():
            with Container(id="header"):
                yield Static(title_text, id="title")
            
            with Vertical():
                yield Static(f"Current: {self.current_name}", id="current_name")
                
                yield Static("New name:")
                yield Input(
                    value=self.current_name,
                    placeholder="Enter new name",
                    id="name_input",
                    validators=[Length(minimum=1, maximum=100)]
                )
                
                with Horizontal(id="buttons"):
                    yield Button("Rename", variant="primary", id="rename")
                    yield Button("Cancel", variant="default", id="cancel")
    
    def on_mount(self) -> None:
        """Focus and select the input when mounted."""
        name_input = self.query_one("#name_input", Input)
        name_input.focus()
        # Select all text for easy replacement
        name_input.action_select_all()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "rename":
            self.rename_item()
        else:
            self.dismiss()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input field."""
        self.rename_item()
    
    def rename_item(self) -> None:
        """Validate and rename the item."""
        name_input = self.query_one("#name_input", Input)
        
        # Validate new name
        new_name = name_input.value.strip()
        if not new_name:
            name_input.focus()
            return
        
        # Don't rename if name hasn't changed
        if new_name == self.current_name:
            self.dismiss()
            return
        
        # Send message and dismiss
        self.post_message(ItemRenamed(
            self.item_type,
            self.item_id,
            self.current_name,
            new_name
        ))
        self.dismiss()