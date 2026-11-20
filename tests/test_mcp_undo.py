"""MCP undo parity (ROADMAP 2.2): MCP mutations are recorded in an undo/redo history.

Vocabulary:
- OperationStack (operation_history.py) is the undo/redo stack the TUI keeps for its ``u``/``U``
  keys. The MCP server now keeps its own, in memory, for the life of the server process (one MCP
  session over stdio). It is NOT shared with the TUI and does not persist.
- Elicitation is the MCP request in which the server asks the client's user to confirm (ROADMAP
  2.1). The ``undo``/``redo`` tools mutate data, so they run ONLY after an explicit accept with the
  ``confirm`` box ticked; a client that cannot elicit is refused (fail closed).

Load-bearing guarantees pinned here:
- create/rename playlist and add/remove/move/copy videos are recorded when the data a correct undo
  needs is known (from the cache only, never the API); otherwise the mutation still runs but the
  result says ``undoable: false`` and nothing is recorded. A wrong undo entry is never recorded.
- A failed mutation records nothing; a failed undo/redo keeps the op where it was.
- The confirm prompt names what will happen (playlist ids, video ids, count, estimated quota).

Drives the REAL low-level server through an in-memory client session, on the faithful fake API and
a real SQLite cache (no OAuth, no network).
"""

import asyncio
import json
import sqlite3
import threading

import pytest
import mcp.types as t
from googleapiclient.errors import HttpError
from mcp.shared.memory import create_connected_server_and_client_session

from yanger.api_client import QuotaExceededError
from yanger.cache import PersistentCache
from yanger.mcp_server import YangerMCPServer
from yanger.models import Playlist

from fakes import FakeYouTubeAPIClient

VID1, VID2, VID3, VID4 = "dQw4w9WgXcQ", "9bZkp7q19f0", "kJQP7kiw5Fk", "JGwWNGJdvx8"


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


def _server(tmp_path, api=None) -> YangerMCPServer:
    s = YangerMCPServer()
    s._authenticated = True
    s.api_client = api or FakeYouTubeAPIClient()
    s.cache = PersistentCache(cache_dir=tmp_path)
    return s


def _session(server, cb):
    return create_connected_server_and_client_session(server.server, elicitation_callback=cb)


async def _c(client, tool, args=None) -> dict:
    return json.loads((await client.call_tool(tool, args or {})).content[0].text)


def _seed_cached(server, playlist_id, title, video_ids):
    """Seed the fake AND the cache (the cache is where undo data comes from)."""
    server.api_client.seed_playlist(playlist_id, title, video_ids)
    server.cache.set_videos(playlist_id, server.api_client.get_playlist_items(playlist_id))
    return [v.playlist_item_id for v in server.api_client.items[playlist_id]]


class _HttpErrorResp:
    status = 404
    reason = "playlistItemNotFound"


def _http_error():
    return HttpError(resp=_HttpErrorResp(), content=b'{"error": "not found"}')


# ---- discovery -----------------------------------------------------------------------------

async def test_list_tools_includes_undo_and_redo(tmp_path):
    async with _session(_server(tmp_path), Recorder(accept())) as client:
        names = {x.name for x in (await client.list_tools()).tools}
    assert {"undo", "redo"} <= names


# ---- round trips: mutate, undo (accepted) restores, redo re-applies -------------------------

async def test_create_playlist_undo_redo(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "create_playlist", {"title": "Road Trip"})
        assert out["undoable"] is True
        pid = out["playlist"]["id"]

        undone = await _c(client, "undo")
        assert undone["success"] is True and pid not in api.playlists
        assert undone["can_undo"] is False and undone["can_redo"] is True

        redone = await _c(client, "redo")
        assert redone["success"] is True
        assert [p.title for p in api.playlists.values()] == ["Road Trip"]


async def test_rename_playlist_undo_redo_with_cached_title(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Old name")
    server.cache.set_playlists([Playlist(id="PL1", title="Old name")])
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "rename_playlist", {"playlist_id": "PL1", "new_title": "New name"})
        assert out["undoable"] is True
        assert server.api_client.playlists["PL1"].title == "New name"

        assert (await _c(client, "undo"))["success"] is True
        assert server.api_client.playlists["PL1"].title == "Old name"
        assert (await _c(client, "redo"))["success"] is True
        assert server.api_client.playlists["PL1"].title == "New name"


