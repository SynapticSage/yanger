"""Confirmation modal dialog for dangerous operations.

Provides a yes/no confirmation dialog with customizable message.
"""
# Created: 2025-08-24

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Button, Label, Static
from textual.screen import ModalScreen
from textual.message import Message


class ConfirmationResult(Message):
    """Message sent when confirmation dialog is closed."""
    
    def __init__(self, confirmed: bool, action: str = "") -> None:
        """Initialize confirmation result.
        
        Args:
            confirmed: Whether the user confirmed (True) or cancelled (False)
            action: Optional action identifier for handling multiple confirmations
        """
        super().__init__()
        self.confirmed = confirmed
        self.action = action


class ConfirmationModal(ModalScreen):
    """Modal dialog for confirming dangerous operations."""
    
    DEFAULT_CSS = """
    ConfirmationModal {
        align: center middle;
    }
    
    ConfirmationModal > Container {
        width: 60;
        height: auto;
        min-height: 11;
        max-height: 20;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    
    ConfirmationModal .modal-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    
    ConfirmationModal .modal-message {
        margin-bottom: 1;
        color: $text;
    }
    
    ConfirmationModal .modal-details {
        margin-bottom: 1;
        color: $text-muted;
        text-style: italic;
    }
    
    ConfirmationModal .button-container {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    
    ConfirmationModal Button {
        width: 12;
        margin: 0 1;
    }
    
    ConfirmationModal .confirm-button {
        background: $error;
    }
    
    ConfirmationModal .cancel-button {
        background: $primary;
    }
    """
    
    def __init__(
        self,
        title: str = "Confirm Action",
        message: str = "Are you sure?",
        details: str = "",
        confirm_text: str = "Yes",
        cancel_text: str = "Cancel",
        action: str = "",
        dangerous: bool = True
    ) -> None:
        """Initialize confirmation modal.
        
        Args:
            title: Title of the dialog
            message: Main confirmation message
            details: Additional details (optional)
            confirm_text: Text for confirm button
            cancel_text: Text for cancel button
            action: Action identifier for result message
            dangerous: Whether this is a dangerous operation (affects styling)
        """
        super().__init__()
        self.title = title
        self.message = message
        self.details = details
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
        self.action = action
        self.dangerous = dangerous
        
    def compose(self) -> ComposeResult:
        """Compose the modal UI."""
        with Container():
            yield Label(self.title, classes="modal-title")
            yield Static(self.message, classes="modal-message")
            
            if self.details:
                yield Static(self.details, classes="modal-details")
            
            with Horizontal(classes="button-container"):
                if self.dangerous:
                    yield Button(
                        self.confirm_text,
                        variant="error",
                        id="confirm",
                        classes="confirm-button"
                    )
                else:
                    yield Button(
                        self.confirm_text,
                        variant="primary",
                        id="confirm",
                        classes="confirm-button"
                    )
                    
                yield Button(
                    self.cancel_text,
                    variant="default",
                    id="cancel",
                    classes="cancel-button"
                )
    
    def on_mount(self) -> None:
        """Focus the cancel button by default for safety."""
        cancel_button = self.query_one("#cancel", Button)
        cancel_button.focus()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)
    
    def on_key(self, event) -> None:
        """Handle keyboard shortcuts."""
        if event.key == "escape":
            self.dismiss(False)
        elif event.key == "y" and self.dangerous:
            # Allow 'y' for yes on dangerous operations
            self.dismiss(True)
        elif event.key == "n":
            # Allow 'n' for no
            self.dismiss(False)
    
    def dismiss(self, result: bool) -> None:
        """Dismiss the modal and send result message.
        
        Args:
            result: Whether the user confirmed the action
        """
        # Send the result message before dismissing
        self.post_message(ConfirmationResult(result, self.action))
        # Call parent dismiss
        super().dismiss(result)