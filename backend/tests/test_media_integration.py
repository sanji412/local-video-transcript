"""Small real-FFmpeg checks; no model download, GPU, or external network."""

import array
import subprocess
import wave

import pytest
from app import media
from app.media import MediaError, extract_audio, probe_media, transcribe_audio


def test_real_silent_mp4_is_decoded_and_rejected_without_loading_a_model(tmp_path):
    original = tmp_path / "original.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x120:r=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(original),
        ],
        check=True,
        timeout=30,
    )
    assert 0.9 <= probe_media(original) <= 1.2
    work = tmp_path / "work"
    work.mkdir()
    audio = extract_audio(original, work)
    assert audio.stat().st_size > 1000
    with pytest.raises(MediaError, match="语音"):
        transcribe_audio(audio, tmp_path / "missing-model")
    assert original.exists()


def test_actual_corrupt_mp4_is_an_actionable_error(tmp_path):
    path = tmp_path / "corrupt.mp4"
    path.write_bytes(b"this is not video")
    with pytest.raises(MediaError, match="读取|转换|播放"):
        probe_media(path)


def test_retry_decodes_only_selected_wav_frames_without_modifying_source(tmp_path, monkeypatch):
    audio = tmp_path / "source.wav"
    frames = array.array("h", [1000] * 16000 + [2000] * 16000 + [3000] * 16000)
    with wave.open(str(audio), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(frames.tobytes())
    original = audio.read_bytes()

    def recognize(path, model):
        with wave.open(str(path), "rb") as clip:
            assert clip.getnframes() == 16000
            assert set(array.array("h", clip.readframes(16000))) == {2000}
        return "只识别中间一秒"

    monkeypatch.setattr(media, "transcribe_audio", recognize)
    result = media.run_retry(
        {
            "input": str(audio),
            "work_dir": str(tmp_path),
            "model_dir": str(tmp_path / "model"),
            "start": 1.0,
            "end": 2.0,
        },
        lambda e: None,
    )
    assert result["text"] == "只识别中间一秒" and result["source"] == "transcription"
    assert result["media_duration_seconds"] == 1.0 and result["recognition_seconds"] >= 0
    assert audio.read_bytes() == original


@pytest.mark.parametrize("start,end", [(-1, 2), (2, 1), (float("nan"), 2), (0, 1801), (True, 2)])
def test_retry_rejects_invalid_range_before_decode(tmp_path, start, end):
    with pytest.raises(MediaError, match="时间"):
        media.run_retry(
            {
                "input": str(tmp_path / "source.wav"),
                "work_dir": str(tmp_path),
                "model_dir": "unused",
                "start": start,
                "end": end,
            },
            lambda e: None,
        )


def test_retry_rejects_audio_outside_work_directory(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(MediaError, match="目录"):
        media.run_retry(
            {
                "input": str(tmp_path / "outside.wav"),
                "work_dir": str(work),
                "model_dir": "unused",
                "start": 0,
                "end": 1,
            },
            lambda e: None,
        )


@pytest.mark.parametrize("link_name", ["source.wav", "clip.wav"])
def test_retry_never_follows_audio_symlinks(tmp_path, monkeypatch, link_name):
    original = tmp_path / "original.wav"
    with wave.open(str(original), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 32000)
    saved = original.read_bytes()
    (tmp_path / link_name).symlink_to(original)
    if link_name == "clip.wav":
        (tmp_path / "source.wav").write_bytes(saved)
    monkeypatch.setattr(media, "transcribe_audio", lambda *a: "候选")
    with pytest.raises(MediaError, match="目录|链接"):
        media.run_retry(
            {
                "input": str(tmp_path / "source.wav"),
                "work_dir": str(tmp_path),
                "model_dir": "unused",
                "start": 0,
                "end": 1,
            },
            lambda e: None,
        )
    assert original.read_bytes() == saved
