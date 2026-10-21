import json
import sys
import types

import pytest
from app import media


def feed(item_id="123456", **video):
    return {
        "status_code": 0,
        "aweme_list": [
            {
                "aweme_id": item_id,
                "video": {
                    "duration": 862900,
                    "play_addr": {
                        "url_list": ["https://cdn.douyinvod.com/video.mp4"],
                        "data_size": 157278368,
                    },
                    **video,
                },
            }
        ],
    }


def mock_json_download(monkeypatch, data):
    def download(url, path, headers, limit):
        path.write_text(json.dumps(data))

    monkeypatch.setattr(media, "_bounded_download", download)


def test_public_feed_extracts_only_requested_video_and_converts_milliseconds(tmp_path, monkeypatch):
    mock_json_download(monkeypatch, feed())
    info = media._public_feed_info("123456", tmp_path)
    assert info["id"] == "123456"
    assert info["duration"] == 862.9
    assert info["formats"][0]["url"] == "https://cdn.douyinvod.com/video.mp4"


@pytest.mark.parametrize(
    "data",
    [
        feed("999"),
        {"status_code": 1, "aweme_list": feed()["aweme_list"]},
        [],
        {"status_code": 0, "aweme_list": []},
        feed(play_addr={}),
        feed(duration=1801000),
        feed(duration=0),
        feed(play_addr={"url_list": ["https://evil.example/video.mp4"]}),
    ],
    ids=[
        "wrong-id",
        "status-error",
        "not-object",
        "empty",
        "no-media",
        "over-limit",
        "zero-duration",
        "bad-host",
    ],
)
def test_public_feed_rejects_wrong_or_invalid_media(tmp_path, monkeypatch, data):
    mock_json_download(monkeypatch, data)
    with pytest.raises(media.MediaError):
        media._public_feed_info("123456", tmp_path)


def test_standard_extractor_failure_uses_exact_id_public_fallback(tmp_path, monkeypatch):
    class Downloader:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, download):
            raise RuntimeError("Fresh cookies needed")

    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Downloader))
    monkeypatch.setattr(media, "_resolve_short_link", lambda url: "https://www.douyin.com/video/123456")
    calls = []

    def download(url, path, headers, limit):
        calls.append(url)
        if "/aweme/v1/feed/" in url:
            path.write_text(json.dumps(feed()))
        else:
            path.write_bytes(b"video")

    monkeypatch.setattr(media, "_bounded_download", download)
    path, info = media.download_video("https://www.douyin.com/video/123456", tmp_path)
    assert path.read_bytes() == b"video"
    assert info["id"] == "123456"
    assert len(calls) == 2


def test_feed_retries_second_known_endpoint_after_network_failure(tmp_path, monkeypatch):
    calls = []

    def download(url, path, headers, limit):
        calls.append(url)
        if len(calls) == 1:
            raise OSError("temporary network error")
        path.write_text(json.dumps(feed()))

    monkeypatch.setattr(media, "_bounded_download", download)
    assert media._public_feed_info("123456", tmp_path)["id"] == "123456"
    assert len(calls) == 2
