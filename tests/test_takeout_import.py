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
