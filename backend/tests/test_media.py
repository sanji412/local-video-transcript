import json
import socket
import subprocess
import sys
import types
import urllib.error
import wave

import pytest
from app import media


def test_extract_single_share_link():
    assert media.extract_link("看看 https://v.douyin.com/abc/ 这个视频") == "https://v.douyin.com/abc/"
    assert (
        media.extract_link("https://www.douyin.com/video/123456。") == "https://www.douyin.com/video/123456"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "https://example.com/a",
        "https://douyin.com.evil.test/video/12",
        "https://user@www.douyin.com/video/12",
        "http://v.douyin.com/a/",
        "https://v.douyin.com:123/a/",
        "https://www.douyin.com/user/abc",
        "https://v.douyin.com/a/ https://v.douyin.com/b/",
        "https://v.douyin.com/a/ https://example.com/",
        "https://v.douyin.com/a/\nhttps://v.douyin.com/a/",
    ],
)
def test_reject_invalid_or_multiple_links(value):
    with pytest.raises(media.MediaError):
        media.extract_link(value)


def test_subtitles_require_whole_duration_and_chinese():
    full = "WEBVTT\n\n00:00.000 --> 00:05.000\n你好世界\n\n00:05.000 --> 00:10.000\n这是完整字幕\n"
    assert media.subtitle_text(full, 10) == "你好世界\n这是完整字幕"
    assert media.subtitle_text(full, 100) is None
    assert (
        media.subtitle_text(
            full.replace("你好世界", "hello world").replace("这是完整字幕", "this is English"), 10
        )
        is None
    )
    assert media.subtitle_text("broken subtitle", 10) is None


def test_subtitles_reject_gaps_overlaps_and_out_of_bounds():
    assert media.subtitle_text("00:00.000 --> 00:01.000\n你好\n\n00:09.000 --> 00:10.000\n你好", 10) is None
    assert media.subtitle_text("00:00.000 --> 00:10.000\n你好\n\n00:01.000 --> 00:05.000\n你好", 10) is None
    assert media.subtitle_text("00:00.000 --> 01:00.000\n你好", 10) is None


@pytest.mark.parametrize("duration", [1801, 0, float("nan"), float("inf")])
def test_probe_rejects_invalid_duration(tmp_path, monkeypatch, duration):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        media,
        "_command",
        lambda *a, **k: json.dumps(
            {
                "format": {"duration": str(duration)},
                "streams": [{"codec_type": "audio"}, {"codec_type": "video"}],
            }
        ),
    )
    with pytest.raises(media.MediaError):
        media.probe_media(path)


def test_probe_checks_audio_and_real_size(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        media,
        "_command",
        lambda *a, **k: json.dumps({"format": {"duration": "10"}, "streams": [{"codec_type": "video"}]}),
    )
    with pytest.raises(media.MediaError, match="音轨"):
        media.probe_media(path)
    monkeypatch.setattr(media, "MAX_MEDIA_BYTES", 1)
    with pytest.raises(media.MediaError, match="500"):
        media.probe_media(path)


def test_local_file_must_be_within_task_directory(tmp_path):
    outside = tmp_path / "original.mp4"
    outside.write_bytes(b"original")
    work = tmp_path / "task"
    work.mkdir()
    with pytest.raises(media.MediaError, match="目录"):
        media.run_pipeline(
            {
                "source": "file",
                "input": str(outside),
                "work_dir": str(work),
                "model_dir": str(tmp_path / "model"),
            },
            lambda e: None,
        )
    assert outside.read_bytes() == b"original"


def test_local_pipeline_transcribes_when_subtitles_are_partial(tmp_path, monkeypatch):
    path = tmp_path / "input.mp4"
    path.write_bytes(b"mp4")
    model = tmp_path / "model"
    monkeypatch.setattr(media, "probe_media", lambda p: 30.0)
    monkeypatch.setattr(media, "extract_audio", lambda p, w: w / "audio.wav")
    timed = {"text": "这是全文。", "segments": [{"id": 0, "start": 0.0, "end": 3.0, "text": "这是全文。"}]}
    monkeypatch.setattr(media, "transcribe_audio", lambda p, m, **kw: timed)
    result = media.run_pipeline(
        {"source": "file", "input": str(path), "work_dir": str(tmp_path), "model_dir": str(model)},
        lambda e: None,
    )
    assert result == {**timed, "source": "transcription"}


def test_silence_rejected_before_model_import(tmp_path):
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 32000)
    with pytest.raises(media.MediaError, match="语音"):
        media.transcribe_audio(path, tmp_path / "missing-model")


