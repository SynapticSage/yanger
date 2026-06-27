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


# ---------------------------------------------------------------------------
# Bulk-edit parser/executor unit tests
#
# (This module also hosts the manual command-input debug app above; the tests
# below are the pytest-collectable part and exercise yanger.bulkedit fixes for
# cross-playlist keying, the parse-coverage guard, deletion-cascade reorders,
# and the synchronous executor.)
# ---------------------------------------------------------------------------
import asyncio
from unittest.mock import MagicMock

import pytest

from yanger.models import Playlist, Video
from yanger.bulkedit import (
    BulkEditGenerator,
    BulkEditParser,
    BulkEditExecutor,
    BulkEditChanges,
    BulkEditParseError,
    VideoMove,
    VideoReorder,
)


def _video(vid, item, title="Title", playlist_id=None):
    return Video(id=vid, playlist_item_id=item, title=title,
                 channel_title="ch", playlist_id=playlist_id)


def _playlist(pid, title):
    return Playlist(id=pid, title=title)


def _pl_line(pid, title):
    return f"- {title} <!-- id:{pid} -->"


def _vid_line(vid, item, title):
    return f"  - {title} <!-- id:{vid},item:{item} -->"


def test_cross_playlist_duplicate_no_edits_yields_zero_ops():
    """A video present in two playlists, unedited, must produce NO operations."""
    p1, p2 = _playlist("p1", "PL1"), _playlist("p2", "PL2")
    vbp = {
        "p1": [_video("vid", "item1", "Shared", "p1")],
        "p2": [_video("vid", "item2", "Shared", "p2")],
    }
    content = BulkEditGenerator.generate([p1, p2], vbp)
    changes = BulkEditParser().parse(content, [p1, p2], vbp)
    assert changes.is_empty()
    assert changes.moves == []
    assert changes.reorders == []
    assert changes.deletions == []


def test_cross_playlist_move_emitted_once():
    """Moving one occurrence to another playlist is a single move, no deletion."""
    p1, p2 = _playlist("p1", "PL1"), _playlist("p2", "PL2")
    vbp = {"p1": [_video("vid", "item1", "Shared", "p1")], "p2": []}
    content = "\n".join([
        _pl_line("p1", "PL1"),
        _pl_line("p2", "PL2"),
        _vid_line("vid", "item1", "Shared"),  # now under p2
    ])
    changes = BulkEditParser().parse(content, [p1, p2], vbp)
    assert len(changes.moves) == 1
    assert changes.moves[0].source_playlist_id == "p1"
    assert changes.moves[0].target_playlist_id == "p2"
    assert changes.deletions == []
    assert changes.reorders == []


def test_single_deletion_does_not_cascade_reorders():
    """Deleting one video must not reorder every following video (fix #5)."""
    p = _playlist("p", "PL")
    vids = [_video(f"v{i}", f"i{i}", f"T{i}", "p") for i in range(5)]
    vbp = {"p": vids}
    content = "\n".join([_pl_line("p", "PL")] + [
        _vid_line(v.id, v.playlist_item_id, v.title)
        for v in vids if v.playlist_item_id != "i2"
    ])
    changes = BulkEditParser().parse(content, [p], vbp)
    assert [d[0].playlist_item_id for d in changes.deletions] == ["i2"]
    assert changes.reorders == []
    assert changes.moves == []


def test_reorder_within_playlist_emitted():
    """Genuinely reordering surviving videos still emits reorders."""
    p = _playlist("p", "PL")
    vids = [_video(f"v{i}", f"i{i}", f"T{i}", "p") for i in range(3)]
    vbp = {"p": vids}
    content = "\n".join([
        _pl_line("p", "PL"),
        _vid_line("v1", "i1", "T1"),  # swapped first two
        _vid_line("v0", "i0", "T0"),
        _vid_line("v2", "i2", "T2"),
    ])
    changes = BulkEditParser().parse(content, [p], vbp)
    assert {r.video.playlist_item_id for r in changes.reorders} == {"i0", "i1"}
    assert changes.deletions == []


def test_unmatched_line_aborts_instead_of_deleting():
    """An unparseable line aborts the apply rather than inferring deletions."""
    p = _playlist("p", "PL")
    vbp = {"p": [_video("v0", "i0", "T0", "p")]}
    content = "\n".join([
        _pl_line("p", "PL"),
        _vid_line("v0", "i0", "T0"),
        "  - reindented line whose id marker got mangled",  # matches neither
    ])
    with pytest.raises(BulkEditParseError):
        BulkEditParser().parse(content, [p], vbp)


def test_mass_deletion_aborts():
    """Deleting > half a non-trivial library looks like a parse glitch -> abort."""
    p = _playlist("p", "PL")
    vids = [_video(f"v{i}", f"i{i}", f"T{i}", "p") for i in range(12)]
    vbp = {"p": vids}
    content = "\n".join([_pl_line("p", "PL")] + [
        _vid_line(v.id, v.playlist_item_id, v.title) for v in vids[:5]
    ])  # 7 of 12 removed
    with pytest.raises(BulkEditParseError):
        BulkEditParser().parse(content, [p], vbp)


def test_moderate_deletion_allowed():
    """A modest deletion fraction is honored, not blocked by the guard."""
    p = _playlist("p", "PL")
    vids = [_video(f"v{i}", f"i{i}", f"T{i}", "p") for i in range(12)]
    vbp = {"p": vids}
    content = "\n".join([_pl_line("p", "PL")] + [
        _vid_line(v.id, v.playlist_item_id, v.title) for v in vids[:9]
    ])  # 3 of 12 removed
    changes = BulkEditParser().parse(content, [p], vbp)
    assert len(changes.deletions) == 3
    assert changes.reorders == []


def test_executor_uses_sync_client_methods():
    """Executor calls the real (synchronous) client methods without awaiting."""
    client = MagicMock()
    client.add_video_to_playlist.return_value = "new_item"
    executor = BulkEditExecutor(client)
    changes = BulkEditChanges(
        moves=[VideoMove(_video("vm", "id_mov", "Mov", "p1"), "p1", "p2", 0)],
        reorders=[VideoReorder(_video("vr", "id_reo", "Reo", "p2"), "p2", 0, 1)],
        deletions=[(_video("vd", "id_del", "Del", "p"), "p")],
    )
    results = asyncio.run(executor.execute(changes))
    client.remove_video_from_playlist.assert_any_call("id_del")
    client.add_video_to_playlist.assert_called_once_with("vm", "p2", position=0)
    client.update_video_position.assert_called_once_with("id_reo", "p2", "vr", 1)
    assert not client.remove_from_playlist.called  # the old, wrong name
    assert results["failed"] == []


if __name__ == "__main__":
    app = CommandTestApp()
    app.run()