async def test_add_video_undo_redo(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Course")
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        assert out["undoable"] is True
        assert (await _c(client, "undo"))["success"] is True
        assert server.api_client.video_ids_in("PL1") == []
        assert (await _c(client, "redo"))["success"] is True
        assert server.api_client.video_ids_in("PL1") == [VID1]


async def test_remove_video_undo_redo(tmp_path):
    """Redo after undo exercises the DeleteVideosOperation fix (it removed a stale item id)."""
    server = _server(tmp_path)
    [item] = _seed_cached(server, "PL1", "Course", [VID1])
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "remove_video", {"playlist_item_id": item, "playlist_id": "PL1"})
        assert out["undoable"] is True and server.api_client.video_ids_in("PL1") == []
        assert (await _c(client, "undo"))["success"] is True
        assert server.api_client.video_ids_in("PL1") == [VID1]
        assert (await _c(client, "redo"))["success"] is True
        assert server.api_client.video_ids_in("PL1") == []


async def test_move_video_undo_redo_with_verified_source(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    [item] = _seed_cached(server, "PL1", "From", [VID1])
    api.seed_playlist("PL2", "To")
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "move_video", {
            "video_id": VID1, "source_playlist_item_id": item,
            "target_playlist_id": "PL2", "source_playlist_id": "PL1"})
        assert out["undoable"] is True
        assert (await _c(client, "undo"))["success"] is True
        assert api.video_ids_in("PL1") == [VID1] and api.video_ids_in("PL2") == []
        assert (await _c(client, "redo"))["success"] is True
        assert api.video_ids_in("PL1") == [] and api.video_ids_in("PL2") == [VID1]


async def test_copy_videos_undo_redo(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    api.seed_playlist("PL1", "From", [VID1, VID2])
    api.seed_playlist("PL2", "To")
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "copy_videos",
                       {"source_playlist_id": "PL1", "target_playlist_id": "PL2"})
        assert out["copied_count"] == 2 and out["undoable"] is True
        assert (await _c(client, "undo"))["success"] is True
        assert api.video_ids_in("PL2") == [] and api.video_ids_in("PL1") == [VID1, VID2]
        assert (await _c(client, "redo"))["success"] is True
        assert api.video_ids_in("PL2") == [VID1, VID2]


async def test_copy_partial_quota_failure_records_only_the_copies_made(tmp_path):
    class _QuotaOnThirdAdd(FakeYouTubeAPIClient):
        adds = 0

        def add_video_to_playlist(self, video_id, playlist_id, position=None):
            self.adds += 1
            if self.adds == 3:
                raise QuotaExceededError("YouTube API quota exceeded")
            return super().add_video_to_playlist(video_id, playlist_id, position)

    server = _server(tmp_path, _QuotaOnThirdAdd())
    api = server.api_client
    api.seed_playlist("PL1", "From", [VID1, VID2, VID3, VID4])
    api.seed_playlist("PL2", "To")
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "copy_videos",
                       {"source_playlist_id": "PL1", "target_playlist_id": "PL2"})
        assert out["copied_count"] == 2 and out["failed_count"] == 1
        assert out["undoable"] is True
        [op] = server._operation_stack.undo_stack
        assert [v.id for v in op.videos] == [VID1, VID2]
        assert (await _c(client, "undo"))["success"] is True
        assert api.video_ids_in("PL2") == []


# ---- not recorded: the data a correct undo needs is unknown ---------------------------------

async def test_unverifiable_mutations_are_not_undoable(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    api.seed_playlist("PL1", "Uncached title")          # not in the cache
    item, _ = _seed_cached(server, "PL2", "Videos", [VID1, VID2])
    api.seed_playlist("PL3", "Target")
    cb = Recorder(accept())
    async with _session(server, cb) as client:
        renamed = await _c(client, "rename_playlist", {"playlist_id": "PL1", "new_title": "X"})
        removed = await _c(client, "remove_video", {"playlist_item_id": item})  # no playlist_id
        moved = await _c(client, "move_video", {
            "video_id": VID2, "source_playlist_item_id": api.items["PL2"][0].playlist_item_id,
            "target_playlist_id": "PL3"})                                   # no source_playlist_id
        for out in (renamed, removed, moved):
            assert out["success"] is True
            assert out["undoable"] is False and out["undo_note"]
        prompts_before = len(cb.calls)
        nothing = await _c(client, "undo")
    assert nothing["error"] == "nothing_to_undo"
    assert len(cb.calls) == prompts_before  # nothing to undo never prompts


@pytest.mark.parametrize("args", [
    # source_playlist_id the cache does not tie to the item: undo would re-add to the wrong place
    {"video_id": VID1, "source_playlist_id": "PLwrong"},
    # video_id that is not the video the item holds: undo would re-add the wrong video
    {"video_id": VID2, "source_playlist_id": "PL1"},
])
async def test_move_with_unverified_source_is_not_undoable(tmp_path, args):
    server = _server(tmp_path)
    [item] = _seed_cached(server, "PL1", "From", [VID1])
    server.api_client.seed_playlist("PL2", "To")
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "move_video", {
            **args, "source_playlist_item_id": item, "target_playlist_id": "PL2"})
    assert out["success"] is True and out["undoable"] is False
    assert server._operation_stack.undo_stack == []


