"""MCP sampling as an LLM backend for fabric_analyze / fabric_analyze_batch (ROADMAP 2.4).

Sampling is an MCP server-to-client request (``sampling/createMessage``): the server asks the
connected client to run a completion on the client's own model. A client offers it only if it
declares ``capabilities.sampling`` at initialize. yanger uses it to run a Fabric pattern prompt
(``<home>/.config/fabric/patterns/<name>/system.md``) over a transcript without a local Fabric
binary or API key.

Load-bearing guarantees pinned here:
- ``backend=auto`` uses the client's model when it declares sampling, else local Fabric, else a
  clear ``fabric_not_installed`` error naming both remedies. Never a silent empty success.
- A chosen backend that then fails reports that backend's error; there is no cross-fallback.
- The capability probe fails closed on SDKs/clients that predate sampling or send odd shapes.

Most tests drive the REAL low-level server through an in-memory client session, so the SDK's own
capability negotiation and request plumbing are exercised, not a mock of them.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import anyio
import pytest
import mcp.types as t
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from mcp.shared.memory import create_connected_server_and_client_session

import yanger.mcp_server as mod
from yanger.cache import PersistentCache
from yanger.core.transcript_fetcher import TranscriptFetcher
from yanger.mcp_server import YangerMCPServer, SAMPLING_MAX_TOKENS

from fakes import FakeYouTubeAPIClient

VID1 = "dQw4w9WgXcQ"
VID2 = "9bZkp7q19f0"
PROMPT = "# IDENTITY\nYou extract wisdom."
TRANSCRIPT = "hello transcript"


class Recorder:
    """A sampling callback that records each request and returns a scripted answer."""

    def __init__(self, answer=None):
        self.answer = answer or t.CreateMessageResult(
            role="assistant", content=t.TextContent(type="text", text="# Wisdom"),
            model="client-model", stopReason="endTurn")
        self.calls: list[t.CreateMessageRequestParams] = []

    async def __call__(self, context, params):
        self.calls.append(params)
        return self.answer


@pytest.fixture
def patterns(tmp_path, monkeypatch):
    d = tmp_path / "patterns"
    (d / "extract_wisdom").mkdir(parents=True)
    (d / "extract_wisdom" / "system.md").write_text(PROMPT)
    (d / "no_prompt").mkdir()  # a dir without system.md: not usable by sampling
    monkeypatch.setattr(mod, "_fabric_patterns_dir", lambda: d)
    return d


def _server(tmp_path, video_ids=(VID1,)) -> YangerMCPServer:
    """Faithful fake API + a real SQLite cache holding a SUCCESS transcript per video."""
    s = YangerMCPServer()
    s._authenticated = True
    s.api_client = FakeYouTubeAPIClient()
    s.cache = PersistentCache(cache_dir=tmp_path / "cache")
    for vid in video_ids:
        s.cache.cache_transcript(
            vid, TranscriptFetcher.compress_transcript(TRANSCRIPT), None, "en", False, "SUCCESS")
    return s


def _payload(result: t.CallToolResult) -> dict:
    return json.loads(result.content[0].text)


async def _call(server, cb, tool, args):
    async with create_connected_server_and_client_session(
        server.server, sampling_callback=cb
    ) as client:
        return _payload(await client.call_tool(tool, args))


def _fabric(installed: bool):
    return patch("shutil.which", return_value="/usr/bin/fabric" if installed else None)


def _with_ctx(session):
    return request_ctx.set(RequestContext(
        request_id=1, meta=None, session=session, lifespan_context=None))


# ---- (a) capability probe: pure, and never raises on old/odd shapes ---------------------

@pytest.mark.parametrize("caps, expected", [
    (None, False),
    (t.ClientCapabilities(), False),
    (SimpleNamespace(), False),                         # pre-sampling SDK: no field at all
    (SimpleNamespace(sampling={}), False),              # raw dict via extra=allow
    (t.ClientCapabilities(sampling=t.SamplingCapability()), True),
])
def test_supports_sampling(caps, expected):
    assert YangerMCPServer._supports_sampling(caps) is expected


# ---- (b) context lookup -----------------------------------------------------------------

def test_sampling_context_none_outside_a_request():
    assert YangerMCPServer()._sampling_context() is None


def test_sampling_context_none_when_session_lacks_create_message():
    caps = t.ClientCapabilities(sampling=t.SamplingCapability())
    session = SimpleNamespace(client_params=SimpleNamespace(capabilities=caps))
    token = _with_ctx(session)
    try:
        assert YangerMCPServer()._sampling_context() is None
    finally:
        request_ctx.reset(token)


# ---- (c) no local Fabric: the client's model runs the pattern ---------------------------

async def test_no_fabric_uses_client_sampling(tmp_path, patterns):
    cb = Recorder()
    with _fabric(False):
        out = await _call(_server(tmp_path), cb, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom", "model": "opus"})
    assert out["backend"] == "sampling"
    assert out["result"] == "# Wisdom"
    assert out["sampled_model"] == "client-model"
    assert out["stop_reason"] == "endTurn"
    assert out["transcript_language"] == "en"
    (p,) = cb.calls
    assert p.systemPrompt == PROMPT
    assert p.messages[0].content.text == TRANSCRIPT
    assert p.maxTokens == SAMPLING_MAX_TOKENS
    assert p.modelPreferences.hints[0].name == "opus"


async def test_no_model_sends_no_model_preferences(tmp_path, patterns):
    cb = Recorder()
    with _fabric(False):
        await _call(_server(tmp_path), cb, "fabric_analyze",
                    {"video_id": VID1, "pattern": "extract_wisdom"})
    assert cb.calls[0].modelPreferences is None


# ---- (d) precedence under auto -----------------------------------------------------------

async def test_auto_prefers_sampling_even_with_fabric_installed(tmp_path, patterns):
    cb = Recorder()
    with _fabric(True), patch("subprocess.run") as run:
        out = await _call(_server(tmp_path), cb, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom"})
    assert out["backend"] == "sampling"
    run.assert_not_called()
    assert len(cb.calls) == 1


async def test_auto_falls_back_to_local_fabric_when_client_lacks_sampling(tmp_path, patterns):
    """Keeps the local-Fabric path: a client without sampling runs the subprocess as before."""
    with _fabric(True), patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="# Local", stderr="")
        out = await _call(_server(tmp_path), None, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom"})
    assert out["backend"] == "fabric"
    assert out["result"] == "# Local"
    assert run.call_args.args[0] == ["fabric", "--pattern", "extract_wisdom"]


# ---- (e) explicit backends ------------------------------------------------------------------

async def test_backend_fabric_forces_local_even_with_sampling_client(tmp_path, patterns):
    cb = Recorder()
    with _fabric(True), patch("subprocess.run") as run:
        run.return_value = MagicMock(returncode=0, stdout="# Local", stderr="")
        out = await _call(_server(tmp_path), cb, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom", "backend": "fabric"})
    assert out["backend"] == "fabric"
    assert cb.calls == []


async def test_backend_sampling_with_fabric_installed_skips_subprocess(tmp_path, patterns):
    cb = Recorder()
    with _fabric(True), patch("subprocess.run") as run:
        out = await _call(_server(tmp_path), cb, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom", "backend": "sampling"})
    assert out["backend"] == "sampling"
    run.assert_not_called()


async def test_unknown_backend_is_invalid(tmp_path, patterns):
    """Called directly: over MCP the SDK's inputSchema enum check rejects it before the handler."""
    with _fabric(True):
        out = await _server(tmp_path)._fabric_analyze(
            {"video_id": VID1, "pattern": "extract_wisdom", "backend": "gpt"})
    assert out["error"] == "invalid_arguments"


