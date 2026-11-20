"""Regression tests for undo/redo operations in operation_history.py.

Covers three latent bugs:
- DeleteVideosOperation.undo called a nonexistent api method (deletes were
  unrecoverable).
- BulkEditOperation reorder calls used the old 2-arg signature.
- PasteOperation accumulated stale item ids on redo and removed the source
  using a playlist_item_id invalidated by a prior undo.
"""
# Created: 2026-06-27

import pytest

from yanger.models import Video
from yanger.bulkedit import BulkEditChanges, VideoReorder, VideoMove
from yanger.operation_history import (
    PasteOperation,
    BulkEditOperation,
    DeleteVideosOperation,
)


class FakeApiClient:
    """In-memory stand-in for YouTubeAPIClient.

    Tracks playlist membership as {playlist_id: [{'item_id', 'video_id'}]} and
    mints a fresh item id on every add, so stale-id reuse surfaces as an error.
    """

    def __init__(self):
        self.playlists = {}
        self._next = 0
        self.position_calls = []  # (item_id, playlist_id, video_id, new_position)

    def _new_item_id(self):
        self._next += 1
        return f"item-{self._next}"

    def add_video_to_playlist(self, video_id, playlist_id, position=None):
        item_id = self._new_item_id()
        self.playlists.setdefault(playlist_id, []).append(
            {'item_id': item_id, 'video_id': video_id}
        )
        return item_id

    def remove_video_from_playlist(self, playlist_item_id):
        for items in self.playlists.values():
            for entry in items:
                if entry['item_id'] == playlist_item_id:
                    items.remove(entry)
                    return
        raise KeyError(f"unknown playlist_item_id: {playlist_item_id}")

    def update_video_position(self, playlist_item_id, playlist_id, video_id, new_position):
        self.position_calls.append(
            (playlist_item_id, playlist_id, video_id, new_position)
        )

    def video_ids_in(self, playlist_id):
        return [e['video_id'] for e in self.playlists.get(playlist_id, [])]


def make_video(video_id, item_id, playlist_id):
    return Video(
        id=video_id,
        playlist_item_id=item_id,
        title=f"Video {video_id}",
        channel_title="Channel",
        playlist_id=playlist_id,
    )


class TestDeleteVideosUndo:
    """DeleteVideosOperation.undo must re-add via add_video_to_playlist."""

    def test_undo_restores_deleted_video(self):
        client = FakeApiClient()
        client.playlists["PL"] = [{'item_id': 'item-orig', 'video_id': 'vid1'}]
        video = make_video("vid1", "item-orig", "PL")

        op = DeleteVideosOperation(client, "PL", [video])
        assert op.execute() is True
        assert client.video_ids_in("PL") == []

        # Previously raised AttributeError (add_to_playlist) and returned False.
        assert op.undo() is True
        assert client.video_ids_in("PL") == ["vid1"]


class TestBulkEditReorder:
    """Reorder calls must use the 4-arg update_video_position signature."""

    def test_execute_and_undo_pass_full_signature(self):
        client = FakeApiClient()
        video = make_video("vid1", "item-1", "PL")
        reorder = VideoReorder(
            video=video, playlist_id="PL", old_position=0, new_position=3
        )
        changes = BulkEditChanges(reorders=[reorder])

        op = BulkEditOperation(client, changes)
        assert op.execute() is True
        # New-position call recorded with all four arguments.
        assert client.position_calls[-1] == ("item-1", "PL", "vid1", 3)
        assert op.applied_reorders == [reorder]

        assert op.undo() is True
        # Undo restores the old position via the same 4-arg call.
        assert client.position_calls[-1] == ("item-1", "PL", "vid1", 0)


class TestBulkEditOperationClientMethods:
    """The real apply path calls the correct SYNC client methods (repointed from the
    deleted BulkEditExecutor test in Tier 0.8)."""

    def test_execute_calls_correct_client_methods(self):
        client = FakeApiClient()
        # Seed source membership so the move's/deletion's remove calls resolve.
        client.playlists["p1"] = [{'item_id': 'id_mov', 'video_id': 'vm'}]
        client.playlists["p"] = [{'item_id': 'id_del', 'video_id': 'vd'}]

        changes = BulkEditChanges(
            moves=[VideoMove(make_video("vm", "id_mov", "p1"), "p1", "p2", 0)],
            reorders=[VideoReorder(make_video("vr", "id_reo", "p2"), "p2", 0, 1)],
            deletions=[(make_video("vd", "id_del", "p"), "p")],
        )
        op = BulkEditOperation(client, changes)
        assert op.execute() is True

        # Deletion removed from its playlist.
        assert client.video_ids_in("p") == []
        # Move: added to target, removed from source.
        assert "vm" in client.video_ids_in("p2")
        assert client.video_ids_in("p1") == []
        # Reorder: full 4-arg update_video_position signature.
        assert ("id_reo", "p2", "vr", 1) in client.position_calls


