"""Roadmap 3.5: key dispatch through the keybinding registry, and user overrides in YAML.

Two layers:
- Characterization (written against the pre-3.5 hand-written ``on_key`` branches, which they
  passed): the ranger-style sequences (gn, gR, cw, dd, dD, u*, gg, B) dispatch exactly as
  before once ``on_key`` goes through the registry's sequence matcher.
- Overrides: ``~/.config/yanger/keybindings.yaml`` rebinds actions by id, for both sequence
  keys (handled in ``on_key``) and Textual BINDINGS keys (handled via ``App.set_keymap``).

Handlers are spied by replacing the instance attribute with a plain recorder that has the real
method's name (no MagicMock), so a misspelled handler name fails instead of passing silently.
"""

import pytest
import pytest_asyncio

import re

from yanger.app import YouTubeRangerApp
from yanger.keybindings import BINDING, registry
from yanger.ui.help_overlay import HelpOverlay


def _composited_text(app) -> str:
    """The text actually painted (compositor output), as in test_pilot_harness.py."""
    strips = app.screen._compositor.render_strips()
    return "\n".join("".join(seg.text for seg in strip) for strip in strips)


@pytest.fixture(autouse=True)
def _restore_registry_defaults():
    """The registry is a process-global the app mutates on construction; reset it after."""
    yield
    registry.apply_overrides({})


def _offline(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # cache/config -> tmp, not the real home

    async def _offline_auth(self):
        self.offline_mode = True
        self.api_client = None

    monkeypatch.setattr(YouTubeRangerApp, "setup_authentication", _offline_auth)


def _write_user_keybindings(tmp_path, text: str) -> None:
    cfg = tmp_path / ".config" / "yanger"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "keybindings.yaml").write_text(text)


@pytest_asyncio.fixture
async def app_pilot(monkeypatch, tmp_path):
    _offline(monkeypatch, tmp_path)
    app = YouTubeRangerApp()
    async with app.run_test(size=(120, 60)) as pilot:
        await pilot.pause()
        yield app, pilot


def _spy(app, calls, *names, async_=True):
    """Replace app methods by recorders that log (name, args)."""
    for name in names:
        assert callable(getattr(app, name)), name  # the real method must exist

        def make(n):
            if async_:
                async def rec(*args):
                    calls.append((n, args))
            else:
                def rec(*args):
                    calls.append((n, args))
            return rec

        setattr(app, name, make(name))


def _spy_miller_keys(app, calls):
    """Wrap MillerView.handle_key: record the key, then call through.

    MillerView.handle_key(str) shadows Textual's Widget.handle_key(Key event), which Textual
    itself calls as a key bubbles through; only the app's str-key calls are recorded.
    """
    real = app.miller_view.handle_key

    async def handle_key(key):
        if isinstance(key, str):
            calls.append(("handle_key", (key,)))
        await real(key)

    app.miller_view.handle_key = handle_key


# --- Characterization: today's sequence dispatch ------------------------------------------

@pytest.mark.parametrize("keys, handler, is_async, args", [
    (("g", "n"), "action_new_playlist", False, ()),
    (("g", "R"), "action_refresh_all", True, ()),
    (("g", "d"), "handle_delete_playlist", True, ()),
    (("g", "t"), "fetch_transcript_for_current_video", True, ()),
    (("g", "T"), "toggle_auto_fetch_transcript", True, ()),
    (("g", "e"), "export_transcript", True, ()),
    (("c", "w"), "action_rename", False, ()),
    (("B",), "execute_bulkedit", True, ()),
])
async def test_app_level_sequences_dispatch(app_pilot, keys, handler, is_async, args):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, handler, async_=is_async)
    await pilot.press(*keys)
    await pilot.pause()
    assert calls == [(handler, args)]


@pytest.mark.parametrize("keys, expected", [
    (("d", "d"), [("execute_ranger_command", ("d",))]),
    (("y", "y"), [("execute_ranger_command", ("y",))]),
    (("p", "p"), [("execute_ranger_command", ("p",))]),
    (("d", "D"), [("handle_delete_videos", ())]),
])
async def test_video_column_double_keys(app_pilot, keys, expected):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, "execute_ranger_command", "handle_delete_videos")
    app.miller_view.focused_column = 1
    await pilot.pause()
    for key in keys:
        await pilot.press(key)
        await pilot.pause()
    assert calls == expected


async def test_bare_u_resolves_to_undo_after_timeout(app_pilot):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, "action_undo", async_=False)
    await pilot.press("u")
    await pilot.pause()
    assert calls == []          # still waiting for v/V
    await pilot.pause(0.6)
    assert calls == [("action_undo", ())]


async def test_u_then_other_key_undoes_and_consumes_the_key(app_pilot):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, "action_undo", async_=False)
    _spy_miller_keys(app, calls)
    await pilot.press("u", "j")
    await pilot.pause(0.6)      # past the timer: undo must not fire twice
    assert calls == [("action_undo", ())]  # j consumed: never reached the miller view


