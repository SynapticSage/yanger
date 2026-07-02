"""Tier 0.7 — refresh-all is documented under the reachable `gR` key, not the
undeliverable Ctrl+Shift+R chord.

The actual key dispatch (a `gR` branch in app.on_key's `_pending_g` block) is a simple
elif; end-to-end key simulation needs a Textual pilot harness (Tier-1 #2) not yet present,
so this pins the help/registry contract that users and the help overlay read.
"""

from yanger.keybindings import registry


def test_refresh_all_documented_as_gR():
    assert "gR" in registry.keybindings
    assert "refresh" in registry.keybindings["gR"].description.lower()


def test_undeliverable_chord_removed_from_help():
    assert "ctrl+shift+r" not in registry.keybindings
