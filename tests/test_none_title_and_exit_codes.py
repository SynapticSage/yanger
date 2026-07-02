"""Tier-0 correctness tests: None-title robustness (0.4) + non-zero exit codes (0.9).

0.4 — pre-metadata (Takeout) videos arrive with NULL title/channel/description and used to
crash the CLI duplicate/statistics/export paths. `Video.__post_init__` now coerces None→"".

0.9 — `proxy test` and `fetch-metadata` used to exit 0 on error, breaking CI/scripting.
"""

from datetime import datetime

from click.testing import CliRunner

from yanger.cli import cli
from yanger.models import Video
from yanger.duplicates import DuplicateDetector
from yanger.statistics import PlaylistAnalyzer
import yanger.core.proxy as proxymod


def _vid(vid, **kw):
    return Video(id=vid, playlist_item_id=f"pi_{vid}", title=None, channel_title=None, **kw)


# ----- 0.4 None-title robustness ------------------------------------------------

def test_video_coerces_none_strings():
    v = Video(id="a", playlist_item_id="p", title=None, channel_title=None, description=None)
    assert v.title == "" and v.channel_title == "" and v.description == ""


def test_normalize_title_handles_none():
    assert DuplicateDetector()._normalize_title(None) == ""


def test_find_duplicates_none_titles_no_crash():
    result = DuplicateDetector().find_duplicates([_vid("a"), _vid("b")])
    assert isinstance(result, list)


def test_statistics_format_none_titles_no_crash():
    """Exercises every `.title[:40]` branch (shortest/longest/oldest/newest/most/least)."""
    vids = [
        _vid("a", duration="PT5M30S", view_count=100, published_at=datetime(2020, 1, 1)),
        _vid("b", duration="PT3M10S", view_count=5, published_at=datetime(2021, 6, 1)),
    ]
    analyzer = PlaylistAnalyzer()
    stats = analyzer.analyze(vids, "test")
    report = analyzer.format_stats(stats, detailed=True)
    assert isinstance(report, str) and len(report) > 0


# ----- 0.9 non-zero exit codes --------------------------------------------------

def test_proxy_test_exits_nonzero_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(proxymod, "test_proxy_connection",
                        lambda settings, vid: {"success": False, "error": "blocked"})
    result = CliRunner().invoke(cli, ["proxy", "test"])
    assert result.exit_code == 1, result.output


def test_proxy_test_exits_zero_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(proxymod, "test_proxy_connection",
                        lambda settings, vid: {"success": True, "transcript_length": 42})
    result = CliRunner().invoke(cli, ["proxy", "test"])
    assert result.exit_code == 0, result.output


def test_fetch_metadata_conflicting_date_opts_exit_nonzero(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(cli, ["fetch-metadata", "--since", "2024-01-01", "--days-ago", "7"])
    assert result.exit_code == 1, result.output


def test_fetch_metadata_bad_date_exit_nonzero(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = CliRunner().invoke(cli, ["fetch-metadata", "--since", "not-a-date"])
    assert result.exit_code == 1, result.output


def test_fetch_metadata_playlist_not_found_exit_nonzero(monkeypatch, tmp_path):
    """The only sys.exit(1) inside the try-block: guards against a future widened handler."""
    monkeypatch.setenv("HOME", str(tmp_path))
    from yanger.cache import PersistentCache
    monkeypatch.setattr(
        PersistentCache, "get_virtual_playlists",
        lambda self: [{"id": "v1", "title": "Real", "video_count": 1}],
    )
    result = CliRunner().invoke(cli, ["fetch-metadata", "--playlist", "Nonexistent"])
    assert result.exit_code == 1, result.output
