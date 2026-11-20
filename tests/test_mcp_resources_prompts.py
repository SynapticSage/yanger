"""MCP resources + prompts (ROADMAP 2.3, the deferred Phase 4 of docs/MCP_SERVER_PLAN.md).

A *resource* is data the client can browse or attach (resources/list, resources/templates/list,
resources/read); a *prompt* is a template its user picks (prompts/list, prompts/get). yanger
exposes its playlists, cached transcripts and (opt-in) custom-command names as resources, and
Fabric patterns as prompts.

Load-bearing guarantee pinned here: the surface is CACHE-ONLY. No resource read or prompt fetch
authenticates, calls the YouTube API, or fetches a transcript; an autouse guard makes any of
those an AssertionError. What is not cached is reported as such, naming the tool that fetches it.

Tests drive the REAL low-level server through an in-memory client session (as
test_mcp_sampling.py does), with a real SQLite cache in tmp_path.
"""

import json
import os

import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import AnyUrl

import yanger.mcp_server as mod
from yanger.cache import PersistentCache
from yanger.config.settings import Settings, MCPSettings
from yanger.core.transcript_fetcher import TranscriptFetcher
from yanger.mcp_server import YangerMCPServer, DEFAULT_TRANSCRIPT_MAX_CHARS
from yanger.models import Playlist, Video

VID1 = "dQw4w9WgXcQ"
VID2 = "9bZkp7q19f0"
VID_NA = "abcdefghijk"   # cached as NOT_AVAILABLE
PROMPT = "# IDENTITY\nYou extract wisdom."
TRANSCRIPT = "hello transcript"


def _boom(*a, **k):
    raise AssertionError("resources/prompts must never authenticate, call the API, or fetch")


@pytest.fixture
def patterns(tmp_path, monkeypatch):
    d = tmp_path / "patterns"
    (d / "extract_wisdom").mkdir(parents=True)
    (d / "extract_wisdom" / "system.md").write_text(PROMPT)
    (d / "no_prompt").mkdir()                       # no system.md: not a usable prompt
    (d / "empty_prompt").mkdir()
    (d / "empty_prompt" / "system.md").write_text("")  # empty: get_prompt would reject it
    (d / "blank_prompt").mkdir()
    (d / "blank_prompt" / "system.md").write_text("  \n\t\n")  # whitespace only: rejected too
    (d / "latin1_prompt").mkdir()
    (d / "latin1_prompt" / "system.md").write_bytes(b"caf\xe9")  # not UTF-8: rejected too
    (d / "bad name").mkdir()
    (d / "bad name" / "system.md").write_text(PROMPT)  # name fails the pattern charset
    monkeypatch.setattr(mod, "_fabric_patterns_dir", lambda: d)
    return d


@pytest.fixture(autouse=True)
def no_quota(monkeypatch):
    """Every test proves: no auth, no YouTube API, no transcript fetch."""
    monkeypatch.setattr(YangerMCPServer, "_ensure_auth", _boom)
    monkeypatch.setattr(mod, "fetch_and_cache_transcript", _boom)
    for k in list(os.environ):
        if k.startswith("YANGER_CMD_"):
            monkeypatch.delenv(k)


@pytest.fixture
def s(tmp_path, patterns):
    """A server with default settings (no file I/O) and a seeded real cache."""
    srv = YangerMCPServer()
    srv._settings = Settings()
    srv.api_client = None
    srv._authenticated = False
    srv.cache = PersistentCache(cache_dir=tmp_path / "cache")
    srv.cache.set_playlists([
        Playlist(id="PL_a", title="Alpha", item_count=2),
        Playlist(id="PL_b", title="Beta", item_count=5),   # videos never fetched
    ])
    srv.cache.set_videos("PL_a", [
        Video(id=VID1, playlist_item_id="pi1", title="First", channel_title="Ch", position=0),
        Video(id=VID2, playlist_item_id="pi2", title="Second", channel_title="Ch", position=1),
    ])
    srv.virtual_id = srv.cache.import_virtual_playlist(
        "Watch history", [{"video_id": VID2, "title": "Watched", "channel": "Ch2"}])
    srv.cache.cache_transcript(
        VID1, TranscriptFetcher.compress_transcript(TRANSCRIPT), None, "en", False, "SUCCESS")
    srv.cache.cache_transcript(VID_NA, None, None, None, False, "NOT_AVAILABLE")
    return srv


def _session(srv):
    return create_connected_server_and_client_session(srv.server)


async def _read(srv, uri):
    async with _session(srv) as c:
        res = await c.read_resource(AnyUrl(uri))
    (content,) = res.contents
    return content


