"""Small real-FFmpeg checks; no model download, GPU, or external network."""

import subprocess

import pytest
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
