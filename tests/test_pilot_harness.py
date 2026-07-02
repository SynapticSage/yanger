"""Tier 1 #2: a real Textual Pilot harness for on_key / modal flows.

This is the harness whose ABSENCE let the `:run` confirm-modal critical (a dangerous=False
modal that was keyboard-inoperable) pass 219 unit tests. It boots the actual app headless —
$HOME sandboxed so the cache lands in tmp, auth bypassed into offline mode — and drives real
keystrokes through the real screen stack.
"""

import pytest
import pytest_asyncio

from yanger.app import YouTubeRangerApp
from yanger.ui.confirmation_modal import ConfirmationModal


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
