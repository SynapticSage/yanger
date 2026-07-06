"""Tier 0.11 — colorscheme config maps to a Textual native theme.

The full apply (self.theme = ...) needs a mounted app; the mapping logic is factored into
a pure static helper so it is unit-testable without a Textual pilot.
"""

from yanger.app import YouTubeRangerApp

AVAILABLE = {"textual-dark", "textual-light", "nord", "gruvbox", "dracula"}


def test_valid_theme_is_applied():
    assert YouTubeRangerApp._resolve_colorscheme_theme("nord", AVAILABLE) == "nord"


def test_default_keeps_builtin_theme():
    assert YouTubeRangerApp._resolve_colorscheme_theme("default", AVAILABLE) is None


def test_unknown_theme_ignored_not_crash():
    assert YouTubeRangerApp._resolve_colorscheme_theme("typo-scheme", AVAILABLE) is None


def test_empty_colorscheme_ignored():
    assert YouTubeRangerApp._resolve_colorscheme_theme("", AVAILABLE) is None
