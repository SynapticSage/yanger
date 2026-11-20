"""Gated MCP ``run_custom_command`` tool + its config gate + the capturing runner.

Security-sensitive (LLM-driven shell execution), so these pin the load-bearing guarantees two
rounds of adversarial review flagged: the opt-in gate is actually wired through settings *merge*
(else it is silently dead — B1); the tool is hidden until enabled and can never break tool
discovery (B2); ``confirm: true`` commands are excluded from the enum AND refused unless the
client supports elicitation (S1; the elicitation path is in test_mcp_elicitation.py); the
fan-out is capped and errors rather than truncates; command output is captured with a timeout;
and — the CRITICAL one — every LLM-supplied video_id is validated to the YouTube charset BEFORE a
command is built, because ``shlex.quote`` is bypassable when a template embeds the placeholder in
quotes (``echo "got {id}"`` + ``$(...)`` executes).
"""

import mcp.types as t
import pytest

from yanger.config.settings import Settings, MCPSettings, load_settings
from yanger.core.custom_command import run_command_capture
from yanger.mcp_server import YangerMCPServer, CUSTOM_COMMAND_MAX_VIDEOS

VID1 = "dQw4w9WgXcQ"   # real, well-formed 11-char YouTube ids
VID2 = "abcdefghijk"


async def _list_tools(server):
    handler = server.server.request_handlers[t.ListToolsRequest]
    res = await handler(t.ListToolsRequest(method="tools/list"))
    return res.root.tools


def _server_with(commands, allow=True) -> YangerMCPServer:
    """A server whose settings are set directly (no file I/O)."""
    s = YangerMCPServer()
    s._settings = Settings(mcp=MCPSettings(allow_custom_commands=allow), commands=commands)
    return s


# ---- config gate wiring (B1: the four-touch-point silent-drop trap) --------------------

def test_user_config_flips_gate_through_merge(tmp_path, monkeypatch):
    """If 'mcp' isn't in the merge section list, this reads the default False forever and the
    entire feature is dead — exactly the trap the roadmap documents for `commands`."""
    monkeypatch.delenv("YANGER_MCP_ALLOW_CUSTOM_COMMANDS", raising=False)
    (tmp_path / "config.yaml").write_text("mcp:\n  allow_custom_commands: true\n")
    assert load_settings(config_dir=tmp_path).mcp.allow_custom_commands is True


def test_gate_defaults_off_without_config(tmp_path, monkeypatch):
    monkeypatch.delenv("YANGER_MCP_ALLOW_CUSTOM_COMMANDS", raising=False)
    assert load_settings(config_dir=tmp_path).mcp.allow_custom_commands is False


def test_env_override_toggles_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("YANGER_MCP_ALLOW_CUSTOM_COMMANDS", "1")
    assert load_settings(config_dir=tmp_path).mcp.allow_custom_commands is True
    monkeypatch.setenv("YANGER_MCP_ALLOW_CUSTOM_COMMANDS", "off")
    assert load_settings(config_dir=tmp_path).mcp.allow_custom_commands is False


# ---- list_tools gating -----------------------------------------------------------------

async def test_tool_hidden_when_gate_off():
    server = _server_with({"greet": "echo {id}"}, allow=False)
    assert "run_custom_command" not in [x.name for x in await _list_tools(server)]


async def test_tool_shown_when_gate_on_and_excludes_confirm():
    server = _server_with({"greet": "echo {id}", "danger": {"run": "echo x", "confirm": True}})
    tools = await _list_tools(server)
    rc = next(x for x in tools if x.name == "run_custom_command")
    # confirm:true command must NOT be advertised (can't be honored without a human).
    assert rc.inputSchema["properties"]["command"]["enum"] == ["greet"]


async def test_tool_hidden_when_only_confirm_commands():
    server = _server_with({"danger": {"run": "echo x", "confirm": True}})
    assert "run_custom_command" not in [x.name for x in await _list_tools(server)]


async def test_list_tools_survives_broken_gate_probe(monkeypatch):
    """B2: a failure probing the gate/registry must NOT wipe out the ~20 static tools."""
    server = _server_with({"greet": "echo {id}"})

    def _boom():
        raise RuntimeError("config blew up")

    monkeypatch.setattr(server, "_custom_command_tools", _boom)
    names = [x.name for x in await _list_tools(server)]
    assert "list_playlists" in names and len(names) >= 20   # discovery intact
    assert "run_custom_command" not in names                 # omitted, not crashed


# ---- handler refusals (structured error codes, not raised exceptions) ------------------

async def test_handler_refuses_when_gate_off():
    server = _server_with({"greet": "echo {id}"}, allow=False)
    out = await server._run_custom_command({"command": "greet", "video_ids": [VID1]})
    assert out["error"] == "disabled"


async def test_handler_unknown_command():
    server = _server_with({"greet": "echo {id}"})
    out = await server._run_custom_command({"command": "nope", "video_ids": [VID1]})
    assert out["error"] == "unknown_command" and "greet" in out["available"]


