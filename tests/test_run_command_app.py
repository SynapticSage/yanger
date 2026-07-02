"""Headline slice 1b: the `:run` TUI wiring (app.py).

Full key-driven testing needs a Textual pilot (Tier-1 #2, not yet present), so we drive the
real coroutines against a lightweight fake `self` — the methods only touch a small, mockable
surface (settings, selection, notify, suspend, push_screen). This covers the decision logic:
registry resolution, marked-else-current, the large-selection confirm gate, and failure
reporting.
"""

import contextlib
from types import SimpleNamespace

import pytest

import yanger.app as appmod
from yanger.app import YouTubeRangerApp, RUN_CONFIRM_THRESHOLD
from yanger.core.custom_command import CommandSpec


def _video(vid):
    return SimpleNamespace(id=vid)


def _fake_app(commands=None, selection=None):
    """Fake `self` exposing only what handle_run_command / run_custom_command read."""
    app = SimpleNamespace()
    app.settings = SimpleNamespace(commands=commands or {})
    app.notes = []                                   # (msg, error)
    app._notify_status = lambda msg, error=False: app.notes.append((msg, error))
    app._marked_or_current_videos = lambda: list(selection or [])
    app.ran = []                                     # (spec, videos) actually run
    async def _run(spec, videos):
        app.ran.append((spec, list(videos)))
    app.run_custom_command = _run
    app.pushed = []                                  # modals pushed for confirm
    async def _push(modal):
        app.pushed.append(modal)
    app.push_screen = _push
    return app


# ----- handle_run_command decision branches ------------------------------------

async def test_no_commands_configured_notifies(monkeypatch):
    monkeypatch.delenv("YANGER_CMD_X", raising=False)
    app = _fake_app(commands={})
    await YouTubeRangerApp.handle_run_command(app, ["dl"])
    assert app.notes and "No custom commands configured" in app.notes[-1][0]
    assert app.notes[-1][1] is True


async def test_empty_args_shows_available():
    app = _fake_app(commands={"dl": "yt-dlp {url}"})
    await YouTubeRangerApp.handle_run_command(app, [])
    msg, err = app.notes[-1]
    assert "Usage: :run <name>" in msg and "dl" in msg and err is True


async def test_unknown_name_shows_available():
    app = _fake_app(commands={"dl": "yt-dlp {url}"})
    await YouTubeRangerApp.handle_run_command(app, ["nope"])
    assert "Unknown command 'nope'" in app.notes[-1][0]


async def test_no_video_selected():
    app = _fake_app(commands={"dl": "yt-dlp {url}"}, selection=[])
    await YouTubeRangerApp.handle_run_command(app, ["dl"])
    assert app.notes[-1][0] == "No video selected"


async def test_small_selection_runs_without_confirm():
    app = _fake_app(commands={"dl": "yt-dlp {url}"}, selection=[_video("a")])
    await YouTubeRangerApp.handle_run_command(app, ["dl"])
    assert len(app.ran) == 1
    spec, videos = app.ran[0]
    assert spec.name == "dl" and [v.id for v in videos] == ["a"]
    assert app.pushed == []


async def test_large_selection_confirms_before_running():
    sel = [_video(str(i)) for i in range(RUN_CONFIRM_THRESHOLD + 1)]
    app = _fake_app(commands={"dl": "yt-dlp {url}"}, selection=sel)
    await YouTubeRangerApp.handle_run_command(app, ["dl"])
    assert app.ran == []                      # not run yet — awaiting confirm
    assert len(app.pushed) == 1               # modal shown
    assert app.pushed[0].action == "run_custom_command"
    assert app._pending_run_spec.name == "dl"
    assert len(app._pending_run_videos) == RUN_CONFIRM_THRESHOLD + 1


async def test_name_is_case_insensitive():
    app = _fake_app(commands={"dl": "yt-dlp {url}"}, selection=[_video("a")])
    await YouTubeRangerApp.handle_run_command(app, ["DL"])
    assert len(app.ran) == 1 and app.ran[0][0].name == "dl"


