"""Tier 1 #2: coverage for the takeout import parsers (previously zero direct tests).

Google Takeout import is a marquee feature; these pin the content-level parsing that the
zip/directory flows delegate to, so a regression in ID extraction or validation is caught.
"""

from yanger.takeout import TakeoutParser


VALID = "dQw4w9WgXcQ"   # 11-char YouTube id
VALID2 = "abcdefghijk"


def test_is_valid_video_id():
    p = TakeoutParser()
    assert p._is_valid_video_id(VALID) is True
    assert p._is_valid_video_id("tooShort") is False       # not 11 chars
    assert p._is_valid_video_id("") is False
    assert p._is_valid_video_id("has spaces!") is False     # invalid chars


def test_parse_playlist_csv_content_extracts_videos_and_timestamps():
    p = TakeoutParser()
    csv_content = "\n".join([
        "Video ID,Playlist Video Creation Timestamp",
        f"{VALID},2024-01-15T12:00:00+00:00",
        f"{VALID2},2024-02-01T08:30:00+00:00",
    ])
    videos = p._parse_playlist_csv_content(csv_content, "My PL")
    assert [v.video_id for v in videos] == [VALID, VALID2]
    assert videos[0].playlist_name == "My PL"
    assert videos[0].added_at is not None  # timestamp parsed


def test_parse_playlist_csv_parses_z_suffixed_timestamp():
    """A trailing 'Z' (UTC) timestamp must parse (fromisoformat rejects 'Z' on Py<3.11); a
    prior no-op `.replace('+00:00','+00:00')` silently dropped such timestamps to None."""
    p = TakeoutParser()
    csv_content = "\n".join([
        "Video ID,Playlist Video Creation Timestamp",
        f"{VALID},2024-01-15T12:00:00Z",
    ])
    videos = p._parse_playlist_csv_content(csv_content, "PL")
    assert len(videos) == 1
    assert videos[0].added_at is not None
    assert videos[0].added_at.year == 2024


def test_parse_playlist_csv_skips_invalid_and_empty_ids():
    p = TakeoutParser()
    csv_content = "\n".join([
        "Video ID,Playlist Video Creation Timestamp",
        "tooShort,2024-01-15T12:00:00+00:00",
        ",",
        f"{VALID},",  # valid id, missing timestamp (added_at stays None)
    ])
    videos = p._parse_playlist_csv_content(csv_content, "PL")
    assert [v.video_id for v in videos] == [VALID]
    assert videos[0].added_at is None


def test_parse_watch_history_dedupes_and_preserves_order():
    p = TakeoutParser()
    html = f"""
    <a href="https://www.youtube.com/watch?v={VALID}">Video</a>
    <a href="https://www.youtube.com/watch?v={VALID}">Same video again</a>
    <a href="https://www.youtube.com/watch?v={VALID2}">Other</a>
    """
    videos = p._parse_watch_history_content(html)
    assert [v.video_id for v in videos] == [VALID, VALID2]      # deduped, order preserved
    assert all(v.playlist_name == "History" for v in videos)


# ----- Roadmap 3.2: zip-import metadata enrichment ---------------------------------
# Takeout's "video metadata/videos.csv" lists the person's own channel uploads; directory
# import already enriched from it, zip import never did. These pin zip parity.

import json
import os
import zipfile

from click.testing import CliRunner

import yanger.cli as cli_mod
from yanger.cli import cli, _latest_export_zips
from yanger.takeout import TakeoutPlaylist, TakeoutVideo

YT = "Takeout/YouTube and YouTube Music"
META_CSV = "\n".join([
    "Video ID,Video Title (Original),Approx Duration (ms)",
    f"{VALID},Real Title,61000",
])


def _zip(path, files):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def test_zip_import_enriches_from_videos_csv(tmp_path):
    z = _zip(tmp_path / "t.zip", {
        f"{YT}/playlists/Foo-videos.csv": f"Video ID,Playlist Video Creation Timestamp\n{VALID},\n",
        f"{YT}/video metadata/videos.csv": META_CSV,
    })
    playlists = TakeoutParser().process_path(z)
    video = playlists["Foo"].videos[0]
    assert video.title == "Real Title"
    assert video.duration_ms == 61000


def test_undecodable_videos_csv_does_not_abort_zip_import(tmp_path):
    # Enrichment is optional: a bad videos.csv is logged and skipped, as before the
    # zip flow read it at all, rather than dropping the whole zip's playlists.
    z = _zip(tmp_path / "t.zip", {
        f"{YT}/playlists/Foo-videos.csv": f"Video ID,Playlist Video Creation Timestamp\n{VALID},\n",
        f"{YT}/video metadata/videos.csv": b"Video ID\n\xff\xfe\n",
    })
    playlists = TakeoutParser().process_path(z)
    assert [v.video_id for v in playlists["Foo"].videos] == [VALID]


def test_split_export_enriches_across_parts(tmp_path):
    p1 = _zip(tmp_path / "takeout-20240101T000000Z-001.zip", {
        f"{YT}/playlists/Foo-videos.csv": f"Video ID,Playlist Video Creation Timestamp\n{VALID},\n",
    })
    p2 = _zip(tmp_path / "takeout-20240101T000000Z-002.zip", {
        f"{YT}/video metadata/videos.csv": META_CSV,
    })
    playlists = TakeoutParser().process_multiple([p1, p2])
    assert playlists["Foo"].videos[0].title == "Real Title"


