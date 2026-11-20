"""Tier 0.7 — refresh-all is documented under the reachable `gR` key, not the
undeliverable Ctrl+Shift+R chord.

The actual key dispatch (a `gR` sequence the app's on_key feeds to the registry's
KeySequenceMatcher) is pinned end to end in tests/test_keybindings_yaml.py; this file pins the
help/registry contract that users and the help overlay read, and the pure matcher.
"""

import re
from pathlib import Path

import pytest
import yaml

from yanger.keybindings import (
    BINDING, SEQUENCE, KeySequenceMatcher, MatchResult, display_keys,
    load_keybinding_overrides, registry,
)


@pytest.fixture(autouse=True)
def _restore_registry_defaults():
    """apply_overrides mutates the process-global registry; reset it after every test."""
    yield
    registry.apply_overrides({})


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


# --- Roadmap 3.5: user overrides (apply_overrides, the YAML loader, the template) ----------

def _keys(action):
    return registry.get_action(action).keys


def test_defaults_are_conflict_free():
    assert registry.apply_overrides({}) == []


def test_override_replaces_defaults_and_empty_restores():
    assert registry.apply_overrides({"new_playlist": ["x"]}) == []
    assert _keys("new_playlist") == ["x"]
    assert registry.keybindings["x"].description == "Create new playlist"
    assert "gn" not in registry.keybindings
    assert registry.apply_overrides({}) == []
    assert _keys("new_playlist") == ["g n"]
    assert "gn" in registry.keybindings and "x" not in registry.keybindings


def test_override_accepts_alternatives_and_named_keys():
    assert registry.apply_overrides({"move_down": ["j", "down", "ctrl+n"]}) == []
    assert _keys("move_down") == ["j", "down", "ctrl+n"]
    assert registry.keybindings["j/↓/ctrl+n"].action == "move_down"


@pytest.mark.parametrize("overrides, needle", [
    ({"no_such_action": ["x"]}, "no_such_action"),
    ({"cut": []}, "cut"),
    ({"cut": [1]}, "cut"),
    ({"cut": "x"}, "cut"),                   # apply_overrides takes lists; the loader normalizes
    ({"cut": [""]}, "cut"),
    ({"quit": ["Q Q"]}, "single key"),       # BINDINGS-kind keys cannot be sequences
    ({"new_playlist": ["tab"]}, "reserved"),
])
def test_bad_entries_warn_and_keep_defaults(overrides, needle):
    before = {b.action: list(b.keys) for b in registry.keybindings.values()}
    warnings = registry.apply_overrides(overrides)
    assert len(warnings) == 1 and needle in warnings[0]
    assert {b.action: list(b.keys) for b in registry.keybindings.values()} == before


@pytest.mark.parametrize("token", ["dd", "gR", "shift+g", "foo", "ctrl+", "\t"])
def test_undeliverable_tokens_are_refused(token):
    """Fail closed: a token no keypress can produce must not replace a working default."""
    warnings = registry.apply_overrides({"cut": [token]})
    assert len(warnings) == 1
    assert _keys("cut") == ["d d"]


def test_glued_sequence_gets_a_spacing_hint():
    [warning] = registry.apply_overrides({"cut": ["dd"]})
    assert '"d d"' in warning


def test_textual_key_aliases_canonicalize_to_the_character():
    assert registry.apply_overrides({"help": ["question_mark"]}) == []
    assert _keys("help") == ["?"]
    warnings = registry.apply_overrides({"new_playlist": ["slash"]})  # "/" is search's key
    assert warnings and "search" in warnings[0]
    assert _keys("new_playlist") == ["g n"]


def test_duplicate_sequence_is_reverted_naming_both_actions():
    [warning] = registry.apply_overrides({"new_playlist": ["d d"]})
    assert "new_playlist" in warning and "cut" in warning
    assert _keys("new_playlist") == ["g n"]
    assert _keys("cut") == ["d d"]