# ---- (f, g) clear messages when nothing can run ----------------------------------------

async def test_no_fabric_and_no_sampling_names_both_remedies(tmp_path, patterns):
    with _fabric(False):
        out = await _call(_server(tmp_path), None, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom"})
    assert out["error"] == "fabric_not_installed"
    assert "sampling" in out["message"]
    assert "install_instructions" in out
    assert "result" not in out


async def test_backend_sampling_with_non_sampling_client_is_unsupported(tmp_path, patterns):
    with _fabric(True), patch("subprocess.run") as run:
        out = await _call(_server(tmp_path), None, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom", "backend": "sampling"})
    assert out["error"] == "sampling_unsupported"
    run.assert_not_called()


# ---- (h) pattern loading fails closed ----------------------------------------------------

@pytest.mark.parametrize("pattern, code", [
    ("missing_pattern", "pattern_not_found"),
    ("no_prompt", "pattern_not_found"),       # dir exists, system.md does not
    ("../etc", "invalid_arguments"),          # path traversal
])
async def test_bad_pattern_refuses_without_sampling(tmp_path, patterns, pattern, code):
    cb = Recorder()
    with _fabric(False):
        out = await _call(_server(tmp_path), cb, "fabric_analyze",
                          {"video_id": VID1, "pattern": pattern})
    assert out["error"] == code
    assert cb.calls == []


async def test_pattern_not_found_names_where_it_looked(tmp_path, patterns):
    with _fabric(False):
        out = await _call(_server(tmp_path), Recorder(), "fabric_analyze",
                          {"video_id": VID1, "pattern": "missing_pattern"})
    assert str(patterns / "missing_pattern" / "system.md") in out["message"]


# ---- (i) client errors and non-text answers are errors, never empty success ---------------

@pytest.mark.parametrize("answer", [
    t.ErrorData(code=-1, message="User rejected sampling request"),
    t.CreateMessageResult(role="assistant", content=t.ImageContent(
        type="image", data="aGk=", mimeType="image/png"), model="m", stopReason="endTurn"),
])
async def test_client_error_or_non_text_is_sampling_error(tmp_path, patterns, answer):
    with _fabric(False):
        out = await _call(_server(tmp_path), Recorder(answer), "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom"})
    assert out["error"] == "sampling_error"
    assert "result" not in out


async def test_sampling_failure_does_not_fall_back_to_fabric(tmp_path, patterns):
    """A chosen backend that fails reports its own error: no silent cross-fallback."""
    cb = Recorder(t.ErrorData(code=-1, message="declined"))
    with _fabric(True), patch("subprocess.run") as run:
        out = await _call(_server(tmp_path), cb, "fabric_analyze",
                          {"video_id": VID1, "pattern": "extract_wisdom"})
    assert out["error"] == "sampling_error"
    run.assert_not_called()


# ---- (j) an unanswered sampling request times out -------------------------------------------

async def test_unanswered_sampling_times_out(tmp_path, patterns, monkeypatch):
    """Stub session rather than the in-memory client: that client runs its callbacks inline in
    its receive loop, so a never-answering callback would also block delivery of the reply."""
    monkeypatch.setattr(mod, "SAMPLING_TIMEOUT_SECONDS", 0.2)

    async def create_message(*a, **k):
        await anyio.sleep(30)

    session = SimpleNamespace(
        client_params=SimpleNamespace(capabilities=t.ClientCapabilities(
            sampling=t.SamplingCapability())),
        create_message=create_message,
    )
    server = _server(tmp_path)
    token = _with_ctx(session)
    try:
        with _fabric(False), anyio.fail_after(10):
            out = await server._fabric_analyze({"video_id": VID1, "pattern": "extract_wisdom"})
    finally:
        request_ctx.reset(token)
    assert out["error"] == "timeout"
    assert "result" not in out


# ---- (k) batch: the request context reaches the gather tasks --------------------------------

@pytest.mark.parametrize("skip_errors", [True, False])
async def test_batch_uses_sampling_for_every_video(tmp_path, patterns, skip_errors):
    cb = Recorder()
    with _fabric(False):
        out = await _call(_server(tmp_path, (VID1, VID2)), cb, "fabric_analyze_batch", {
            "pattern": "extract_wisdom", "video_ids": [VID1, VID2], "skip_errors": skip_errors})
    assert out["backend"] == "sampling"
    assert out["successful_count"] == 2
    assert len(cb.calls) == 2
    for r in out["results"]:
        assert r["sampled_model"] == "client-model"
        assert r["stop_reason"] == "endTurn"


async def test_batch_bad_pattern_fails_before_playlist_work(tmp_path, patterns):
    """Under sampling the pattern is checked once, before auth and any quota-costing fetch."""
    server = _server(tmp_path)
    touched = []
    server._ensure_auth = lambda: touched.append("auth")
    real_items = server.api_client.get_playlist_items
    server.api_client.get_playlist_items = lambda *a, **k: touched.append("items") or real_items(*a, **k)
    with _fabric(False):
        out = await _call(server, Recorder(), "fabric_analyze_batch",
                          {"pattern": "missing_pattern", "playlist_id": "PL_nope"})
    assert out["error"] == "pattern_not_found"
    assert touched == []


async def test_batch_no_backend_is_fabric_not_installed(tmp_path, patterns):
    with _fabric(False):
        out = await _call(_server(tmp_path), None, "fabric_analyze_batch",
                          {"pattern": "extract_wisdom", "video_ids": [VID1]})
    assert out["error"] == "fabric_not_installed"
    assert "sampling" in out["message"]


# ---- (l) pattern discovery without the binary ------------------------------------------------

async def test_list_patterns_without_binary_reads_filesystem(tmp_path, patterns):
    with _fabric(False):
        out = await _call(_server(tmp_path), Recorder(), "list_fabric_patterns", {})
    assert out["source"] == "filesystem"
    assert out["patterns"] == ["extract_wisdom"]  # no_prompt has no system.md
    assert "note" not in out


async def test_list_patterns_without_any_backend_says_so(tmp_path, patterns):
    with _fabric(False):
        out = await _call(_server(tmp_path), None, "list_fabric_patterns", {})
    assert out["patterns"] == ["extract_wisdom"]
    assert "sampling" in out["note"]


# ---- tool schema ------------------------------------------------------------------------------

async def test_tools_advertise_backend_param(tmp_path):
    async with create_connected_server_and_client_session(_server(tmp_path).server) as client:
        tools = {x.name: x for x in (await client.list_tools()).tools}
    for name in ("fabric_analyze", "fabric_analyze_batch"):
        prop = tools[name].inputSchema["properties"]["backend"]
        assert prop["enum"] == ["auto", "fabric", "sampling"]
        assert "sampling" in tools[name].description
