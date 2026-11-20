"""Roadmap 3.2 (Takeout improvements): JSON watch-history import.

Google Takeout can export YouTube history as `watch-history.json` instead of the default
`watch-history.html`. The HTML parse recovers only video ids; the JSON carries the
watched-at time, the title and the channel, which these tests pin.

FIXTURE IS A GUESS from the widely observed watch-history.json shape; Google publishes no
formal schema. Each record looks like:
    {"header": "YouTube", "title": "Watched <video title>",
     "titleUrl": "https://www.youtube.com/watch?v=<11-char id>",
     "subtitles": [{"name": "<channel>", "url": "..."}],
     "time": "2024-01-15T12:00:00.123Z", "products": ["YouTube"], ...}
The array is newest first.
"""

import json
import zipfile
from datetime import timedelta

from yanger.takeout import TakeoutParser

VID_A = "dQw4w9WgXcQ"
VID_B = "abcdefghijk"
VID_C = "ABCDEFGHIJK"
VID_HTML = "zzzzzzzzzzz"


def _record(vid, time, title=None, channel=None, host="www.youtube.com"):
    url = f"https://{host}/watch?v={vid}"
    rec = {
        "header": "YouTube",
        "title": title if title is not None else f"Watched Video {vid}",
        "titleUrl": url,
        "time": time,
        "products": ["YouTube"],
        "activityControls": ["YouTube watch history"],
    }
    if channel:
        rec["subtitles"] = [{"name": channel, "url": "https://www.youtube.com/channel/UCx"}]
    return rec


def _parse(records):
    return TakeoutParser()._parse_watch_history_json_content(json.dumps(records))


def _is_utc(dt):
    return dt is not None and dt.utcoffset() == timedelta(0)


def test_z_suffixed_times_with_and_without_millis_parse_to_utc():
    videos = _parse([
        _record(VID_A, "2024-01-15T12:00:00.123Z"),
        _record(VID_B, "2024-01-14T08:30:00Z"),
    ])
    assert [v.video_id for v in videos] == [VID_A, VID_B]
    assert _is_utc(videos[0].added_at) and videos[0].added_at.microsecond == 123000
    assert _is_utc(videos[1].added_at) and videos[1].added_at.hour == 8


def test_one_digit_fraction_parses():
    # datetime.fromisoformat on Python 3.10 rejects a 1-digit fraction; isoparse does not.
    videos = _parse([_record(VID_A, "2024-01-15T12:00:00.5Z")])
    assert _is_utc(videos[0].added_at)
    assert videos[0].added_at.microsecond == 500000


def test_title_prefix_stripped_and_channel_from_subtitles():
    videos = _parse([_record(VID_A, "2024-01-15T12:00:00Z",
                             title="Watched Never Gonna Give You Up", channel="Rick Astley")])
    assert videos[0].title == "Never Gonna Give You Up"
    assert videos[0].channel == "Rick Astley"
    assert videos[0].playlist_name == "History"


def test_repeat_watch_keeps_first_most_recent_record():
    videos = _parse([
        _record(VID_A, "2024-03-01T00:00:00Z"),   # most recent watch
        _record(VID_B, "2024-02-01T00:00:00Z"),
        _record(VID_A, "2024-01-01T00:00:00Z"),   # older watch of the same video
    ])
    assert [v.video_id for v in videos] == [VID_A, VID_B]
    assert videos[0].added_at.month == 3


def test_non_video_records_skipped_music_included_url_title_nulled():
    url_title = f"https://www.youtube.com/watch?v={VID_C}"
    records = [
        {"header": "YouTube", "title": "Searched for lofi", "time": "2024-01-15T12:00:00Z"},
        {"header": "YouTube", "title": "Viewed a post", "titleUrl": "https://www.youtube.com/post/Ugx",
         "time": "2024-01-15T12:00:00Z"},
        _record(VID_B, "2024-01-15T12:00:00Z", host="music.youtube.com"),
        _record(VID_C, "2024-01-15T12:00:00Z", title=f"Watched {url_title}"),
    ]
    videos = _parse(records)
    assert [v.video_id for v in videos] == [VID_B, VID_C]
    # A removed/private video's record has the URL as its title: leave it None so
    # `yanger fetch-metadata` (which selects empty titles) still fills it.
    assert videos[1].title is None


def test_malformed_input_returns_empty_without_raising():
    p = TakeoutParser()
    assert p._parse_watch_history_json_content('{"a": 1}') == []
    assert p._parse_watch_history_json_content("not json [") == []


def test_bad_time_values_leave_added_at_none():
    records = [_record(VID_A, None), _record(VID_B, "yesterday"), _record(VID_C, 12345)]
    videos = _parse(records)
    assert [v.video_id for v in videos] == [VID_A, VID_B, VID_C]
    assert all(v.added_at is None for v in videos)


def _write_json_history(history_dir, records):
    history_dir.mkdir(parents=True, exist_ok=True)
    (history_dir / "watch-history.json").write_text(json.dumps(records), encoding="utf-8")


def test_directory_flow_prefers_json_over_html(tmp_path):
    history = tmp_path / "Takeout" / "YouTube and YouTube Music" / "history"
    _write_json_history(history, [_record(VID_A, "2024-01-15T12:00:00Z", channel="Ch")])
    (history / "watch-history.html").write_text(
        f'<a href="https://www.youtube.com/watch?v={VID_HTML}">x</a>', encoding="utf-8")

    playlists = TakeoutParser().process_path(tmp_path)

    hist = playlists["History (Imported)"]
    assert hist.source == "history"
    assert [v.video_id for v in hist.videos] == [VID_A]
    assert _is_utc(hist.videos[0].added_at)
    assert hist.videos[0].channel == "Ch"


def test_directory_flow_falls_back_to_html(tmp_path):
    history = tmp_path / "YouTube and YouTube Music" / "history"
    history.mkdir(parents=True)
    (history / "watch-history.html").write_text(
        f'<a href="https://www.youtube.com/watch?v={VID_HTML}">x</a>', encoding="utf-8")

    playlists = TakeoutParser().process_path(tmp_path)

    assert [v.video_id for v in playlists["History (Imported)"].videos] == [VID_HTML]


def test_zip_flow_reads_json_history(tmp_path):
    zpath = tmp_path / "takeout-20240101T000000Z-001.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("Takeout/YouTube and YouTube Music/history/watch-history.json",
                    json.dumps([_record(VID_A, "2024-01-15T12:00:00.123Z")]))
        zf.writestr("Takeout/YouTube and YouTube Music/history/watch-history.html",
                    f'<a href="https://www.youtube.com/watch?v={VID_HTML}">x</a>')

    playlists = TakeoutParser().process_path(zpath)

    vids = playlists["History (Imported)"].videos
    assert [v.video_id for v in vids] == [VID_A]
    assert _is_utc(vids[0].added_at)
