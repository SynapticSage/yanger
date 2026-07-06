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
