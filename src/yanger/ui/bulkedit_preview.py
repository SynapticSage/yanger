"""Preview modal for bulk edit changes.

Shows a summary of changes that will be applied from bulk edit.
"""
# Created: 2025-09-22

from textual.app import ComposeResult
from textual.containers import Container, Vertical, Horizontal, ScrollableContainer
from textual.widgets import Static, Button
from textual.message import Message
from textual import events

from ..bulkedit import BulkEditChanges


class BulkEditConfirmed(Message):
    """Message sent when user confirms bulk edit."""

    def __init__(self, changes: BulkEditChanges):
        super().__init__()
        self.changes = changes


class BulkEditCancelled(Message):
    """Message sent when user cancels bulk edit."""
    pass


class BulkEditPreview(Container):
    """Modal showing preview of bulk edit changes."""

    DEFAULT_CSS = """
    BulkEditPreview {
        layer: modal;
        dock: top;
        width: 80%;
        height: 80%;
        max-width: 100;
        max-height: 40;
        margin: 2 4;
        background: $surface;
        border: thick $accent;
        padding: 1;
    }

    BulkEditPreview > Vertical {
        width: 100%;
        height: 100%;
    }

    BulkEditPreview .header {
        width: 100%;
        height: 3;
        background: $accent;
        color: $text;
        text-align: center;
        text-style: bold;
        padding: 1;
    }

    BulkEditPreview .changes-container {
        width: 100%;
        height: 1fr;
        border: solid $primary;
        margin: 1 0;
        padding: 1;
        overflow-y: scroll;
    }

    BulkEditPreview .change-section {
        margin-bottom: 1;
    }

    BulkEditPreview .section-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 0;
    }

    BulkEditPreview .change-item {
        padding-left: 2;
        margin: 0;
    }

    BulkEditPreview .move-change {
        color: $success;
    }

    BulkEditPreview .delete-change {
        color: $error;
    }

    BulkEditPreview .rename-change {
        color: $warning;
    }

    BulkEditPreview .reorder-change {
        color: $accent;
    }

    BulkEditPreview .summary {
        width: 100%;
        height: 3;
        background: $surface-lighten-1;
        padding: 1;
        text-align: center;
        border: tall $primary;
        margin: 1 0;
    }

    BulkEditPreview .buttons {
        width: 100%;
        height: 3;
        layout: horizontal;
        align: center middle;
    }

    BulkEditPreview Button {
        width: 20;
        margin: 0 2;
    }

    BulkEditPreview .confirm-button {
        background: $success;
    }

    BulkEditPreview .cancel-button {
        background: $error;
    }

    BulkEditPreview .dry-run-button {
        background: $warning;
    }
    """

    def __init__(self, changes: BulkEditChanges, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.changes = changes
        self.can_dismiss = True

    def compose(self) -> ComposeResult:
        """Build the preview layout."""
        with Vertical():
            yield Static("Bulk Edit Preview", classes="header")

            # Changes container
            with ScrollableContainer(classes="changes-container"):
                # Moves section
                if self.changes.moves:
                    yield Static("Video Moves", classes="section-title")
                    for move in self.changes.moves[:10]:  # Show first 10
                        yield Static(
                            f"• {move.video.title[:50]}... → {move.target_playlist_id[:20]}",
                            classes="change-item move-change"
                        )
                    if len(self.changes.moves) > 10:
                        yield Static(
                            f"  ... and {len(self.changes.moves) - 10} more",
                            classes="change-item"
                        )

                # Reorders section
                if self.changes.reorders:
                    yield Static("Reorders", classes="section-title")
                    for reorder in self.changes.reorders[:10]:
                        yield Static(
                            f"• {reorder.video.title[:50]}... "
                            f"(pos {reorder.old_position} → {reorder.new_position})",
                            classes="change-item reorder-change"
                        )
                    if len(self.changes.reorders) > 10:
                        yield Static(
                            f"  ... and {len(self.changes.reorders) - 10} more",
                            classes="change-item"
                        )

                # Renames section
                if self.changes.renames:
                    yield Static("Renames", classes="section-title")
                    for rename in self.changes.renames[:10]:
                        yield Static(
                            f"• {rename.old_name[:30]}... → {rename.new_name[:30]}...",
                            classes="change-item rename-change"
                        )
                    if len(self.changes.renames) > 10:
                        yield Static(
                            f"  ... and {len(self.changes.renames) - 10} more",
                            classes="change-item"
                        )

                # Deletions section
                if self.changes.deletions:
                    yield Static("Deletions", classes="section-title")
                    for video, playlist_id in self.changes.deletions[:10]:
                        yield Static(
                            f"• {video.title[:50]}...",
                            classes="change-item delete-change"
                        )
                    if len(self.changes.deletions) > 10:
                        yield Static(
                            f"  ... and {len(self.changes.deletions) - 10} more",
                            classes="change-item"
                        )

                # No changes
                if self.changes.is_empty():
                    yield Static(
                        "No changes detected",
                        classes="change-item"
                    )

            # Summary
            yield Static(
                f"Summary: {self.changes.summary()}",
                classes="summary"
            )

            # Buttons
            with Horizontal(classes="buttons"):
                yield Button("Apply Changes", variant="success",
                           classes="confirm-button", id="confirm")
                yield Button("Dry Run", variant="warning",
                           classes="dry-run-button", id="dry-run")
                yield Button("Cancel", variant="error",
                           classes="cancel-button", id="cancel")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "confirm":
            await self.post_message(BulkEditConfirmed(self.changes))
            await self.remove()
        elif event.button.id == "cancel":
            await self.post_message(BulkEditCancelled())
            await self.remove()
        elif event.button.id == "dry-run":
            # For dry run, we still send confirmed but app should handle differently
            self.changes.dry_run = True
            await self.post_message(BulkEditConfirmed(self.changes))
            await self.remove()

    async def on_key(self, event: events.Key) -> None:
        """Handle key events."""
        if event.key == "escape":
            await self.post_message(BulkEditCancelled())
            await self.remove()
            event.stop()
        elif event.key == "enter":
            await self.post_message(BulkEditConfirmed(self.changes))
            await self.remove()
            event.stop()