async def test_handler_refuses_confirm_command():
    server = _server_with({"danger": {"run": "echo x", "confirm": True}})
    out = await server._run_custom_command({"command": "danger", "video_ids": [VID1]})
    assert out["error"] == "requires_confirmation"


async def test_handler_rejects_too_many_videos():
    server = _server_with({"greet": "echo {id}"})
    ids = [VID1] * (CUSTOM_COMMAND_MAX_VIDEOS + 1)
    out = await server._run_custom_command({"command": "greet", "video_ids": ids})
    assert out["error"] == "too_many_videos"


async def test_handler_rejects_empty_video_ids():
    server = _server_with({"greet": "echo {id}"})
    out = await server._run_custom_command({"command": "greet", "video_ids": []})
    assert out["error"] == "invalid_video_ids"


async def test_handler_rejects_non_string_ids():
    """S2: a non-string id must be rejected (would blow up is_valid_video_id / the shell)."""
    server = _server_with({"greet": "echo {id}"})
    out = await server._run_custom_command({"command": "greet", "video_ids": [123]})
    assert out["error"] == "invalid_video_ids"


# ---- the CRITICAL injection defense (W1: the real test, not `echo {id}`) ---------------

async def test_command_injection_via_quoted_template_is_rejected(tmp_path):
    """Reviewer's PoC: a template that embeds {id} INSIDE quotes defeats shlex.quote, so a
    hostile id like $(...) would execute. The id-charset validation must reject it BEFORE any
    command is built — proven by (a) the error code and (b) the side-effect file never existing."""
    marker = tmp_path / "PWNED"
    server = _server_with({"notify": 'echo "got {id}"'})   # placeholder inside double quotes
    hostile = f"$(touch {marker})"
    out = await server._run_custom_command({"command": "notify", "video_ids": [hostile]})
    assert out["error"] == "invalid_video_ids"
    assert not marker.exists(), "command injection executed — id validation failed"


# ---- handler execution (echo, no external deps) ----------------------------------------

async def test_per_video_runs_once_each_and_captures_id():
    server = _server_with({"greet": "echo {id}"})
    out = await server._run_custom_command({"command": "greet", "video_ids": [VID1, VID2]})
    assert out["mode"] == "per-video"
    assert [r["stdout"].strip() for r in out["results"]] == [VID1, VID2]
    assert out["results"][0]["returncode"] == 0 and out["results"][0]["timed_out"] is False


async def test_batch_runs_once_with_all_ids():
    server = _server_with({"greetall": {"run": "echo {ids}", "mode": "batch"}})
    out = await server._run_custom_command({"command": "greetall", "video_ids": [VID1, VID2]})
    assert out["mode"] == "batch"
    assert len(out["results"]) == 1
    assert out["results"][0]["stdout"].strip() == f"{VID1} {VID2}"


async def test_dispatch_through_handle_tool_needs_no_auth(monkeypatch):
    """S3: exercise the real dispatch (_handle_tool -> handlers[...] -> shaping). run_custom_command
    is in no_auth_tools, so this must NOT trigger the OAuth path."""
    server = _server_with({"greet": "echo {id}"})
    monkeypatch.setattr(server, "_ensure_auth",
                        lambda: (_ for _ in ()).throw(AssertionError("auth must not run")))
    out = await server._handle_tool("run_custom_command", {"command": "greet", "video_ids": [VID1]})
    assert out["command"] == "greet" and out["results"][0]["stdout"].strip() == VID1


async def test_small_max_output_chars_truncates():
    """S4: truncated=True must propagate up when a stream exceeds max_output_chars."""
    server = _server_with({"greet": "echo {id}"})
    out = await server._run_custom_command(
        {"command": "greet", "video_ids": [VID1], "max_output_chars": 3}
    )
    assert out["truncated"] is True and out["results"][0]["truncated"] is True


async def test_junk_max_output_chars_falls_back_to_default():
    """S1: a non-int / 0 max_output_chars must not crash or mean 'unlimited'."""
    server = _server_with({"greet": "echo {id}"})
    out = await server._run_custom_command(
        {"command": "greet", "video_ids": [VID1], "max_output_chars": "lots"}
    )
    assert out["results"][0]["returncode"] == 0  # ran fine, junk ignored


# ---- capturing runner ------------------------------------------------------------------

def test_run_command_capture_basic():
    r = run_command_capture("echo hi", timeout=10)
    assert r.returncode == 0 and r.stdout.strip() == "hi" and r.timed_out is False


def test_run_command_capture_timeout():
    r = run_command_capture("sleep 5", timeout=0.3)
    assert r.timed_out is True and r.returncode == -1


def test_run_command_capture_non_utf8_does_not_raise():
    # errors='replace' keeps binary tool output from crashing the decode.
    r = run_command_capture(r"printf '\xff\xfe'", timeout=10)
    assert r.returncode == 0