def test_binding_key_clashing_with_a_later_sequence_token_is_reverted():
    """event.stop() in on_key does NOT stop App BINDINGS, so redo on 'w' would fire on the
    w of `c w` as well as rename: any token of any sequence counts as a clash."""
    [warning] = registry.apply_overrides({"redo": ["w"]})
    assert "redo" in warning and "rename" in warning
    assert _keys("redo") == ["U"]


def test_binding_vs_binding_clash_is_reverted():
    warnings = registry.apply_overrides({"redo": ["r"]})
    assert warnings and "open_in_browser" in warnings[0]
    assert _keys("redo") == ["U"]


def test_conflict_reverts_cascade():
    """quit/new_playlist clash on x -> both revert -> new_playlist's default g n now clashes
    with cut's override -> cut reverts too."""
    warnings = registry.apply_overrides(
        {"new_playlist": ["x"], "quit": ["x"], "cut": ["g n"]})
    assert len(warnings) >= 2
    assert (_keys("new_playlist"), _keys("quit"), _keys("cut")) == (["g n"], ["q"], ["d d"])


def test_swap_is_accepted():
    assert registry.apply_overrides({"cut": ["y y"], "copy": ["d d"]}) == []
    assert (_keys("cut"), _keys("copy")) == (["y y"], ["d d"])


def test_binding_keymap_holds_only_overridden_binding_actions():
    assert registry.binding_keymap() == {}
    registry.apply_overrides({"quit": ["Q"], "new_playlist": ["x"]})
    assert registry.binding_keymap() == {"quit": "Q"}
    registry.apply_overrides({})
    assert registry.binding_keymap() == {}


def test_binding_overrides_refused_when_textual_cannot_rebind():
    [warning] = registry.apply_overrides({"quit": ["Q"]}, rebind_bindings=False)
    assert "quit" in warning
    assert _keys("quit") == ["q"]


def test_loader_missing_file(tmp_path):
    assert load_keybinding_overrides(tmp_path / "keybindings.yaml") == ({}, [])


def test_loader_empty_or_comment_only_file(tmp_path):
    p = tmp_path / "keybindings.yaml"
    p.write_text("# nothing here\n")
    assert load_keybinding_overrides(p) == ({}, [])


@pytest.mark.parametrize("text", ["cut: [d d\n", "- cut\n- copy\n", "just a string\n"])
def test_loader_parse_error_or_non_mapping_warns(tmp_path, text):
    p = tmp_path / "keybindings.yaml"
    p.write_text(text)
    overrides, warnings = load_keybinding_overrides(p)
    assert overrides == {} and len(warnings) == 1


def test_loader_normalizes_and_skips_bad_values(tmp_path):
    p = tmp_path / "keybindings.yaml"
    p.write_text('cut: "x x"\ncopy: ["y y", "c c"]\nquit: on\nselect: 1\n')
    overrides, warnings = load_keybinding_overrides(p)
    assert overrides == {"cut": ["x x"], "copy": ["y y", "c c"]}
    assert len(warnings) == 2 and "quit" in warnings[0] and "select" in warnings[1]


_TEMPLATE = Path(__file__).resolve().parent.parent / "config" / "keybindings.yaml"


def test_template_lists_every_action_with_its_defaults_and_loads_as_nothing():
    text = _TEMPLATE.read_text()
    assert yaml.safe_load(text) is None          # all commented out: changes nothing
    listed = {}
    for line in text.splitlines():
        m = re.match(r"^#\s*([a-z_]+):\s*([\"\[].*)$", line)
        if m:
            assert m.group(1) not in listed, f"{m.group(1)} listed twice"
            listed[m.group(1)] = yaml.safe_load(m.group(2))
    actions = {b.action: b.keys for b in registry.keybindings.values()}
    assert set(listed) == set(actions)
    for action, value in listed.items():
        assert (value if isinstance(value, list) else [value]) == actions[action], action