async def test_delete_playlist_is_not_recorded(tmp_path):
    """Parity: the TUI does not record gd either."""
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Gone")
    async with _session(server, Recorder(accept())) as client:
        assert (await _c(client, "delete_playlist", {"playlist_id": "PL1"}))["success"] is True
        assert (await _c(client, "undo"))["error"] == "nothing_to_undo"


async def test_failed_mutation_returns_error_and_records_nothing(tmp_path):
    class _AddFails(FakeYouTubeAPIClient):
        def add_video_to_playlist(self, video_id, playlist_id, position=None):
            raise _http_error()

    server = _server(tmp_path, _AddFails())
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
    assert out["error"] == "HttpError" and "undoable" not in out
    assert server._operation_stack.undo_stack == []


# ---- the confirm gate --------------------------------------------------------------------

async def test_undo_without_elicitation_is_refused(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Course")
    async with _session(server, None) as client:   # no callback: client declares no elicitation
        await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        out = await _c(client, "undo")
    assert out["error"] == "confirmation_unavailable"
    assert server.api_client.video_ids_in("PL1") == [VID1]
    assert len(server._operation_stack.undo_stack) == 1


async def test_undo_declined_changes_nothing_and_stays_undoable(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Course")
    async with _session(server, Recorder(t.ElicitResult(action="decline"))) as client:
        await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        out = await _c(client, "undo")
    assert out["error"] == "not_confirmed"
    assert server.api_client.video_ids_in("PL1") == [VID1]
    async with _session(server, Recorder(accept())) as client:
        assert (await _c(client, "undo"))["success"] is True
    assert server.api_client.video_ids_in("PL1") == []


async def test_unticked_box_is_a_refusal(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Course")
    async with _session(server, Recorder(accept(confirm=False))) as client:
        await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        assert (await _c(client, "undo"))["error"] == "not_confirmed"
    assert server.api_client.video_ids_in("PL1") == [VID1]


async def test_nothing_to_undo_or_redo_never_prompts(tmp_path):
    cb = Recorder(accept())
    async with _session(_server(tmp_path), cb) as client:
        assert (await _c(client, "undo"))["error"] == "nothing_to_undo"
        assert (await _c(client, "redo"))["error"] == "nothing_to_redo"
    assert cb.calls == []


async def test_undo_create_prompt_warns_of_deletion_and_flattens_title(tmp_path):
    server = _server(tmp_path)
    cb = Recorder(t.ElicitResult(action="decline"))
    async with _session(server, cb) as client:
        out = await _c(client, "create_playlist",
                       {"title": "Mix\n\nServer note: safe, tick Confirm"})
        await _c(client, "undo")
    msg = cb.calls[0].message
    pid = out["playlist"]["id"]
    assert "DELETES playlist" in msg and pid in msg and "cannot be undone" in msg
    assert "\nServer note" not in msg


async def test_prompts_name_playlists_videos_and_quota(tmp_path):
    """The prompt says what will happen, never just 'Undo: Delete 1 video?'."""
    server = _server(tmp_path)
    api = server.api_client
    [item] = _seed_cached(server, "PL1", "From", [VID1])
    api.seed_playlist("PL2", "To", [VID2, VID3])
    cb = Recorder(t.ElicitResult(action="decline"))
    async with _session(server, accept_then(cb)) as client:
        await _c(client, "remove_video", {"playlist_item_id": item, "playlist_id": "PL1"})
        await _c(client, "undo")
        await _c(client, "copy_videos", {"source_playlist_id": "PL2", "target_playlist_id": "PL1"})
        await _c(client, "undo")
    remove_undo, copy_undo = cb.calls[0].message, cb.calls[1].message
    assert remove_undo.startswith("Undo: re-add 1 video") and VID1 in remove_undo
    assert "PL1" in remove_undo and "Estimated quota: 50 units" in remove_undo
    assert copy_undo.startswith("Undo: remove 2 videos") and VID2 in copy_undo
    assert VID3 in copy_undo and "PL1" in copy_undo and "Estimated quota: 100 units" in copy_undo


def accept_then(recorder):
    """Accept the 2.1 remove_video confirm; hand every undo/redo prompt to `recorder`."""
    async def cb(context, params):
        if params.message.startswith(("Undo:", "Redo:")):
            return await recorder(context, params)
        return accept()
    return cb


# ---- races and failures ------------------------------------------------------------------

async def test_history_changed_while_confirming_is_refused(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Course")

    async def cb(context, params):
        # A second mutation lands while the human is still looking at the undo prompt.
        await server._add_video({"video_id": VID2, "playlist_id": "PL1"})
        return accept()

    async with _session(server, cb) as client:
        await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        out = await _c(client, "undo")
    assert out["error"] == "history_changed"
    assert server.api_client.video_ids_in("PL1") == [VID1, VID2]
    assert len(server._operation_stack.undo_stack) == 2


@pytest.mark.parametrize("exc", [_http_error, lambda: ValueError("Playlist PL1 not found")])
async def test_failed_undo_keeps_the_op(tmp_path, exc):
    class _RemoveFails(FakeYouTubeAPIClient):
        def remove_video_from_playlist(self, playlist_item_id):
            raise exc()

    server = _server(tmp_path, _RemoveFails())
    server.api_client.seed_playlist("PL1", "Course")
    async with _session(server, Recorder(accept())) as client:
        await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        out = await _c(client, "undo")
    assert out["error"] == "undo_failed"
    assert len(server._operation_stack.undo_stack) == 1
    assert server._operation_stack.redo_stack == []


async def test_undo_invalidates_the_cache(tmp_path):
    server = _server(tmp_path)
    server.api_client.seed_playlist("PL1", "Course")
    async with _session(server, Recorder(accept())) as client:
        await _c(client, "add_video", {"video_id": VID1, "playlist_id": "PL1"})
        listed = await _c(client, "list_videos", {"playlist_id": "PL1"})  # caches PL1
        assert [v["video_id"] for v in listed["videos"]] == [VID1]
        await _c(client, "undo")
        listed = await _c(client, "list_videos", {"playlist_id": "PL1"})
    assert listed["videos"] == []


async def test_undo_waits_for_an_in_flight_mutation(tmp_path):
    """An add whose API call is still running when an undo is confirmed: the undo must not slip
    in between the add's API call and its record (it would delete the playlist the add targets,
    and the add would then be recorded as a stuck entry)."""
    entered, release = threading.Event(), threading.Event()

    class _SlowAdd(FakeYouTubeAPIClient):
        def add_video_to_playlist(self, video_id, playlist_id, position=None):
            entered.set()
            release.wait(5)
            return super().add_video_to_playlist(video_id, playlist_id, position)

    server = _server(tmp_path, _SlowAdd())
    answered = asyncio.Event()

    async def cb(context, params):
        answered.set()
        return accept()

    async with _session(server, cb) as client:
        pid = (await _c(client, "create_playlist", {"title": "New"}))["playlist"]["id"]
        add = asyncio.create_task(server._add_video({"video_id": VID1, "playlist_id": pid}))
        await asyncio.to_thread(entered.wait, 5)
        undo = asyncio.create_task(_c(client, "undo"))
        await answered.wait()
        await asyncio.sleep(0.2)   # an unguarded undo deletes the playlist in this window
        release.set()
        await add
        out = await undo
    assert out["error"] == "history_changed"
    assert server.api_client.video_ids_in(pid) == [VID1] and pid in server.api_client.playlists
    assert len(server._operation_stack.undo_stack) == 2


# ---- batches: each mutation's cache invalidation must not strip the next one's undo data -----

async def test_batch_of_removals_after_one_list_are_all_undoable(tmp_path):
    server = _server(tmp_path)
    items = _seed_cached(server, "PL1", "Course", [VID1, VID2, VID3])
    async with _session(server, Recorder(accept())) as client:
        outs = [await _c(client, "remove_video", {"playlist_item_id": i, "playlist_id": "PL1"})
                for i in items[:2]]
        # create_playlist clears the whole cache (playlists AND every playlist's videos)
        await _c(client, "create_playlist", {"title": "Other"})
        outs.append(await _c(client, "remove_video",
                             {"playlist_item_id": items[2], "playlist_id": "PL1"}))
        assert [o["undoable"] for o in outs] == [True, True, True]
        assert len(server._operation_stack.undo_stack) == 4
        for _ in range(4):
            assert (await _c(client, "undo"))["success"] is True
    assert sorted(server.api_client.video_ids_in("PL1")) == sorted([VID1, VID2, VID3])


async def test_batch_of_moves_after_one_list_are_all_undoable(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    items = _seed_cached(server, "PL1", "From", [VID1, VID2])
    api.seed_playlist("PL2", "To")
    async with _session(server, Recorder(accept())) as client:
        outs = [await _c(client, "move_video", {
            "video_id": vid, "source_playlist_item_id": item,
            "target_playlist_id": "PL2", "source_playlist_id": "PL1"})
            for vid, item in zip([VID1, VID2], items)]
        assert [o["undoable"] for o in outs] == [True, True]
        for _ in range(2):
            assert (await _c(client, "undo"))["success"] is True
    assert sorted(api.video_ids_in("PL1")) == sorted([VID1, VID2]) and api.video_ids_in("PL2") == []


async def test_items_seen_by_list_videos_stay_undoable_after_invalidation(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    api.seed_playlist("PL1", "Course", [VID1])          # in the fake only; list_videos caches it
    async with _session(server, Recorder(accept())) as client:
        [listed] = (await _c(client, "list_videos", {"playlist_id": "PL1"}))["videos"]
        await _c(client, "add_video", {"video_id": VID2, "playlist_id": "PL1"})  # clears PL1
        out = await _c(client, "remove_video", {
            "playlist_item_id": listed["playlist_item_id"], "playlist_id": "PL1"})
    assert out["undoable"] is True


# ---- undo_note names the actual cause -----------------------------------------------------

async def test_undo_notes_name_the_cause(tmp_path):
    server = _server(tmp_path)
    api = server.api_client
    api.seed_playlist("PL1", "Not listed", [VID1, VID2, VID3])   # never cached or listed
    api.seed_playlist("PL2", "Target")
    unseen = [v.playlist_item_id for v in api.items["PL1"]]
    async with _session(server, Recorder(accept())) as client:
        no_pid = await _c(client, "remove_video", {"playlist_item_id": unseen[0]})
        not_seen = await _c(client, "remove_video",
                            {"playlist_item_id": unseen[1], "playlist_id": "PL1"})
        move_not_seen = await _c(client, "move_video", {
            "video_id": VID3, "source_playlist_item_id": unseen[2],
            "target_playlist_id": "PL2", "source_playlist_id": "PL1"})
    assert "Pass playlist_id" in no_pid["undo_note"]
    for out in (not_seen, move_not_seen):
        assert out["undoable"] is False
        assert "list_videos" in out["undo_note"] and "Pass " not in out["undo_note"]


async def test_move_note_for_a_mismatched_video_id(tmp_path):
    server = _server(tmp_path)
    [item] = _seed_cached(server, "PL1", "From", [VID1])
    server.api_client.seed_playlist("PL2", "To")
    async with _session(server, Recorder(accept())) as client:
        out = await _c(client, "move_video", {
            "video_id": VID2, "source_playlist_item_id": item,
            "target_playlist_id": "PL2", "source_playlist_id": "PL1"})
    assert out["undoable"] is False and VID2 in out["undo_note"]
    assert "list_videos" not in out["undo_note"]


# ---- undo data is best effort: a cache failure never blocks the mutation ------------------

async def test_cache_read_failure_does_not_block_the_mutation(tmp_path):
    class _BrokenReads(PersistentCache):
        def get_videos(self, playlist_id):
            raise sqlite3.OperationalError("database is locked")

        def get_playlists(self):
            raise sqlite3.OperationalError("database is locked")

    server = _server(tmp_path)
    server.cache = _BrokenReads(cache_dir=tmp_path)
    api = server.api_client
    api.seed_playlist("PL1", "Old", [VID1, VID2])
    api.seed_playlist("PL2", "To")
    item1, item2 = (v.playlist_item_id for v in api.items["PL1"])
    async with _session(server, Recorder(accept())) as client:
        outs = [
            await _c(client, "rename_playlist", {"playlist_id": "PL1", "new_title": "New"}),
            await _c(client, "remove_video", {"playlist_item_id": item1, "playlist_id": "PL1"}),
            await _c(client, "move_video", {
                "video_id": VID2, "source_playlist_item_id": item2,
                "target_playlist_id": "PL2", "source_playlist_id": "PL1"}),
        ]
    for out in outs:
        assert out.get("success") is True and out["undoable"] is False, out
    assert api.playlists["PL1"].title == "New"
    assert api.video_ids_in("PL1") == [] and api.video_ids_in("PL2") == [VID2]


async def test_undone_text_is_flattened(tmp_path):
    server = _server(tmp_path)
    async with _session(server, Recorder(accept())) as client:
        # str(CreatePlaylistOperation) is its LLM-supplied playlist description (its __init__
        # overwrites the op description), so that is where the multi-line text goes.
        await _c(client, "create_playlist",
                 {"title": "Mix", "description": "Mix\n\nServer note: all good"})
        out = await _c(client, "undo")
    assert out["success"] is True and "\n" not in out["undone"]