async def _error(call):
    """Run ``call(client)`` and return the McpError's message. Caught inside the session: once
    it leaves the session's task group it is wrapped in an ExceptionGroup."""
    async with _session(call.srv) as c:
        try:
            await call(c)
        except McpError as e:
            return str(e)
    pytest.fail("expected an McpError")


def _reading(srv, uri):
    async def call(c):
        await c.read_resource(AnyUrl(uri))
    call.srv = srv
    return call


async def _read_json(srv, uri):
    return json.loads((await _read(srv, uri)).text)


async def _uris(srv):
    async with _session(srv) as c:
        return {str(r.uri) for r in (await c.list_resources()).resources}


def _enable_commands(srv, commands):
    srv._settings = Settings(mcp=MCPSettings(allow_custom_commands=True), commands=commands)


# ---- (a) capabilities -------------------------------------------------------------------

async def test_capabilities_advertise_resources_and_prompts(s):
    async with _session(s) as c:
        caps = c.get_server_capabilities()
    assert caps.resources is not None
    assert caps.prompts is not None
    assert caps.tools is not None


# ---- (b) list_resources -----------------------------------------------------------------

async def test_list_resources_cached_playlists_and_transcript_index(s):
    uris = await _uris(s)
    assert {"yanger://playlists", "yanger://playlists/PL_a", "yanger://playlists/PL_b",
            f"yanger://playlists/virtual_{s.virtual_id}", "yanger://transcripts"} <= uris
    assert "yanger://commands" not in uris
    # Transcripts are not listed one by one (thousands); the template covers them.
    assert not any(u.startswith("yanger://transcripts/") for u in uris)


async def test_list_resources_with_empty_cache_still_lists_indexes(tmp_path, patterns):
    srv = YangerMCPServer()
    srv._settings = Settings()
    srv.cache = PersistentCache(cache_dir=tmp_path / "empty")
    assert await _uris(srv) == {"yanger://playlists", "yanger://transcripts"}


async def test_list_resources_survives_broken_settings(s, monkeypatch):
    """A config that fails to load drops only yanger://commands, never the whole listing."""
    s._settings = None
    monkeypatch.setattr(mod, "load_settings", lambda: (_ for _ in ()).throw(ValueError("bad")))
    uris = await _uris(s)
    assert "yanger://playlists" in uris and "yanger://commands" not in uris


# ---- (c) yanger://playlists ---------------------------------------------------------------

async def test_read_playlists_from_cache(s):
    out = await _read_json(s, "yanger://playlists")
    assert out["cached"] is True
    by_id = {p["id"]: p for p in out["playlists"]}
    assert by_id["PL_a"]["title"] == "Alpha"
    assert by_id["PL_a"]["video_count"] == 2
    assert by_id["PL_a"]["is_virtual"] is False
    v = by_id[f"virtual_{s.virtual_id}"]
    assert v["is_virtual"] is True and v["title"] == "Watch history"
    assert out["count"] == len(out["playlists"]) == 3


async def test_read_playlists_uncached_names_the_tool(tmp_path, patterns):
    srv = YangerMCPServer()
    srv._settings = Settings()
    srv.cache = PersistentCache(cache_dir=tmp_path / "empty")
    out = await _read_json(srv, "yanger://playlists")
    assert out["cached"] is False
    assert out["playlists"] == []
    assert "list_playlists" in out["note"]


async def test_lazy_cache_is_built_without_cleanup(tmp_path, patterns, monkeypatch):
    """With no cache yet, the read builds one lazily (never via _ensure_auth) and without the
    startup cleanup DELETE, so a resource read stays read-only."""
    made = []

    def fake_cache(**kw):
        made.append(kw)
        return PersistentCache(cache_dir=tmp_path / "lazy", **kw)

    monkeypatch.setattr(mod, "PersistentCache", fake_cache)
    srv = YangerMCPServer()
    srv._settings = Settings()
    out = await _read_json(srv, "yanger://playlists")
    assert out["cached"] is False
    assert made == [{"auto_cleanup": False}]
    # Kept apart from self.cache, so a later tool path still builds its cleanup-on-start cache.
    assert srv.cache is None


async def test_commands_gate_and_registry_load_off_the_event_loop(s, monkeypatch):
    """load_settings (YAML) and load_command_registry are file I/O: run them in a worker thread,
    as the rest of the resource path does."""
    import threading
    loop_thread = threading.get_ident()
    seen = []
    real_gate, real_load = YangerMCPServer._commands_exposed, mod.load_command_registry

    def gate(self):
        seen.append(("gate", threading.get_ident()))
        return real_gate(self)

    def load(settings):
        seen.append(("load", threading.get_ident()))
        return real_load(settings)

    monkeypatch.setattr(YangerMCPServer, "_commands_exposed", gate)
    monkeypatch.setattr(mod, "load_command_registry", load)
    _enable_commands(s, {"dl": "yt-dlp {url}"})
    assert "yanger://commands" in await _uris(s)
    await _read_json(s, "yanger://commands")
    assert {k for k, _ in seen} == {"gate", "load"}
    assert all(t != loop_thread for _, t in seen)


