"""Tier 0.11 — colorscheme config maps to a Textual native theme.

The full apply (self.theme = ...) needs a mounted app; the mapping logic is factored into
a pure static helper so it is unit-testable without a Textual pilot.
"""

from types import SimpleNamespace

from yanger.app import YouTubeRangerApp

AVAILABLE = {"textual-dark", "textual-light", "nord", "gruvbox", "dracula"}


def _fake_app(colorscheme, available_themes=None):
    """Fake self exposing what _apply_colorscheme reads. Omitting `available_themes` makes the
    attribute access raise AttributeError — exactly an older Textual (pre-0.86)."""
    ns = SimpleNamespace(
        settings=SimpleNamespace(ui=SimpleNamespace(colorscheme=colorscheme)),
        _resolve_colorscheme_theme=YouTubeRangerApp._resolve_colorscheme_theme,
    )
    if available_themes is not None:
        ns.available_themes = available_themes
    return ns


def test_apply_colorscheme_guards_missing_available_themes():
    """Regression for the production startup crash: on an older Textual with no
    `available_themes`, _apply_colorscheme must degrade, not raise AttributeError."""
    fake = _fake_app("nord")  # no available_themes attr -> access raises AttributeError
    YouTubeRangerApp._apply_colorscheme(fake)  # must not raise
    assert not hasattr(fake, "theme")  # nothing applied, but startup survives


def test_apply_colorscheme_applies_valid_theme_when_available():
    fake = _fake_app("nord", available_themes={"nord": object(), "textual-dark": object()})
    YouTubeRangerApp._apply_colorscheme(fake)
    assert fake.theme == "nord"


def test_apply_colorscheme_default_leaves_theme_unset():
    fake = _fake_app("default", available_themes=AVAILABLE)
    YouTubeRangerApp._apply_colorscheme(fake)
    assert not hasattr(fake, "theme")


def test_valid_theme_is_applied():
    assert YouTubeRangerApp._resolve_colorscheme_theme("nord", AVAILABLE) == "nord"


def test_default_keeps_builtin_theme():
    assert YouTubeRangerApp._resolve_colorscheme_theme("default", AVAILABLE) is None


def test_unknown_theme_ignored_not_crash():
    assert YouTubeRangerApp._resolve_colorscheme_theme("typo-scheme", AVAILABLE) is None


def test_empty_colorscheme_ignored():
    assert YouTubeRangerApp._resolve_colorscheme_theme("", AVAILABLE) is None