def test_transcription_filters_silent_low_confidence_segments():
    with pytest.raises(media.MediaError, match="语音"):
        media.transcript_text(
            {
                "text": "谢谢观看",
                "segments": [{"text": "谢谢观看", "no_speech_prob": 0.95, "avg_logprob": -1.5}],
            }
        )
    assert (
        media.transcript_text(
            {"segments": [{"text": "真实讲述。", "no_speech_prob": 0.02, "avg_logprob": -0.2}]}
        )
        == "真实讲述。"
    )


def test_network_guard_rejects_private_dns_and_unknown_domains(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with media.safe_network():
        with pytest.raises(media.MediaError):
            socket.getaddrinfo("v.douyin.com", 443)
        with pytest.raises(media.MediaError):
            socket.getaddrinfo("evil.test", 443)


def test_media_url_allows_official_cdn_and_rejects_arbitrary_url():
    media.validate_network_url("https://v3.douyinvod.com/video/a.mp4")
    with pytest.raises(media.MediaError):
        media.validate_network_url("https://example.com/a.mp4")
    with pytest.raises(media.MediaError):
        media.validate_network_url("file:///etc/passwd")


def test_command_timeout_is_actionable(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ffprobe", 30))
    )
    with pytest.raises(media.MediaError, match="超时"):
        media._command(["ffprobe"], timeout=30)


def test_bounded_download_rejects_unknown_length_oversize_before_write(tmp_path, monkeypatch):
    class Response:
        url = "https://v3.douyinvod.com/video"
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, amount):
            return b"0123456789"

    monkeypatch.setattr(media, "_opener", lambda: types.SimpleNamespace(open=lambda *a, **k: Response()))
    path = tmp_path / "download.mp4"
    with pytest.raises(media.MediaError, match="500"):
        media._bounded_download(Response.url, path, {}, 15)
    assert path.stat().st_size == 10


def test_short_link_rejects_redirect_before_opening_bad_host(monkeypatch):
    requests = []

    def redirect(request, **kwargs):
        requests.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url, 302, "redirect", {"Location": "https://evil.test/video/12"}, None
        )

    monkeypatch.setattr(media.urllib.request, "build_opener", lambda *a: types.SimpleNamespace(open=redirect))
    with pytest.raises(media.MediaError):
        media._resolve_short_link("https://v.douyin.com/abc/")
    assert len(requests) == 1


def test_long_share_page_is_canonicalized_without_generic_extractor():
    assert (
        media._resolve_short_link("https://www.iesdouyin.com/share/video/123456/?foo=1")
        == "https://www.douyin.com/video/123456"
    )


def test_full_subtitles_skip_transcription_but_partial_subtitles_do_not(tmp_path, monkeypatch):
    path = tmp_path / "download.mp4"
    path.write_bytes(b"video")
    info = {"subtitles": {"zh": [{"ext": "srt", "data": "1\n00:00:00,000 --> 00:00:10,000\n这是完整字幕"}]}}
    monkeypatch.setattr(media, "download_video", lambda *a: (path, info))
    monkeypatch.setattr(media, "probe_media", lambda p: 10.0)
    monkeypatch.setattr(media, "extract_audio", lambda p, w: w / "audio.wav")
    timed = {
        "text": "这是全段识别结果",
        "segments": [{"id": 0, "start": 0.0, "end": 10.0, "text": "这是全段识别结果"}],
    }
    monkeypatch.setattr(media, "transcribe_audio", lambda *a, **kw: timed)
    payload = {
        "source": "link",
        "input": "https://v.douyin.com/abc/",
        "work_dir": str(tmp_path),
        "model_dir": str(tmp_path / "model"),
    }
    result = media.run_pipeline(payload, lambda e: None)
    assert result["source"] == "subtitle"
    assert result["segments"] == [{"id": 0, "start": 0.0, "end": 10.0, "text": "这是完整字幕"}]
    monkeypatch.setattr(media, "probe_media", lambda p: 100.0)
    assert media.run_pipeline(payload, lambda e: None) == {"source": "transcription", **timed}


def test_timed_transcription_filters_text_and_timestamps_together():
    result = media.transcript_segments(
        {
            "segments": [
                {"start": 0.2, "end": 2.3, "text": " 第一段。 "},
                {"start": 2.3, "end": 4.0, "text": "幻听", "avg_logprob": -2},
                {"start": 4.1, "end": 6.2, "text": "第二段。"},
            ]
        },
        duration=6.0,
    )
    assert result == {
        "text": "第一段。\n第二段。",
        "segments": [
            {"id": 0, "start": 0.2, "end": 2.3, "text": "第一段。"},
            {"id": 1, "start": 4.1, "end": 6.0, "text": "第二段。"},
        ],
    }