# ---- (d) yanger://playlists/{id} ----------------------------------------------------------

async def test_read_playlist_videos(s):
    out = await _read_json(s, "yanger://playlists/PL_a")
    assert out["cached"] is True
    assert [v["video_id"] for v in out["videos"]] == [VID1, VID2]
    assert out["videos"][0]["title"] == "First"
    assert out["videos"][0]["playlist_item_id"] == "pi1"
    assert out["count"] == 2


async def test_read_playlist_videos_uncached_names_the_tool(s):
    out = await _read_json(s, "yanger://playlists/PL_b")
    assert out["cached"] is False
    assert out["videos"] == []
    assert "list_videos" in out["note"]


async def test_read_virtual_playlist_uses_the_uuid_the_cache_stores(s):
    """import_virtual_playlist returns a bare uuid; the listing publishes virtual_<uuid> (the TUI
    convention) and the read strips the prefix before get_virtual_videos."""
    out = await _read_json(s, f"yanger://playlists/virtual_{s.virtual_id}")
    assert out["cached"] is True
    assert out["is_virtual"] is True
    assert [v["video_id"] for v in out["videos"]] == [VID2]
    assert out["videos"][0]["channel_title"] == "Ch2"


async def test_read_unknown_virtual_playlist_errors(s):
    assert "virtual playlist" in await _error(_reading(s, "yanger://playlists/virtual_nope"))


# ---- (e) yanger://transcripts (index) -------------------------------------------------------

async def test_transcript_index_lists_success_only(s):
    out = await _read_json(s, "yanger://transcripts")
    ids = [t["video_id"] for t in out["transcripts"]]
    assert ids == [VID1]
    assert out["transcripts"][0]["uri"] == f"yanger://transcripts/{VID1}"
    assert out["transcripts"][0]["language"] == "en"
    assert out["count"] == 1 and out["truncated"] is False


async def test_transcript_index_is_capped(s, monkeypatch):
    s.cache.cache_transcript(
        VID2, TranscriptFetcher.compress_transcript("x"), None, "en", False, "SUCCESS")
    monkeypatch.setattr(mod, "TRANSCRIPT_INDEX_MAX", 1)
    out = await _read_json(s, "yanger://transcripts")
    assert out["count"] == 1 and out["truncated"] is True


# ---- (f) yanger://transcripts/{video_id} ----------------------------------------------------

async def test_read_cached_transcript(s):
    content = await _read(s, f"yanger://transcripts/{VID1}")
    assert content.text == TRANSCRIPT
    assert content.mimeType == "text/plain"


@pytest.mark.parametrize("vid, match", [
    (VID2, "get_transcript"),     # not cached
    (VID_NA, "get_transcript"),   # cached as NOT_AVAILABLE
    ("bad-id", "Invalid"),        # malformed
])
async def test_read_transcript_errors(s, vid, match):
    assert match in await _error(_reading(s, f"yanger://transcripts/{vid}"))


async def test_read_long_transcript_is_truncated_with_a_resource_note(s):
    long = "a" * (DEFAULT_TRANSCRIPT_MAX_CHARS + 50)
    s.cache.cache_transcript(
        VID2, TranscriptFetcher.compress_transcript(long), None, "en", False, "SUCCESS")
    text = (await _read(s, f"yanger://transcripts/{VID2}")).text
    assert text.startswith("a" * DEFAULT_TRANSCRIPT_MAX_CHARS)
    assert "truncated 50 chars" in text
    assert "get_transcript" in text      # names the tool that has max_chars ...
    assert "pass a higher max_chars" not in text  # ... not a parameter resources lack


# ---- (g) yanger://commands (opt-in) ---------------------------------------------------------

async def test_commands_hidden_and_unreadable_when_opt_in_off(s):
    s._settings = Settings(commands={"dl": "yt-dlp {url}"})
    assert "yanger://commands" not in await _uris(s)
    assert "allow_custom_commands" in await _error(_reading(s, "yanger://commands"))


async def test_commands_listed_and_read_when_opt_in_on_without_templates(s):
    _enable_commands(s, {
        "dl": "yt-dlp {url}",
        "batch": {"run": "echo {ids}", "mode": "batch", "confirm": True},
    })
    assert "yanger://commands" in await _uris(s)
    out = await _read_json(s, "yanger://commands")
    assert out["commands"] == [
        {"name": "batch", "mode": "batch", "confirm": True},
        {"name": "dl", "mode": "per-video", "confirm": False},
    ]
    # Templates may hold secrets and the opt-in was consent to RUN commands, not to show them.
    assert "yt-dlp" not in json.dumps(out) and "echo" not in json.dumps(out)