class TestPasteCutRedo:
    """A cut survives undo->redo without duplicating or losing the video."""

    def test_undo_then_redo_leaves_video_only_in_target(self):
        client = FakeApiClient()
        client.playlists["SRC"] = [{'item_id': 'item-src', 'video_id': 'vid1'}]
        video = make_video("vid1", "item-src", "SRC")

        op = PasteOperation(
            client, [video], target_playlist_id="DST",
            source_playlist_id="SRC", is_cut=True,
        )

        assert op.execute() is True
        assert client.video_ids_in("DST") == ["vid1"]
        assert client.video_ids_in("SRC") == []
        assert len(op.added_item_ids) == 1

        assert op.undo() is True
        assert client.video_ids_in("SRC") == ["vid1"]
        assert client.video_ids_in("DST") == []

        # Redo must not reuse the stale source id or accumulate target ids.
        assert op.execute() is True
        assert client.video_ids_in("DST") == ["vid1"]
        assert client.video_ids_in("SRC") == []
        assert len(op.added_item_ids) == 1


# ---- ROADMAP 2.2 (MCP undo parity): OperationStack.record + DeleteVideos redo -----------

from fakes import FakeYouTubeAPIClient  # noqa: E402
from yanger.operation_history import OperationStack, CreatePlaylistOperation, RenameOperation  # noqa: E402


class TestDeleteVideosRedo:
    """Undo re-adds the video under a NEW playlist item id; redo must remove that one."""

    def test_execute_undo_redo_leaves_video_removed(self):
        api = FakeYouTubeAPIClient()
        api.seed_playlist("PL", video_ids=["vid1"])
        video = api.get_playlist_items("PL")[0]

        op = DeleteVideosOperation(api, "PL", [video])
        assert op.execute() is True
        assert op.undo() is True
        assert api.video_ids_in("PL") == ["vid1"]
        # Previously raised KeyError: unknown playlist_item_id (the pre-undo id).
        assert op.execute() is True
        assert api.video_ids_in("PL") == []


class TestOperationStackRecord:
    """record() logs an operation that already ran elsewhere (the MCP server makes the API call
    itself so its errors reach the MCP error contract), with execute()'s stack bookkeeping."""

    def _executed_create(self, api, title="T"):
        op = CreatePlaylistOperation(api, title)
        assert op.execute() is True
        return op

    def test_record_pushes_and_clears_redo(self):
        api = FakeYouTubeAPIClient()
        stack = OperationStack()
        first = self._executed_create(api, "A")
        stack.record(first)
        assert stack.undo() is first
        assert stack.can_redo()

        second = self._executed_create(api, "B")
        stack.record(second)
        assert stack.undo_stack == [second]
        assert not stack.can_redo()

    def test_record_refuses_unexecuted_op(self):
        stack = OperationStack()
        with pytest.raises(ValueError):
            stack.record(CreatePlaylistOperation(FakeYouTubeAPIClient(), "never ran"))
        assert stack.undo_stack == []

    def test_record_trims_to_max_size(self):
        api = FakeYouTubeAPIClient()
        stack = OperationStack(max_size=2)
        ops = [self._executed_create(api, str(i)) for i in range(3)]
        for op in ops:
            stack.record(op)
        assert stack.undo_stack == ops[1:]

    def test_unexpected_exception_in_undo_keeps_op_on_stack(self):
        """An error the Operation does not catch (ValueError 'Playlist not found' from
        update_playlist, a fake's KeyError) must not drop the op from both stacks."""
        class _Raises(FakeYouTubeAPIClient):
            def rename_playlist(self, playlist_id, new_title):
                raise ValueError(f"Playlist {playlist_id} not found")

        api = FakeYouTubeAPIClient()
        api.seed_playlist("PL", title="old")
        op = RenameOperation(api, "playlist", "PL", "old", "new")
        assert op.execute() is True
        stack = OperationStack()
        stack.record(op)

        op.api_client = _Raises()
        with pytest.raises(ValueError):
            stack.undo()
        assert stack.undo_stack == [op] and stack.redo_stack == []

    def test_unexpected_exception_in_redo_keeps_op_on_stack(self):
        class _Raises(FakeYouTubeAPIClient):
            def rename_playlist(self, playlist_id, new_title):
                raise ValueError(f"Playlist {playlist_id} not found")

        api = FakeYouTubeAPIClient()
        api.seed_playlist("PL", title="old")
        op = RenameOperation(api, "playlist", "PL", "old", "new")
        stack = OperationStack()
        assert stack.execute(op) is True
        assert stack.undo() is op

        op.api_client = _Raises()
        with pytest.raises(ValueError):
            stack.redo()
        assert stack.redo_stack == [op] and stack.undo_stack == []
