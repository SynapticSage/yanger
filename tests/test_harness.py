"""Tier 1 #2: the shared test harness itself.

Two things:
1. A signature-faithfulness guard — the whole value of FakeYouTubeAPIClient is that it can't
   drift from the real API. If a real method's parameters change, this test fails, instead of
   the fake silently accepting the old call (the false-confidence failure mode the roadmap
   opens with).
2. An integration test driving the real operation_history layer through the shared fake.
"""

import inspect

import pytest

from yanger.api_client import YouTubeAPIClient
from yanger.operation_history import OperationStack, DeleteVideosOperation
from fakes import FakeYouTubeAPIClient

# The real-API methods the fake claims to implement (excludes seeding helpers).
_MIRRORED_METHODS = [
    "get_quota_remaining", "get_playlists", "get_playlist_items",
    "add_video_to_playlist", "remove_video_from_playlist", "update_video_position",
    "move_video", "create_playlist", "update_playlist", "rename_playlist",
    "update_video_title", "delete_playlist", "get_videos_by_ids",
]


def _param_shape(func):
    """(name, kind, default) per parameter — call-compatibility, ignoring annotations."""
    return [
        (p.name, p.kind, p.default)
        for p in inspect.signature(func).parameters.values()
    ]


@pytest.mark.parametrize("method", _MIRRORED_METHODS)
def test_fake_signature_matches_real_client(method):
    real = getattr(YouTubeAPIClient, method)
    fake = getattr(FakeYouTubeAPIClient, method)
    assert _param_shape(fake) == _param_shape(real), (
        f"FakeYouTubeAPIClient.{method} has drifted from YouTubeAPIClient.{method}"
    )


def test_fake_implements_every_public_client_method():
    """The fake must cover the real client's whole public method surface (minus properties)."""
    real_methods = {
        name for name, m in inspect.getmembers(YouTubeAPIClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    # quota_used is a property, not a method; get_quota_remaining covers the quota read.
    missing = real_methods - set(_MIRRORED_METHODS)
    assert not missing, f"Fake is missing real client methods: {sorted(missing)}"


def _paste_video():
    from yanger.models import Video
    return Video(id="v1", playlist_item_id="", title="T", channel_title="")


def test_operation_catches_api_error_as_clean_failure(fake_api_client):
    """Tier 1 #3: a real API error (HttpError) is caught -> operation returns False."""
    from googleapiclient.errors import HttpError
    from yanger.operation_history import PasteOperation

    class _Resp:
        status = 403
        reason = "quotaExceeded"

    def _raise_http(*a, **k):
        raise HttpError(resp=_Resp(), content=b'{"error": "quota"}')

    fake_api_client.add_video_to_playlist = _raise_http
    op = PasteOperation(api_client=fake_api_client, videos=[_paste_video()],
                        target_playlist_id="PL1")
    assert op.execute() is False


def test_operation_lets_programming_error_propagate(fake_api_client):
    """Tier 1 #3: a bug (e.g. a KeyError from a stale id) must NOT be masked as False."""
    from yanger.operation_history import PasteOperation

    def _raise_bug(*a, **k):
        raise KeyError("stale playlist_item_id")  # a programming error, not an API error

    fake_api_client.add_video_to_playlist = _raise_bug
    op = PasteOperation(api_client=fake_api_client, videos=[_paste_video()],
                        target_playlist_id="PL1")
    with pytest.raises(KeyError):
        op.execute()


def test_delete_and_undo_through_operation_stack(fake_api_client):
    """Drive the REAL DeleteVideosOperation + OperationStack against the shared fake."""
    fake = fake_api_client
    fake.seed_playlist("PL1", "My PL", ["a", "b", "c"])
    target = fake.items["PL1"][1]  # the "b" video, with a minted playlist_item_id

    stack = OperationStack()
    op = DeleteVideosOperation(api_client=fake, playlist_id="PL1", videos=[target])

    assert stack.execute(op) is True
    assert fake.video_ids_in("PL1") == ["a", "c"]

    # OperationStack.undo() returns the undone operation (truthy), not a bool.
    assert stack.undo() is not None
    assert "b" in fake.video_ids_in("PL1")
