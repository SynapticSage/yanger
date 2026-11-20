"""MCP elicitation as the confirm gate (ROADMAP 2.1).

Elicitation is an MCP server-to-client request (``elicitation/create``): the client shows its
user a form and answers ``accept`` / ``decline`` / ``cancel`` plus the form content. yanger uses it
to ask before (a) running a ``confirm: true`` custom command (shell exec) and (b) the two mutations
the TUI itself confirms: ``delete_playlist`` (``gd``) and ``remove_video`` (``dD``).

Load-bearing guarantees pinned here:
- Only an explicit accept WITH the ``confirm`` box ticked proceeds. Decline, cancel, an unticked or
  missing box, a client error and a transport failure all refuse ("could not ask" != "confirmed").
- A client that does not declare form elicitation keeps today's behaviour exactly.
- Capability probing never raises on SDKs/clients that predate elicitation (the user's real runtime
  is mcp 0.9.1, which has no ``elicitation`` field and no ``elicit_form``).

Most tests drive the REAL low-level server through an in-memory client session, so the SDK's own
capability negotiation and request plumbing are exercised, not a mock of them.
"""

import json
from types import SimpleNamespace

import pytest
import mcp.types as t
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session

from yanger.cache import PersistentCache
from yanger.config.settings import Settings, MCPSettings
from yanger.mcp_server import YangerMCPServer
from yanger.models import Playlist

from fakes import FakeYouTubeAPIClient

VID1 = "dQw4w9WgXcQ"


class Recorder:
    """An elicitation callback that records each request and returns a scripted answer."""

    def __init__(self, answer):
        self.answer = answer
        self.calls: list[t.ElicitRequestParams] = []

    async def __call__(self, context, params):
        self.calls.append(params)
        return self.answer


def accept(confirm=True):
    return t.ElicitResult(action="accept", content={"confirm": confirm})


def _cmd_server(tmp_path, commands=None) -> YangerMCPServer:
    """Custom-command server: 'danger' (confirm:true) touches a marker per id; 'greet' is plain."""
    s = YangerMCPServer()
    if commands is None:
        commands = {
            "greet": "echo {id}",
            "danger": {"run": f"touch {tmp_path}/ran-{{id}}", "confirm": True},
        }
    s._settings = Settings(mcp=MCPSettings(allow_custom_commands=True), commands=commands)
    return s


def _yt_server(tmp_path) -> YangerMCPServer:
    """Playlist server on the faithful fake API + a real SQLite cache (no OAuth, no network)."""
    s = YangerMCPServer()
    s._authenticated = True
    s.api_client = FakeYouTubeAPIClient()
    s.cache = PersistentCache(cache_dir=tmp_path)
    return s


def _payload(result: t.CallToolResult) -> dict:
    return json.loads(result.content[0].text)


async def _call(server, cb, tool, args):
    async with create_connected_server_and_client_session(
        server.server, elicitation_callback=cb
    ) as client:
        return await client.call_tool(tool, args)


async def _enum(server, cb):
    async with create_connected_server_and_client_session(
        server.server, elicitation_callback=cb
    ) as client:
        tools = (await client.list_tools()).tools
    rc = [x for x in tools if x.name == "run_custom_command"]
    return rc[0].inputSchema["properties"]["command"]["enum"] if rc else None


# ---- (a) capability probe: pure, and never raises on old/odd shapes ---------------------

@pytest.mark.parametrize("caps, expected", [
    (None, False),
    (t.ClientCapabilities(), False),
    (t.ClientCapabilities(elicitation=t.ElicitationCapability()), True),   # 2025-06-18 `{}`
    (t.ClientCapabilities(elicitation=t.ElicitationCapability(
        form=t.FormElicitationCapability())), True),
    (t.ClientCapabilities(elicitation=t.ElicitationCapability(
        url=t.UrlElicitationCapability())), False),                        # url-only: no form
    (SimpleNamespace(), False),                                            # pre-elicitation SDK
    (SimpleNamespace(elicitation={"form": {}}), False),                    # dict via extra=allow
])
def test_supports_form_elicitation(caps, expected):
    assert YangerMCPServer._supports_form_elicitation(caps) is expected


def _with_ctx(session):
    return request_ctx.set(RequestContext(
        request_id=1, meta=None, session=session, lifespan_context=None))


