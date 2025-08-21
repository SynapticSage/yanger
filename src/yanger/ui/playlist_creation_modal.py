"""Modal dialog for creating new playlists.

Provides a form for entering playlist details.
"""
# Created: 2025-08-21

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Static, Input, Button, RadioSet, RadioButton
from textual.screen import ModalScreen
from textual.message import Message
from textual.validation import Length


class PlaylistCreated(Message):
    """Message sent when a playlist is created."""
    
    def __init__(self, title: str, description: str, privacy: str) -> None:
        """Initialize the message.
        
        Args:
            title: Playlist title
            description: Playlist description
            privacy: Privacy status (private, unlisted, public)
        """
        super().__init__()
        self.title = title
        self.description = description
        self.privacy = privacy


class PlaylistCreationModal(ModalScreen):
    """Modal dialog for creating a new playlist."""
    
    DEFAULT_CSS = """
    PlaylistCreationModal {
        align: center middle;
    }
    
    PlaylistCreationModal > Container {
        width: 60;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    
    PlaylistCreationModal Container#header {
        height: 3;
        margin-bottom: 1;
    }
    
    PlaylistCreationModal Static#title {
        text-align: center;
        text-style: bold;
        color: $primary;
    }
    
    PlaylistCreationModal Input {
        margin: 1 0;
    }
    
    PlaylistCreationModal RadioSet {
        height: 5;
        margin: 1 0;
    }
    
    PlaylistCreationModal Container#buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    
    PlaylistCreationModal Button {
        margin: 0 1;
    }
    """
    
    def compose(self) -> ComposeResult:
        """Compose the modal layout."""
        with Container():
            with Container(id="header"):
                yield Static("Create New Playlist", id="title")
            
            with Vertical():
                yield Static("Title:")
                yield Input(
                    placeholder="Enter playlist title",
                    id="title_input",
                    validators=[Length(minimum=1, maximum=100)]
                )
                
                yield Static("Description (optional):")
                yield Input(
                    placeholder="Enter playlist description",
                    id="description_input",
                    validators=[Length(maximum=5000)]
                )
                
                yield Static("Privacy:")
                with RadioSet(id="privacy_set"):
                    yield RadioButton("Private", value=True, id="private")
                    yield RadioButton("Unlisted", id="unlisted")
                    yield RadioButton("Public", id="public")
                
                with Horizontal(id="buttons"):
                    yield Button("Create", variant="primary", id="create")
                    yield Button("Cancel", variant="default", id="cancel")
    
    def on_mount(self) -> None:
        """Focus the title input when mounted."""
        self.query_one("#title_input", Input).focus()
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "create":
            self.create_playlist()
        else:
            self.dismiss()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle Enter key in input fields."""
        if event.input.id == "title_input":
            # Move to description field
            self.query_one("#description_input", Input).focus()
        elif event.input.id == "description_input":
            # Create the playlist
            self.create_playlist()
    
    def create_playlist(self) -> None:
        """Validate and create the playlist."""
        title_input = self.query_one("#title_input", Input)
        description_input = self.query_one("#description_input", Input)
        
        # Validate title
        title = title_input.value.strip()
        if not title:
            title_input.focus()
            return
        
        description = description_input.value.strip()
        
        # Get privacy setting
        privacy = "private"  # default
        radio_set = self.query_one("#privacy_set", RadioSet)
        if radio_set.pressed_button:
            button_id = radio_set.pressed_button.id
            if button_id == "unlisted":
                privacy = "unlisted"
            elif button_id == "public":
                privacy = "public"
        
        # Send message and dismiss
        self.post_message(PlaylistCreated(title, description, privacy))
        self.dismiss()