def test_empty_metadata_title_does_not_clobber_known_title():
    p = TakeoutParser()
    p.video_metadata[VALID] = {"title": "", "duration_ms": ""}
    playlists = {"History (Imported)": TakeoutPlaylist(
        name="History (Imported)", source="history",
        videos=[TakeoutVideo(video_id=VALID, title="From JSON")])}
    p._enrich_with_metadata(playlists)
    assert playlists["History (Imported)"].videos[0].title == "From JSON"


# ----- Roadmap 3.2: `yanger sync --resume` (download-only) ---------------------------

def _sandbox_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("YANGER_CACHE_DIR", raising=False)
    return tmp_path


def _forbid(*_a, **_k):
    raise AssertionError("must not be called")


def _forbid_browser(monkeypatch):
    monkeypatch.setattr(cli_mod, "_devtools_up", _forbid)
    monkeypatch.setattr(cli_mod, "_launch_chrome", _forbid)
    monkeypatch.setattr(cli_mod.subprocess, "run", _forbid)


def _watch_later_zip(path, vid=VALID):
    return _zip(path, {
        f"{YT}/playlists/Watch later-videos.csv":
            f"Video ID,Playlist Video Creation Timestamp\n{vid},2024-01-15T12:00:00Z\n",
    })


class _Proc:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0


def _fake_node(monkeypatch, result):
    """Pretend Chrome and node are ready; capture the node command line."""
    calls = []

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        return _Proc(json.dumps(result) + "\n")

    monkeypatch.setattr(cli_mod, "_devtools_up", lambda port: True)
    monkeypatch.setattr(cli_mod.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli_mod, "_ensure_node_deps", lambda: True)
    monkeypatch.setattr(cli_mod.subprocess, "run", fake_run)
    return calls


def test_latest_export_zips_groups_split_parts_and_fails_closed(tmp_path):
    old = _watch_later_zip(tmp_path / "takeout-20230101T000000Z-001.zip")
    p1 = _watch_later_zip(tmp_path / "takeout-20240101T000000Z-001.zip")
    p2 = _watch_later_zip(tmp_path / "takeout-20240101T000000Z-002.zip")
    os.utime(old, (1_000, 1_000))
    os.utime(p1, (2_000, 2_000))
    os.utime(p2, (3_000, 3_000))
    (tmp_path / "empty.zip").write_bytes(b"")  # zero-byte: never chosen

    assert _latest_export_zips(tmp_path) == [p1, p2]

    (tmp_path / "takeout-x.zip.crdownload").write_bytes(b"partial")
    assert _latest_export_zips(tmp_path) == []


def test_sync_resume_imports_finished_zip_without_browser(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _watch_later_zip(downloads / "takeout-20240101T000000Z-001.zip")
    _forbid_browser(monkeypatch)

    result = CliRunner().invoke(cli, ["sync", "--resume", "--download-dir", str(downloads)],
                                input="y\n")

    assert result.exit_code == 0, result.output
    assert "takeout-20240101T000000Z-001.zip" in result.output
    assert "Import Complete" in result.output


def test_sync_resume_declined_zip_waits_download_only(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _watch_later_zip(downloads / "takeout-20230101T000000Z-001.zip")  # stale export
    calls = _fake_node(monkeypatch, {"status": "timeout"})

    result = CliRunner().invoke(cli, ["sync", "--resume", "--download-dir", str(downloads)],
                                input="n\n")

    assert result.exit_code == 0, result.output
    assert "Import Complete" not in result.output
    assert len(calls) == 1 and "--download-only" in calls[0]


def test_sync_resume_partial_download_names_file_and_finishes_later(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    downloads = tmp_path / "dl"
    downloads.mkdir()
    _watch_later_zip(downloads / "takeout-20240101T000000Z-001.zip")
    (downloads / "takeout-20240101T000000Z-002.zip.crdownload").write_bytes(b"partial")
    _forbid_browser(monkeypatch)

    result = CliRunner().invoke(cli, ["sync", "--resume", "--wait-minutes", "0",
                                      "--download-dir", str(downloads)])

    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())  # undo Rich's line wrapping
    assert "takeout-20240101T000000Z-002.zip.crdownload" in out
    assert "delete it if stale" in out
    assert "yanger sync --resume" in out
    assert "Import Complete" not in out


def test_sync_resume_no_zip_runs_node_download_only(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    downloads = tmp_path / "dl"
    calls = _fake_node(monkeypatch, {"status": "timeout"})

    result = CliRunner().invoke(cli, ["sync", "--resume", "--download-dir", str(downloads)])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1 and "--download-only" in calls[0]


def test_sync_without_resume_does_not_pass_download_only(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    calls = _fake_node(monkeypatch, {"status": "timeout"})

    result = CliRunner().invoke(cli, ["sync", "--download-dir", str(tmp_path / "dl")])

    assert result.exit_code == 0, result.output
    assert "--download-only" not in calls[0]


def test_sync_downloaded_part_imports_its_split_siblings(monkeypatch, tmp_path):
    _sandbox_home(monkeypatch, tmp_path)
    downloads = tmp_path / "dl"
    downloads.mkdir()
    p1 = _watch_later_zip(downloads / "takeout-20240101T000000Z-001.zip", VALID)
    _zip(downloads / "takeout-20240101T000000Z-002.zip", {
        f"{YT}/playlists/Foo-videos.csv": f"Video ID,Playlist Video Creation Timestamp\n{VALID2},\n",
    })
    _fake_node(monkeypatch, {"status": "downloaded", "zipPath": str(p1)})

    result = CliRunner().invoke(cli, ["sync", "--resume", "--download-dir", str(downloads)],
                                input="n\n")

    assert result.exit_code == 0, result.output
    assert "Total videos processed: 2" in result.output