# ----- run_custom_command execution + reporting --------------------------------

def _runner_app(exit_codes):
    """Fake self for run_custom_command; suspend()/refresh are no-ops."""
    app = SimpleNamespace()
    app.notes = []
    app._notify_status = lambda msg, error=False: app.notes.append((msg, error))
    app.suspend = lambda: contextlib.nullcontext()
    app.refresh = lambda: None
    return app


async def test_run_custom_command_all_success(monkeypatch):
    monkeypatch.setattr(appmod, "run_command", lambda cmd: 0)
    app = _runner_app([0, 0])
    spec = CommandSpec(name="dl", template="yt-dlp {url}")
    await YouTubeRangerApp.run_custom_command(app, spec, [_video("a"), _video("b")])
    assert "finished on 2 video(s)" in app.notes[-1][0] and app.notes[-1][1] is False


async def test_run_custom_command_reports_failures(monkeypatch):
    monkeypatch.setattr(appmod, "run_command", lambda cmd: 1)
    app = _runner_app([1])
    spec = CommandSpec(name="dl", template="yt-dlp {url}")
    await YouTubeRangerApp.run_custom_command(app, spec, [_video("a")])
    assert "1 of 1 exited non-zero" in app.notes[-1][0] and app.notes[-1][1] is True


async def test_run_custom_command_builds_quoted_command(monkeypatch):
    seen = []
    monkeypatch.setattr(appmod, "run_command", lambda cmd: seen.append(cmd) or 0)
    app = _runner_app([0])
    spec = CommandSpec(name="dl", template="yt-dlp {url}")
    await YouTubeRangerApp.run_custom_command(app, spec, [_video("abc123")])
    assert "https://www.youtube.com/watch?v=abc123" in seen[0]


# ----- on_confirmation_result routing (confirm -> run) --------------------------

def test_confirm_result_runs_pending_command():
    app = SimpleNamespace()
    calls = []
    app.call_later = lambda fn, *a: calls.append((fn, a))
    app.notify = lambda *a, **k: None
    runner = object()
    app.run_custom_command = runner
    spec = CommandSpec(name="dl", template="yt-dlp {url}")
    videos = [_video("a")]
    app._pending_run_spec = spec
    app._pending_run_videos = videos

    msg = SimpleNamespace(confirmed=True, action="run_custom_command")
    YouTubeRangerApp.on_confirmation_result(app, msg)

    assert calls == [(runner, (spec, videos))]
    assert app._pending_run_spec is None and app._pending_run_videos is None


def test_cancelled_result_does_not_run():
    app = SimpleNamespace()
    calls = []
    app.call_later = lambda fn, *a: calls.append((fn, a))
    app.notify = lambda *a, **k: None
    app._pending_run_spec = CommandSpec(name="dl", template="x")
    app._pending_run_videos = [_video("a")]

    msg = SimpleNamespace(confirmed=False, action="run_custom_command")
    YouTubeRangerApp.on_confirmation_result(app, msg)

    assert calls == []  # cancelled -> nothing runs


# ----- _marked_or_current_videos ------------------------------------------------

def _selection_app(marked, selected_index=0, videos=None):
    vc = SimpleNamespace(
        get_marked_videos=lambda: marked,
        selected_index=selected_index,
        videos=videos if videos is not None else [],
    )
    return SimpleNamespace(miller_view=SimpleNamespace(video_column=vc))


def test_marked_wins_when_present():
    app = _selection_app(marked=[_video("m1"), _video("m2")], videos=[_video("cur")])
    result = YouTubeRangerApp._marked_or_current_videos(app)
    assert [v.id for v in result] == ["m1", "m2"]


def test_falls_back_to_current_when_none_marked():
    app = _selection_app(marked=[], selected_index=1, videos=[_video("a"), _video("b")])
    result = YouTubeRangerApp._marked_or_current_videos(app)
    assert [v.id for v in result] == ["b"]


def test_empty_when_no_miller_view():
    app = SimpleNamespace(miller_view=None)
    assert YouTubeRangerApp._marked_or_current_videos(app) == []
