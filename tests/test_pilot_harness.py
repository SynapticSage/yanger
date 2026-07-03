"""Tier 1 #2: a real Textual Pilot harness for on_key / modal flows.

This is the harness whose ABSENCE let the `:run` confirm-modal critical (a dangerous=False
modal that was keyboard-inoperable) pass 219 unit tests. It boots the actual app headless —
$HOME sandboxed so the cache lands in tmp, auth bypassed into offline mode — and drives real
keystrokes through the real screen stack.
"""

import pytest
import pytest_asyncio

from textual.containers import ScrollableContainer

from yanger.app import YouTubeRangerApp
from yanger.ui.confirmation_modal import ConfirmationModal
from yanger.ui.help_overlay import HelpOverlay


@pytest_asyncio.fixture
async def app_pilot(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # cache/config -> tmp, not the real home

    async def _offline(self):
        # Skip real OAuth; run the app in offline mode (enough to exercise key/modal handling).
        self.offline_mode = True
        self.api_client = None

    monkeypatch.setattr(YouTubeRangerApp, "setup_authentication", _offline)

    app = YouTubeRangerApp()
    async with app.run_test() as pilot:
        yield app, pilot


async def test_app_boots_headless(app_pilot):
    app, _pilot = app_pilot
    assert app.is_running
    assert app.offline_mode is True


async def test_confirm_modal_y_confirms_via_keyboard(app_pilot):
    """The regression the pilot exists for: a dangerous=False modal must CONFIRM (not just
    dismiss) on 'y'. We capture the dismiss result to prove confirm vs cancel."""
    app, pilot = app_pilot
    results = []
    app.push_screen(
        ConfirmationModal(title="Confirm Run", message="Run on 6 videos?",
                          action="run_custom_command", dangerous=False),
        results.append,
    )
    await pilot.pause()
    assert isinstance(app.screen, ConfirmationModal)

    await pilot.press("y")
    await pilot.pause()
    assert not isinstance(app.screen, ConfirmationModal)
    assert results == [True]  # 'y' CONFIRMED, not merely dismissed


async def test_help_is_modal_and_owns_the_keyboard(app_pilot):
    """Regression for 'help ignores arrow/j/k': help is now a ModalScreen that captures focus,
    so its scroll area (not the miller view behind it) receives navigation keys."""
    app, pilot = app_pilot
    app.action_help()
    await pilot.pause()

    assert isinstance(app.screen, HelpOverlay)             # a real modal on the stack now
    scroll = app.screen.query_one(ScrollableContainer)
    assert app.focused is scroll                            # focus moved to the help, not the list

    await pilot.press("end")                                # content is longer than the modal
    await pilot.pause()
    assert scroll.scroll_offset.y > 0                       # it actually scrolled

    await pilot.press("k")                                  # a nav key is consumed, stays modal
    await pilot.pause()
    assert isinstance(app.screen, HelpOverlay)

    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(app.screen, HelpOverlay)          # dismissed back to the base screen


async def test_command_input_is_on_screen_when_typing(app_pilot):
    """Regression for the 'can't see what I'm typing' bug: the command Input was composited
    off-screen (rows 30-31 on a 30-row screen) by a dock collision + internal margin. Assert
    the rendered region is on-screen AND not covered, and that typed text echoes into it."""
    app, pilot = app_pilot
    app.action_command_mode()
    await pilot.pause()

    iw = app.query_one("#command-input").input_widget
    assert iw.region.height > 0
    assert iw.region.bottom <= app.size.height, f"Input off-screen: {iw.region}"
    # The Input's own center must hit the Input (before the fix this raised NoWidget /
    # returned the status bar, i.e. it was hidden behind/below other widgets).
    widget, _ = app.screen.get_widget_at(*iw.region.center)
    assert widget is iw

    await pilot.press("g", "t")
    await pilot.pause()
    assert "gt" in iw.value  # typed chars land in (and render in) the visible input


async def test_confirm_modal_escape_cancels(app_pilot):
    app, pilot = app_pilot
    results = []
    app.push_screen(
        ConfirmationModal(title="Confirm", message="?", action="run_custom_command",
                          dangerous=False),
        results.append,
    )
    await pilot.pause()
    await pilot.press("escape")
    await pilot.pause()
    assert not isinstance(app.screen, ConfirmationModal)
    assert results == [False]  # escape CANCELLED
