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


@pytest.mark.parametrize("dismiss_key", ["escape", "q", "question_mark"])
async def test_help_dismiss_keys(app_pilot, dismiss_key):
    app, pilot = app_pilot
    app.action_help()
    await pilot.pause()
    assert isinstance(app.screen, HelpOverlay)
    await pilot.press(dismiss_key)
    await pilot.pause()
    assert not isinstance(app.screen, HelpOverlay)


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


def _composited_text(app) -> str:
    """The text actually PAINTED to the terminal (compositor output), rows joined.

    Input.value can be fully populated while nothing is painted (that trap hid the
    'typing is invisible' bug behind value-based assertions) — always assert against
    this, not widget state. `_compositor.render_strips()` is private but stable
    across the Textual versions we run (6.5.0 venv, 8.2.8 homebrew runtime).
    """
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(seg.text for seg in strip) for strip in strips)


async def test_command_input_typed_text_is_composited(app_pilot):
    """Regression: `CommandInput > Input:focus { border: tall }` inside height:1 left
    ZERO content rows — only border glyphs were painted and typed text never appeared
    on screen (while Input.value filled invisibly)."""
    app, pilot = app_pilot
    app.action_command_mode()
    await pilot.pause()
    await pilot.press("d", "l")
    await pilot.pause()

    iw = app.query_one("#command-input").input_widget
    assert iw.value == ":dl"  # select_on_focus must not eat the pre-filled ':'
    assert iw.size.height >= 1, f"Input content area collapsed: {iw.size}"
    assert ":dl" in _composited_text(app), "typed command text was not painted"


async def test_command_enter_preserves_colon_prefix(app_pilot):
    """Regression: select_on_focus selected the pre-filled ':' so the first keystroke
    replaced it; submissions then failed on_input_submitted's startswith(':') gate and
    Enter silently did nothing."""
    app, pilot = app_pilot
    received = []
    app.command_input.on_submit_callback = received.append
    app.action_command_mode()
    await pilot.pause()
    await pilot.press("h", "e", "l", "p", "enter")
    await pilot.pause()
    assert received == [":help"]


async def test_search_input_typed_text_is_composited(app_pilot):
    """Regression (same family as the command input): SearchInput stacked '/' label and
    a 3-row bordered Input vertically inside a 1-row content area — the Input's text
    row was clipped out and typed search text never painted."""
    app, pilot = app_pilot
    await pilot.press("slash")
    await pilot.pause()
    sinp = app.query_one("#search-input")
    assert sinp.has_focus
    await pilot.press("x", "y", "z")
    await pilot.pause()
    assert sinp.value == "xyz"
    assert "xyz" in _composited_text(app), "typed search text was not painted"


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
