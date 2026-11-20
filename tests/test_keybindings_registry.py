"""Tier 0.7 — refresh-all is documented under the reachable `gR` key, not the
undeliverable Ctrl+Shift+R chord.

The actual key dispatch (a `gR` sequence the app's on_key feeds to the registry's
KeySequenceMatcher) is pinned end to end in tests/test_keybindings_yaml.py; this file pins the
help/registry contract that users and the help overlay read, and the pure matcher.
"""

from yanger.keybindings import (
    BINDING, SEQUENCE, KeySequenceMatcher, MatchResult, display_keys, registry,
)


def test_refresh_all_documented_as_gR():
    assert "gR" in registry.keybindings
    assert "refresh" in registry.keybindings["gR"].description.lower()


def test_undeliverable_chord_removed_from_help():
    assert "ctrl+shift+r" not in registry.keybindings


# --- Roadmap 3.5: action ids and the sequence matcher ------------------------------------

# The help display keys before 3.5 introduced action ids (pins the help overlay's output).
_PRE_3_5_DISPLAY_KEYS = {
    "q", "?", ":", "ctrl+r", "gR", "ctrl+q", "h/←", "j/↓", "k/↑", "l/→", "gg", "G", "enter",
    "space", "V", "v", "uv", "uV", "dd", "dD", "yy", "pp", "u", "U", "/", "n", "N", "escape",
    "gn", "gd", "cw", "o", "r", "B", "gt", "gT", "ge",
}


def test_display_keys_unchanged_plus_paging():
    """Help keeps every key it showed; pageup/pagedown (handled, never listed) now appear."""
    assert set(registry.keybindings) == _PRE_3_5_DISPLAY_KEYS | {"pageup", "pagedown"}


def test_every_binding_has_a_unique_action_id():
    actions = [b.action for b in registry.keybindings.values()]
    assert all(actions)
    assert len(actions) == len(set(actions))
    for action in actions:
        assert registry.get_action(action).action == action


def test_display_rule():
    assert display_keys(["g R"]) == "gR"
    assert display_keys(["h", "left"]) == "h/←"
    assert display_keys(["ctrl+r"]) == "ctrl+r"


def test_binding_kind_actions_are_single_keys():
    for b in registry.keybindings.values():
        assert b.kind in (BINDING, SEQUENCE)
        if b.kind == BINDING:
            assert all(len(seq.split()) == 1 for seq in b.keys), b.action


def test_app_dispatches_every_sequence_action():
    """Every SEQUENCE action has exactly one handler in app.py, so a registry entry can never be
    bound to a key that does nothing (and a handler can't drift out of the registry)."""
    from yanger.app import _APP_KEY_ACTIONS, _MILLER_KEY_ACTIONS, YouTubeRangerApp

    seq_actions = {b.action for b in registry.keybindings.values() if b.kind == SEQUENCE}
    assert not set(_APP_KEY_ACTIONS) & set(_MILLER_KEY_ACTIONS)
    assert seq_actions == set(_APP_KEY_ACTIONS) | set(_MILLER_KEY_ACTIONS)
    for name, _args in _APP_KEY_ACTIONS.values():
        assert callable(getattr(YouTubeRangerApp, name)), name


def _always(_action):
    return True


def _matcher(*pairs):
    return KeySequenceMatcher([(tuple(seq.split()), a) for seq, a in pairs])


def test_matcher_single_key_match_and_none():
    m = _matcher(("j", "down"), ("g n", "new"))
    r = m.feed("j", _always)
    assert (r.kind, r.action) == (MatchResult.MATCH, "down")
    assert m.feed("x", _always).kind == MatchResult.NONE
    assert not m.pending


def test_matcher_prefix_then_match():
    m = _matcher(("g n", "new"), ("g g", "top"))
    r = m.feed("g", _always)
    assert r.kind == MatchResult.PENDING and r.action is None
    assert sorted(r.children) == [("g", "top"), ("n", "new")]
    r = m.feed("n", _always)
    assert (r.kind, r.action) == (MatchResult.MATCH, "new")
    assert not m.pending


def test_matcher_mismatch_at_plain_prefix_fires_nothing():
    m = _matcher(("g n", "new"))
    m.feed("g", _always)
    r = m.feed("x", _always)
    assert (r.kind, r.action) == (MatchResult.MISMATCH, None)
    assert not m.pending


def test_matcher_terminal_prefix_falls_back_and_times_out():
    m = _matcher(("u", "undo"), ("u v", "unmark"))
    r = m.feed("u", _always)
    assert (r.kind, r.action) == (MatchResult.PENDING, "undo")
    r = m.feed("j", _always)                       # u then j: undo, j consumed
    assert (r.kind, r.action) == (MatchResult.MISMATCH, "undo")
    m.feed("u", _always)
    assert m.timeout(_always) == "undo"            # u then nothing
    assert not m.pending
    assert m.timeout(_always) is None              # nothing pending: nothing fires


def test_matcher_enabled_filter():
    """A prefix arms only while an enabled action lies below it (d outside the video column)."""
    m = _matcher(("d d", "cut"), ("d D", "delete"))
    assert m.feed("d", lambda a: False).kind == MatchResult.NONE
    assert m.feed("d", lambda a: a == "cut").children == [("d", "cut")]


def test_default_sequences_resolve():
    m = KeySequenceMatcher(registry.sequence_bindings())
    for b in registry.keybindings.values():
        if b.kind != SEQUENCE:
            continue
        for seq in b.keys:
            *head, last = seq.split()
            for tok in head:
                assert m.feed(tok, _always).kind == MatchResult.PENDING
            r = m.feed(last, _always)
            if r.kind == MatchResult.PENDING:          # terminal prefix (bare u)
                assert r.action == b.action
                m.reset()
            else:
                assert (r.kind, r.action) == (MatchResult.MATCH, b.action)