@pytest.mark.parametrize("start,end", [(float("nan"), 2), (0, float("inf")), (-1, 2), (3, 2)])
def test_timed_transcription_rejects_invalid_times(start, end):
    with pytest.raises(media.MediaError, match="时间"):
        media.transcript_segments({"segments": [{"start": start, "end": end, "text": "正文"}]}, 5)


def test_timed_subtitles_preserve_real_cue_boundaries():
    content = "00:00.000 --> 00:05.000\n你好世界\n\n00:05.000 --> 00:10.000\n这是完整字幕"
    assert media.subtitle_text(content, 10, timed=True) == {
        "text": "你好世界\n这是完整字幕",
        "segments": [
            {"id": 0, "start": 0.0, "end": 5.0, "text": "你好世界"},
            {"id": 1, "start": 5.0, "end": 10.0, "text": "这是完整字幕"},
        ],
    }


def test_local_transcription_passes_existing_model_path_and_blocks_network(tmp_path, monkeypatch):
    path = tmp_path / "speech.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\xff\x0f\x00\xf0" * 16000)
    model = tmp_path / "model"
    model.mkdir()
    monkeypatch.setattr(media, "model_status", lambda p: {"status": "ready"})
    calls = []

    def recognize(audio, **options):
        calls.append(options)
        with pytest.raises(media.MediaError, match="离线"):
            socket.getaddrinfo("example.com", 443)
        return {"segments": [{"text": "本地转写"}]}

    monkeypatch.setitem(sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=recognize))
    assert media.transcribe_audio(path, model) == "本地转写"
    assert calls[0]["path_or_hf_repo"] == str(model.resolve())
    assert calls[0]["language"] == "zh"


def test_decoded_audio_longer_than_limit_rejected_before_model(tmp_path, monkeypatch):
    class Recording:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def getnframes(self):
            return 1801 * 16000

        def getframerate(self):
            return 16000

    monkeypatch.setattr(wave, "open", lambda *a: Recording())
    with pytest.raises(media.MediaError, match="30 分钟"):
        media.transcribe_audio(tmp_path / "long.wav", tmp_path / "model")


def test_probe_accepts_full_thirty_minutes(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"video")
    monkeypatch.setattr(
        media,
        "_command",
        lambda *a, **k: json.dumps(
            {"format": {"duration": "1800"}, "streams": [{"codec_type": "audio"}, {"codec_type": "video"}]}
        ),
    )
    assert media.probe_media(path) == 1800


@pytest.mark.parametrize(
    ("share_text", "expected"),
    [
        (
            "0.71 :1pm b@n.qR EHv:/ 07/18 学就完了，祝大家都是表达高手 # 表达 # 干货 "
            "https://v.douyin.com/BCVusZue21g/ 复制此链接，打开Dou音搜索，直接观看视频！",
            "学就完了，祝大家都是表达高手",
        ),
        (
            "2.82 SYM:/ 04/15 :6pm y@g.oD 我宁愿痛苦，我不要麻木   "
            "https://v.douyin.com/YxDhnVDo_AU/ 复制此链接，打开Dou音搜索，直接观看视频！",
            "我宁愿痛苦，我不要麻木",
        ),
        ("表达训练\n每天  练习 😊 #表达 https://v.douyin.com/abc/", "表达训练 每天 练习 😊"),
        ("2026 年的 3 个 AI 表达技巧！ https://v.douyin.com/abc/", "2026 年的 3 个 AI 表达技巧！"),
        ("AI 2026：C# 入门 https://v.douyin.com/abc/", "AI 2026：C# 入门"),
        ("0.71 的概率有多大 https://v.douyin.com/abc/", "0.71 的概率有多大"),
        ("07/18 表达练习 https://v.douyin.com/abc/", "07/18 表达练习"),
        ("0.71 :1pm b@n.qR EHv:/ 07/18 AI 2026 的表达 https://v.douyin.com/abc/", "AI 2026 的表达"),
        ("表达技巧#表达#干货 https://v.douyin.com/abc/", "表达技巧"),
        ("https://v.douyin.com/abc/ 复制此链接，打开抖音搜索，直接观看视频！", None),
        ("0.71 :1pm b@n.qR EHv:/ 07/18 https://v.douyin.com/abc/", None),
        ("#表达 #干货 https://v.douyin.com/abc/", None),
        ("文" * 210 + " https://v.douyin.com/abc/", "文" * 200),
    ],
)
def test_extract_share_title(share_text, expected):
    assert media.extract_share_title(share_text) == expected