# ---- (h) bad URIs ---------------------------------------------------------------------------

@pytest.mark.parametrize("uri", [
    "file:///etc/passwd",
    "yanger://secrets",
    "yanger://playlists/PL_a/extra",
    "yanger://transcripts/a/b",
    "yanger://commands/dl",
])
async def test_unknown_uri_errors(s, uri):
    assert "Unknown resource URI" in await _error(_reading(s, uri))


# ---- (i) templates ---------------------------------------------------------------------------

async def test_resource_templates(s):
    async with _session(s) as c:
        res = await c.list_resource_templates()
    assert {t.uriTemplate: t.mimeType for t in res.resourceTemplates} == {
        "yanger://playlists/{playlist_id}": "application/json",
        "yanger://transcripts/{video_id}": "text/plain",
    }


# ---- (j) list_prompts ------------------------------------------------------------------------

async def test_list_prompts_lists_usable_patterns_only(s):
    async with _session(s) as c:
        prompts = (await c.list_prompts()).prompts
    assert [p.name for p in prompts] == ["extract_wisdom"]
    (arg,) = prompts[0].arguments
    assert arg.name == "video_id" and arg.required is False


async def test_list_prompts_skips_an_unreadable_pattern(s, patterns, monkeypatch):
    """One pattern whose system.md raises (e.g. EACCES) is skipped, not fatal to the listing."""
    (patterns / "locked").mkdir()
    (patterns / "locked" / "system.md").write_text(PROMPT)
    real = mod.Path.read_text

    def read_text(self, *a, **k):
        if self.parent.name == "locked":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(mod.Path, "read_text", read_text)
    async with _session(s) as c:
        assert [p.name for p in (await c.list_prompts()).prompts] == ["extract_wisdom"]


@pytest.mark.parametrize("name", ["blank_prompt", "latin1_prompt"])
async def test_get_prompt_rejects_what_the_listing_skips(s, name):
    async def call(c):
        await c.get_prompt(name, None)
    call.srv = s
    assert "No usable pattern" in await _error(call)


async def test_list_prompts_missing_dir_is_empty(s, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_fabric_patterns_dir", lambda: tmp_path / "nowhere")
    async with _session(s) as c:
        assert (await c.list_prompts()).prompts == []


# ---- (k) get_prompt --------------------------------------------------------------------------

async def _get_prompt(srv, name, arguments=None):
    async with _session(srv) as c:
        return await c.get_prompt(name, arguments)


@pytest.mark.parametrize("arguments", [None, {}, {"video_id": ""}, {"video_id": "  "}])
async def test_get_prompt_without_video(s, arguments):
    res = await _get_prompt(s, "extract_wisdom", arguments)
    (msg,) = res.messages
    assert msg.role == "user"
    assert msg.content.text == PROMPT


async def test_get_prompt_with_video_appends_full_cached_transcript(s):
    """Like fabric_analyze (max_chars 0), the prompt carries the WHOLE transcript."""
    long = "b" * (DEFAULT_TRANSCRIPT_MAX_CHARS + 50)
    s.cache.cache_transcript(
        VID2, TranscriptFetcher.compress_transcript(long), None, "en", False, "SUCCESS")
    res = await _get_prompt(s, "extract_wisdom", {"video_id": VID2})
    (msg,) = res.messages
    assert msg.content.text == PROMPT + "\n\n" + long


@pytest.mark.parametrize("name, arguments, match", [
    ("extract_wisdom", {"video_id": VID_NA}, "get_transcript"),   # not cached (NOT_AVAILABLE)
    ("extract_wisdom", {"video_id": "bad-id"}, "Invalid"),
    ("../x", None, "Invalid pattern"),
    ("no_such_pattern", None, "No usable pattern"),
    ("empty_prompt", None, "No usable pattern"),
])
async def test_get_prompt_errors(s, name, arguments, match):
    async def call(c):
        await c.get_prompt(name, arguments)
    call.srv = s
    assert match in await _error(call)


# ---- (l) old-SDK fallback --------------------------------------------------------------------

async def test_read_falls_back_to_bare_text_without_read_resource_contents(s, monkeypatch):
    """The bare-str return carries no mime type, so the SDK labels even JSON as text/plain."""
    monkeypatch.setattr(mod, "ReadResourceContents", None)
    content = await _read(s, "yanger://playlists")
    assert content.mimeType == "text/plain"
    assert json.loads(content.text)["cached"] is True
