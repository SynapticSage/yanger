"""Regression: ConfirmationModal must be keyboard-operable regardless of `dangerous`.

The `:run` large-selection confirm uses `dangerous=False`; a prior bug gated the `y`
confirm key on `dangerous`, so that modal could only be cancelled or mouse-clicked (on_key
stops every key, so Tab/Enter never reach the buttons). These tests drive on_key directly
with `dismiss` mocked — no running app needed.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from yanger.ui.confirmation_modal import ConfirmationModal


def _modal(dangerous):
    m = ConfirmationModal(title="t", message="m", action="run_custom_command", dangerous=dangerous)
    m.dismiss = MagicMock()
    return m


def _key(k):
    return SimpleNamespace(key=k, stop=lambda: None)


def test_y_confirms_non_dangerous_modal():
    m = _modal(dangerous=False)
    m.on_key(_key("y"))
    m.dismiss.assert_called_once_with(True)


def test_y_confirms_dangerous_modal():
    m = _modal(dangerous=True)
    m.on_key(_key("y"))
    m.dismiss.assert_called_once_with(True)


def test_n_cancels():
    m = _modal(dangerous=False)
    m.on_key(_key("n"))
    m.dismiss.assert_called_once_with(False)


def test_escape_cancels():
    m = _modal(dangerous=False)
    m.on_key(_key("escape"))
    m.dismiss.assert_called_once_with(False)


def test_enter_does_not_confirm():
    """Enter must NOT confirm — it would let an accidental Enter confirm a delete."""
    m = _modal(dangerous=True)
    m.on_key(_key("enter"))
    m.dismiss.assert_not_called()