async def test_u_v_unmarks_without_undo(app_pilot):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, "action_undo", async_=False)
    vc = app.miller_view.video_column
    unmarked = []
    vc.unselect_all = lambda: unmarked.append(True)
    await pilot.press("u", "v")
    await pilot.pause(0.6)
    assert unmarked == [True]
    assert calls == []


async def test_g_then_unknown_key_fires_nothing(app_pilot):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, "action_refresh_all", "handle_delete_playlist",
         "fetch_transcript_for_current_video", "toggle_auto_fetch_transcript",
         "export_transcript")
    _spy(app, calls, "action_new_playlist", async_=False)
    _spy_miller_keys(app, calls)
    await pilot.press("g", "x")
    await pilot.pause()
    assert calls == []


async def test_g_g_jumps_to_top(app_pilot):
    app, pilot = app_pilot
    calls = []
    _spy_miller_keys(app, calls)
    await pilot.press("g", "g")
    await pilot.pause()
    assert calls == [("handle_key", ("g",))]


async def test_d_in_playlist_column_does_not_swallow_the_next_key(app_pilot):
    app, pilot = app_pilot
    calls = []
    _spy(app, calls, "execute_ranger_command", "handle_delete_videos")
    _spy_miller_keys(app, calls)
    assert app.miller_view.focused_column == 0
    await pilot.press("d", "j")
    await pilot.pause()
    assert calls[-1] == ("handle_key", ("j",))
    assert ("execute_ranger_command", ("d",)) not in calls


async def test_colon_opens_command_input_and_typing_renders(app_pilot):
    app, pilot = app_pilot
    await pilot.press("colon")
    await pilot.pause()
    iw = app.query_one("#command-input").input_widget
    assert iw.has_focus
    await pilot.press("g", "n")
    await pilot.pause()
    assert ":gn" in _composited_text(app)


# --- Overrides from ~/.config/yanger/keybindings.yaml --------------------------------------

def test_binding_actions_are_app_bindings_with_matching_ids():
    """BINDING-kind registry actions are the App.BINDINGS entries set_keymap rebinds by id."""
    by_id = {b.id: b for b in YouTubeRangerApp.BINDINGS}
    for b in registry.keybindings.values():
        if b.kind == BINDING:
            assert by_id[b.action].key == b.keys[0]
            assert by_id[b.action].action == b.action


def _user_pilot(text):
    """A pilot fixture whose ~/.config/yanger/keybindings.yaml holds ``text``; it also
    records every notify() as (message, severity)."""
    @pytest_asyncio.fixture
    async def fixture(monkeypatch, tmp_path):
        _offline(monkeypatch, tmp_path)
        _write_user_keybindings(tmp_path, text)
        app = YouTubeRangerApp()
        notes = []
        real_notify = app.notify

        def notify(message, *args, **kwargs):
            notes.append((message, kwargs.get("severity")))
            return real_notify(message, *args, **kwargs)

        app.notify = notify
        async with app.run_test(size=(120, 120)) as pilot:
            await pilot.pause()
            yield app, pilot, notes
    return fixture


x_new_playlist = _user_pilot('new_playlist: "x"\n')
big_q_quit = _user_pilot('quit: "Q"\n')
malformed = _user_pilot('new_playlist: [x\n')


async def test_sequence_override_rebinds_the_key(x_new_playlist):
    app, pilot, notes = x_new_playlist
    assert not [n for n in notes if n[1] == "warning"]
    calls = []
    _spy(app, calls, "action_new_playlist", async_=False)
    await pilot.press("g", "n")
    await pilot.pause()
    assert calls == []                      # the default is replaced, not added to
    await pilot.press("x")
    await pilot.pause()
    assert calls == [("action_new_playlist", ())]


async def test_help_overlay_renders_the_effective_key(x_new_playlist):
    app, pilot, _notes = x_new_playlist
    app.action_help()
    await pilot.pause()
    assert isinstance(app.screen, HelpOverlay)
    painted = _composited_text(app)
    assert re.search(r"\sx\s+Create new playlist", painted), painted
    assert not re.search(r"\sgn\s+Create new playlist", painted)


async def test_override_key_types_into_the_command_input(x_new_playlist):
    app, pilot, _notes = x_new_playlist
    calls = []
    _spy(app, calls, "action_new_playlist", async_=False)
    app.action_command_mode()
    await pilot.pause()
    await pilot.press("x")
    await pilot.pause()
    assert ":x" in _composited_text(app)
    assert calls == []


async def test_binding_override_goes_through_textual_keymap(big_q_quit):
    app, pilot, _notes = big_q_quit
    exits = []
    app.exit = lambda *a, **k: exits.append(a)
    await pilot.press("q")
    await pilot.pause()
    assert exits == []
    await pilot.press("Q")
    await pilot.pause()
    assert exits == [(0,)]


async def test_malformed_yaml_warns_and_keeps_defaults(malformed):
    app, pilot, notes = malformed
    assert any(sev == "warning" and "keybindings.yaml" in msg for msg, sev in notes), notes
    calls = []
    _spy(app, calls, "action_new_playlist", async_=False)
    await pilot.press("g", "n")
    await pilot.pause()
    assert calls == [("action_new_playlist", ())]
