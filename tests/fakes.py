"""Shared test doubles (Tier 1 #2 harness).

FakeYouTubeAPIClient is a FAITHFUL in-memory stand-in for `YouTubeAPIClient`: it mirrors the
real method NAMES and SIGNATURES and tracks playlist membership so mutating operations have
observable effects. Unlike a `MagicMock`, a wrong method name or arg raises — which is the
whole point: two prior Criticals hid behind mocks of nonexistent methods (see ROADMAP guiding
theme). Import this instead of hand-rolling per-test mocks.
"""

from typing import List, Optional, Dict, Any

from yanger.models import Playlist, Video


class FakeYouTubeAPIClient:
    """In-memory YouTubeAPIClient double with real signatures."""

    def __init__(self):
        self.playlists: Dict[str, Playlist] = {}          # playlist_id -> Playlist
        self.items: Dict[str, List[Video]] = {}           # playlist_id -> ordered [Video]
        self._counter = 0
        self.daily_quota = 10000
        self._quota_used = 0

    # ----- test seeding helpers (not part of the real API) -----

    def seed_playlist(self, playlist_id: str, title: str = "PL",
                      video_ids: Optional[List[str]] = None) -> Playlist:
        """Create a playlist with videos whose playlist_item_ids are minted like the real API."""
        pl = Playlist(id=playlist_id, title=title)
        self.playlists[playlist_id] = pl
        self.items[playlist_id] = [
            self._make_video(vid, playlist_id) for vid in (video_ids or [])
        ]
        return pl

    def video_ids_in(self, playlist_id: str) -> List[str]:
        return [v.id for v in self.items.get(playlist_id, [])]

    # ----- internals -----

    def _new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    def _make_video(self, video_id: str, playlist_id: str) -> Video:
        return Video(
            id=video_id,
            playlist_item_id=self._new_id("item"),
            title=f"Video {video_id}",
            channel_title="Channel",
            playlist_id=playlist_id,
        )

    # ----- real YouTubeAPIClient surface (matching signatures) -----

    def get_quota_remaining(self) -> int:
        return self.daily_quota - self._quota_used

    def get_playlists(self, mine: bool = True, channel_id: Optional[str] = None,
                      max_results: int = 50, include_special: bool = True,
                      progress_callback=None) -> List[Playlist]:
        return list(self.playlists.values())

    def get_playlist_items(self, playlist_id: str, max_results: int = 50,
                           progress_callback=None) -> List[Video]:
        return list(self.items.get(playlist_id, []))

    def add_video_to_playlist(self, video_id: str, playlist_id: str,
                              position: Optional[int] = None) -> str:
        video = self._make_video(video_id, playlist_id)
        lst = self.items.setdefault(playlist_id, [])
        if position is None or position >= len(lst):
            lst.append(video)
        else:
            lst.insert(position, video)
        return video.playlist_item_id

    def remove_video_from_playlist(self, playlist_item_id: str) -> None:
        for lst in self.items.values():
            for v in lst:
                if v.playlist_item_id == playlist_item_id:
                    lst.remove(v)
                    return
        raise KeyError(f"unknown playlist_item_id: {playlist_item_id}")

    def update_video_position(self, playlist_item_id: str, playlist_id: str,
                              video_id: str, new_position: int) -> None:
        lst = self.items.get(playlist_id, [])
        for i, v in enumerate(lst):
            if v.playlist_item_id == playlist_item_id:
                lst.pop(i)
                lst.insert(min(new_position, len(lst)), v)
                return
        raise KeyError(f"unknown playlist_item_id: {playlist_item_id}")

    def move_video(self, video: Video, target_playlist_id: str) -> str:
        new_item_id = self.add_video_to_playlist(video.id, target_playlist_id)
        self.remove_video_from_playlist(video.playlist_item_id)
        return new_item_id

    def create_playlist(self, title: str, description: str = "",
                        privacy_status: str = "private") -> Playlist:
        pid = self._new_id("PL")
        pl = Playlist(id=pid, title=title, description=description)
        self.playlists[pid] = pl
        self.items[pid] = []
        return pl

    def update_playlist(self, playlist_id: str, title: Optional[str] = None,
                        description: Optional[str] = None,
                        privacy_status: Optional[str] = None) -> None:
        pl = self.playlists.get(playlist_id)
        if pl is not None and title is not None:
            pl.title = title

    def rename_playlist(self, playlist_id: str, new_title: str) -> None:
        pl = self.playlists.get(playlist_id)
        if pl is not None:
            pl.title = new_title

    def update_video_title(self, video_id: str, new_title: str,
                           playlist_id: Optional[str] = None) -> None:
        for lst in self.items.values():
            for v in lst:
                if v.id == video_id:
                    v.title = new_title

    def delete_playlist(self, playlist_id: str) -> None:
        self.playlists.pop(playlist_id, None)
        self.items.pop(playlist_id, None)

    def get_videos_by_ids(self, video_ids: List[str]) -> List[Dict[str, Any]]:
        return [{"video_id": vid, "title": f"Video {vid}"} for vid in video_ids]