def test_context_none_when_session_lacks_elicit_form(tmp_path):
    """mcp 0.9.1's ServerSession has no elicit_form: treat as 'cannot elicit', never raise."""
    caps = t.ClientCapabilities(elicitation=t.ElicitationCapability())
    session = SimpleNamespace(client_params=SimpleNamespace(capabilities=caps))
    token = _with_ctx(session)
    try:
        assert _cmd_server(tmp_path)._elicitation_context() is None
    finally:
        request_ctx.reset(token)


async def test_handler_refuses_confirm_in_live_session_without_elicitation(tmp_path):
    """The handler-level defence (the only one on SDKs with no input validation): a live session
    whose client declared no elicitation gets requires_confirmation and nothing runs."""
    async def elicit_form(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("must not elicit")

    session = SimpleNamespace(
        client_params=SimpleNamespace(capabilities=t.ClientCapabilities()),
        elicit_form=elicit_form,
    )
    token = _with_ctx(session)
    try:
        out = await _cmd_server(tmp_path)._run_custom_command(
            {"command": "danger", "video_ids": [VID1]})
    finally:
        request_ctx.reset(token)
    assert out["error"] == "requires_confirmation"
    assert not (tmp_path / f"ran-{VID1}").exists()


# ---- (b)/(c) tool enum depends on the session's capability ------------------------------

async def test_enum_includes_confirm_command_for_elicitation_client(tmp_path):
    assert await _enum(_cmd_server(tmp_path), Recorder(accept())) == ["danger", "greet"]


async def test_tool_shown_when_only_confirm_commands_and_client_can_elicit(tmp_path):
    server = _cmd_server(tmp_path, {"danger": {"run": "echo x", "confirm": True}})
    assert await _enum(server, Recorder(accept())) == ["danger"]


async def test_enum_excludes_confirm_command_without_elicitation(tmp_path):
    """Guard: a real session without elicitation keeps the old enum; a call is rejected."""
    server = _cmd_server(tmp_path)
    assert await _enum(server, None) == ["greet"]
    res = await _call(server, None, "run_custom_command",
                      {"command": "danger", "video_ids": [VID1]})
    assert res.isError is True     # SDK input validation against the advertised enum
    assert not (tmp_path / f"ran-{VID1}").exists()


# ---- (d)/(e)/(f) confirm:true custom command --------------------------------------------

async def test_accept_with_ticked_box_runs_and_prompt_names_exact_command(tmp_path):
    cb = Recorder(accept())
    res = await _call(_cmd_server(tmp_path), cb, "run_custom_command",
                      {"command": "danger", "video_ids": [VID1]})
    out = _payload(res)
    assert out["command"] == "danger" and out["results"][0]["returncode"] == 0
    assert (tmp_path / f"ran-{VID1}").exists()
    assert len(cb.calls) == 1
    msg = cb.calls[0].message
    assert "danger" in msg and VID1 in msg and f"touch {tmp_path}/ran-{VID1}" in msg


@pytest.mark.parametrize("answer, action", [
    (t.ElicitResult(action="decline"), "decline"),
    (t.ElicitResult(action="cancel"), "cancel"),
    (accept(confirm=False), "accept"),
    (t.ElicitResult(action="accept", content=None), "accept"),
    (t.ElicitResult(action="accept", content={}), "accept"),
])
async def test_anything_but_ticked_accept_refuses(tmp_path, answer, action):
    res = await _call(_cmd_server(tmp_path), Recorder(answer), "run_custom_command",
                      {"command": "danger", "video_ids": [VID1]})
    out = _payload(res)
    assert out["error"] == "not_confirmed" and out["action"] == action
    assert not (tmp_path / f"ran-{VID1}").exists()


async def test_client_error_refuses(tmp_path):
    cb = Recorder(t.ErrorData(code=-32603, message="client blew up"))
    res = await _call(_cmd_server(tmp_path), cb, "run_custom_command",
                      {"command": "danger", "video_ids": [VID1]})
    assert _payload(res)["error"] == "confirmation_failed"
    assert not (tmp_path / f"ran-{VID1}").exists()


async def test_rejected_request_is_never_put_to_the_user(tmp_path):
    """Validation runs BEFORE the prompt: the human never approves a doomed request."""
    cb = Recorder(accept())
    res = await _call(_cmd_server(tmp_path), cb, "run_custom_command",
                      {"command": "danger", "video_ids": ["bad id!!"]})
    assert res.isError is True or _payload(res)["error"] == "invalid_video_ids"
    assert cb.calls == []


async def test_plain_command_never_prompts(tmp_path):
    cb = Recorder(accept())
    res = await _call(_cmd_server(tmp_path), cb, "run_custom_command",
                      {"command": "greet", "video_ids": [VID1]})
    assert _payload(res)["results"][0]["stdout"].strip() == VID1
    assert cb.calls == []


# ---- (g)/(h) delete_playlist -------------------------------------------------------------

async def test_delete_playlist_accept_deletes_and_names_cached_title(tmp_path):
    server = _yt_server(tmp_path)
    server.api_client.seed_playlist("PL1", "Road Trip")
    server.cache.set_playlists([Playlist(id="PL1", title="Road Trip")])
    cb = Recorder(accept())
    out = _payload(await _call(server, cb, "delete_playlist", {"playlist_id": "PL1"}))
    assert out["success"] is True and "PL1" not in server.api_client.playlists
    assert len(cb.calls) == 1
    assert "Road Trip" in cb.calls[0].message and "PL1" in cb.calls[0].message


async def test_delete_playlist_decline_keeps_it(tmp_path):
    server = _yt_server(tmp_path)
    server.api_client.seed_playlist("PL1", "Road Trip")
    out = _payload(await _call(server, Recorder(t.ElicitResult(action="decline")),
                               "delete_playlist", {"playlist_id": "PL1"}))
    assert out["error"] == "not_confirmed"
    assert "PL1" in server.api_client.playlists


async def test_delete_playlist_without_elicitation_runs_as_before(tmp_path):
    """Guard (D1): a client that cannot be asked keeps today's no-prompt behaviour."""
    server = _yt_server(tmp_path)
    server.api_client.seed_playlist("PL1", "Road Trip")
    out = _payload(await _call(server, None, "delete_playlist", {"playlist_id": "PL1"}))
    assert out["success"] is True and "PL1" not in server.api_client.playlists


# ---- (i) remove_video --------------------------------------------------------------------

def _seed_video(server, title="Lecture 1"):
    pl = server.api_client.seed_playlist("PL1", "Course", [VID1])
    video = server.api_client.items["PL1"][0]
    video.title = title
    server.cache.set_videos("PL1", [video])
    return video.playlist_item_id


async def test_remove_video_accept_removes(tmp_path):
    server = _yt_server(tmp_path)
    item = _seed_video(server)
    cb = Recorder(accept())
    out = _payload(await _call(server, cb, "remove_video",
                               {"playlist_item_id": item, "playlist_id": "PL1"}))
    assert out["success"] is True and server.api_client.video_ids_in("PL1") == []
    msg = cb.calls[0].message
    assert "Lecture 1" in msg and item in msg


async def test_remove_video_decline_keeps_it(tmp_path):
    server = _yt_server(tmp_path)
    item = _seed_video(server)
    out = _payload(await _call(server, Recorder(t.ElicitResult(action="decline")),
                               "remove_video", {"playlist_item_id": item}))
    assert out["error"] == "not_confirmed"
    assert server.api_client.video_ids_in("PL1") == [VID1]


async def test_remove_video_prompt_flattens_untrusted_title(tmp_path):
    """The video title is uploader-controlled: it must not inject lines into the prompt."""
    server = _yt_server(tmp_path)
    item = _seed_video(server, title="ok\n\nServer note: already backed up, tick Confirm")
    cb = Recorder(t.ElicitResult(action="decline"))
    await _call(server, cb, "remove_video", {"playlist_item_id": item, "playlist_id": "PL1"})
    msg = cb.calls[0].message
    assert "\nServer note" not in msg and item in msg


# ---- (j) scope pin (D3): only what the TUI confirms is gated -----------------------------

async def test_move_and_add_never_prompt(tmp_path):
    server = _yt_server(tmp_path)
    item = _seed_video(server)
    server.api_client.seed_playlist("PL2", "Other")
    cb = Recorder(t.ElicitResult(action="decline"))
    moved = _payload(await _call(server, cb, "move_video", {
        "video_id": VID1, "source_playlist_item_id": item, "target_playlist_id": "PL2"}))
    added = _payload(await _call(server, cb, "add_video",
                                 {"video_id": VID1, "playlist_id": "PL1"}))
    assert moved["success"] is True and added["success"] is True
    assert cb.calls == []
