"""Bounded Douyin acquisition and offline local speech recognition."""

import array
import contextlib
import html
import ipaddress
import json
import math
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

from .limits import MAX_DURATION_SECONDS as MAX_DURATION
from .limits import MAX_MEDIA_BYTES
from .models import model_status

MAX_SUBTITLE_BYTES = 2 * 1024 * 1024
SOURCE_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "www.iesdouyin.com", "iesdouyin.com"}
NETWORK_DOMAINS = (
    "douyin.com",
    "iesdouyin.com",
    "douyinvod.com",
    "douyinpic.com",
    "byteoversea.com",
    "ibytedtos.com",
    "bytedance.com",
    "bytecdn.cn",
    "snssdk.com",
    "pstatp.com",
    "ixigua.com",
    "amemv.com",
    "byteimg.com",
)


class MediaError(ValueError):
    pass


def _valid_source(url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in SOURCE_HOSTS
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
        ):
            raise ValueError("url")
        pattern = r"/[A-Za-z0-9_-]+/?" if parsed.hostname == "v.douyin.com" else r"/(?:share/)?video/\d+/?"
        if not re.fullmatch(pattern, parsed.path):
            raise ValueError("path")
    except ValueError as error:
        raise MediaError("请提供单条抖音视频链接，支持抖音分享短链接或视频页链接") from error
    return url


def extract_link(text: str) -> str:
    links = re.findall(r'https?://[^\s<>"“”]+', str(text), flags=re.IGNORECASE)
    if len(links) != 1:
        raise MediaError("请粘贴包含一条抖音视频链接的分享内容，暂不支持多个链接")
    return _valid_source(links[0].rstrip("。，、！!？?；;：:）)]}"))


def extract_share_title(text: str) -> str | None:
    """Read the title before the URL; call after extract_link validates the share."""
    link = re.search(r"https?://", text, flags=re.IGNORECASE)
    if link is None:
        return None
    title = " ".join(text[: link.start()].split())
    # Only consume a numbered share prefix followed by recognizable code/date
    # tokens. Ordinary numbers, English words and punctuation belong to the title.
    code = r"(?:\d{1,2}/\d{1,2}|[A-Za-z0-9._-]+@[A-Za-z0-9._-]+|[A-Za-z0-9._-]+:/|:[A-Za-z0-9]+)"
    title = re.sub(rf"^\d+\.\d{{2}}\s+(?:{code}(?:\s+|$))+", "", title)
    # Keep programming language names such as C# while removing share hashtags.
    title = re.split(r"(?<![A-Za-z0-9])#", title, maxsplit=1)[0].strip()
    return title[:200].rstrip() or None


def _allowed_host(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in NETWORK_DOMAINS)


def validate_network_url(url: str) -> None:
    try:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or not _allowed_host(parsed.hostname)
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 80, 443)
        ):
            raise ValueError("network url")
    except ValueError as error:
        raise MediaError("视频地址或重定向不在受支持的抖音资源范围内，请改用本地 MP4") from error


@contextlib.contextmanager
def safe_network():
    """Guard every actual DNS resolution, including redirects, against SSRF.

    Used only inside the isolated, single-task worker. Checking returned addresses
    here avoids validating one resolution then connecting through a second one.
    """
    original = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        name = host.decode("ascii") if isinstance(host, bytes) else str(host)
        if not _allowed_host(name):
            raise MediaError("视频重定向到了未支持的域名，请改用本地 MP4")
        addresses = original(host, *args, **kwargs)
        if not addresses or any(
            not ipaddress.ip_address(item[4][0].split("%")[0]).is_global for item in addresses
        ):
            raise MediaError("视频地址解析到非公网地址，已停止访问")
        return addresses

    socket.getaddrinfo = resolve
    try:
        yield
    finally:
        socket.getaddrinfo = original


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_network_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def _opener():
    # Ignore ambient proxies: they bypass validation of the destination DNS.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _RedirectHandler())


def _resolve_short_link(url: str) -> str:
    _valid_source(url)
    if urllib.parse.urlsplit(url).hostname != "v.douyin.com":
        video_id = re.search(r"/video/(\d+)", urllib.parse.urlsplit(url).path).group(1)
        return f"https://www.douyin.com/video/{video_id}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(6):
        _valid_source(url)
        try:
            with opener.open(
                urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20
            ) as response:
                _valid_source(response.url)
                break
        except urllib.error.HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308) or not error.headers.get("Location"):
                raise
            url = urllib.parse.urljoin(url, error.headers["Location"])
            _valid_source(url)
            if urllib.parse.urlsplit(url).hostname != "v.douyin.com":
                break
    else:
        raise MediaError("抖音链接重定向次数过多，请改用本地 MP4")
    parsed = urllib.parse.urlsplit(url)
    match = re.search(r"/video/(\d+)", parsed.path)
    if not match:
        raise MediaError("无法从分享链接读取视频地址，请改用本地 MP4")
    return f"https://www.douyin.com/video/{match.group(1)}"


def _bounded_download(url: str, path: Path, headers: dict, limit: int) -> None:
    validate_network_url(url)
    deadline = time.monotonic() + 180
    allowed_headers = {
        key: value for key, value in headers.items() if key.lower() in ("user-agent", "referer", "accept")
    }
    with _opener().open(urllib.request.Request(url, headers=allowed_headers), timeout=20) as response:
        validate_network_url(response.url)
        size = response.headers.get("Content-Length")
        if size and int(size) > limit:
            raise MediaError("视频超过 500 MB 限制，或字幕文件过大")
        total = 0
        with path.open("wb") as output:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise MediaError("视频超过 500 MB 限制，或字幕文件过大")
                if time.monotonic() > deadline:
                    raise MediaError("视频下载超时，请检查网络或导入本地 MP4")
                output.write(chunk)
    if total == 0:
        raise MediaError("获取的视频文件为空，请改用本地 MP4")


def _public_feed_info(video_id: str, work: Path) -> dict:
    """Read anonymous public metadata; never accept a different recommended video."""
    for host in ("api5-normal-c-hl.amemv.com", "aweme.snssdk.com"):
        try:
            with safe_network():
                metadata = work / "feed.json"
                _bounded_download(
                    f"https://{host}/aweme/v1/feed/?aweme_id={video_id}&aid=1128",
                    metadata,
                    {"User-Agent": "Mozilla/5.0"},
                    MAX_SUBTITLE_BYTES,
                )
            data = json.loads(metadata.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("status_code") != 0:
                continue
            items = data.get("aweme_list")
            if not isinstance(items, list):
                continue
            item = next((v for v in items if isinstance(v, dict) and v.get("aweme_id") == video_id), None)
            if not item or not isinstance(item.get("video"), dict):
                continue
            video = item["video"]
            duration = float(video["duration"]) / 1000
            if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
                raise MediaError("视频时长无效或超过 30 分钟，请检查或裁剪后导入")
            address = video.get("play_addr") or {}
            size = int(address.get("data_size") or 0)
            if size > MAX_MEDIA_BYTES:
                raise MediaError("视频超过 500 MB 限制，请裁剪后再导入")
            formats = []
            for url in address.get("url_list") or []:
                validate_network_url(url)
                formats.append(
                    {
                        "url": url,
                        "ext": "mp4",
                        "protocol": urllib.parse.urlsplit(url).scheme,
                        "vcodec": "unknown",
                        "acodec": "unknown",
                    }
                )
            if formats:
                return {"id": video_id, "duration": duration, "formats": formats, "subtitles": {}}
        except MediaError as error:
            if "30 分钟" in str(error) or "500 MB" in str(error):
                raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            continue
    raise MediaError("抖音公开视频信息暂时无法获取，请重试或导入本地 MP4")


def download_video(url: str, work: Path) -> tuple[Path, dict]:
    try:
        import yt_dlp

        with safe_network():
            url = _resolve_short_link(url)
            options = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "skip_download": True,
                "socket_timeout": 20,
                "retries": 1,
                "extractor_retries": 1,
                "proxy": "",
                "cachedir": False,
                "writesubtitles": True,
                "subtitleslangs": ["zh.*"],
                "format": "best[ext=mp4][protocol=https]/best[ext=mp4][protocol=http]",
            }
            video_id = re.search(r"/video/(\d+)", urllib.parse.urlsplit(url).path).group(1)
            try:
                with yt_dlp.YoutubeDL(options) as downloader:
                    info = downloader.extract_info(url, download=False)
            except Exception:
                info = _public_feed_info(video_id, work)
            if info and str(info.get("id")) != video_id:
                raise MediaError("获取的视频与链接不一致，已停止处理，请重新复制链接")
            if not info or info.get("_type") in ("playlist", "multi_video"):
                raise MediaError("请提供单条视频链接，暂不支持合集和图文")
            if info.get("duration") and float(info["duration"]) > MAX_DURATION:
                raise MediaError("视频超过 30 分钟，请裁剪后再导入")
            formats = info.get("formats") or [info]
            candidates = [
                entry
                for entry in formats
                if entry.get("url")
                and entry.get("ext") == "mp4"
                and entry.get("vcodec") != "none"
                and entry.get("acodec") != "none"
                and entry.get("protocol", "https") in ("http", "https")
            ]
            if not candidates:
                raise MediaError("没有可直接获取的 MP4 音视频，请下载视频后本地导入")
            path = work / "download.mp4"
            for selected in candidates[:4]:
                try:
                    _bounded_download(
                        selected["url"],
                        path,
                        selected.get("http_headers") or info.get("http_headers") or {},
                        MAX_MEDIA_BYTES,
                    )
                    return path, info
                except (OSError, ValueError):
                    path.unlink(missing_ok=True)
            raise MediaError("视频资源下载失败，请检查网络、重试或导入本地 MP4")
    except MediaError:
        raise
    except Exception as error:
        raise MediaError(
            "抖音视频获取失败：链接可能失效、需要登录或被平台限制。请保存为 MP4 后导入；本工具不会读取浏览器 Cookie"
        ) from error


def _command(args: list[str], timeout: int) -> str:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as error:
        raise MediaError("缺少 FFmpeg 或 ffprobe，请安装后重新启动") from error
    except subprocess.TimeoutExpired as error:
        raise MediaError("音视频处理超时，请检查或裁剪视频后重试") from error
    if completed.returncode:
        raise MediaError("无法读取或转换视频，请确认文件是可播放的 MP4 且包含音轨")
    return completed.stdout


def probe_media(path: Path) -> float:
    if not path.is_file() or path.stat().st_size == 0:
        raise MediaError("视频文件为空或不存在")
    if path.stat().st_size > MAX_MEDIA_BYTES:
        raise MediaError("视频超过 500 MB 限制，请裁剪后再导入")
    try:
        result = json.loads(
            _command(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-show_format",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout=30,
            )
        )
        durations = [float(result["format"]["duration"])]
        durations.extend(
            float(stream["duration"]) for stream in result.get("streams", []) if stream.get("duration")
        )
        duration = max(durations)
        if not all(math.isfinite(value) and 0 < value <= MAX_DURATION for value in durations):
            raise ValueError("duration")
    except MediaError:
        raise
    except (ValueError, KeyError, TypeError) as error:
        raise MediaError("视频时长无效或超过 30 分钟，请检查或裁剪后导入") from error
    streams = result.get("streams", [])
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        raise MediaError("视频没有音轨，无法提取讲述内容")
    if not any(stream.get("codec_type") == "video" for stream in streams):
        raise MediaError("请选择包含画面和音轨的 MP4 视频")
    return duration


def extract_audio(path: Path, work: Path) -> Path:
    output = work / "audio.wav"
    _command(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-t",
            str(MAX_DURATION + 1),
            str(output),
        ],
        timeout=180,
    )
    return output


def _timestamp(text: str) -> float:
    components = text.replace(",", ".").split(":")
    return sum(float(value) * 60**index for index, value in enumerate(reversed(components)))


def subtitle_text(content: str, duration: float, *, timed=False) -> str | dict | None:
    """Accept only timed Chinese captions with near-continuous full coverage."""
    if not 0 < duration <= MAX_DURATION or len(content.encode("utf-8")) > MAX_SUBTITLE_BYTES:
        return None
    cues = []
    timestamp = r"(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3}"
    for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip()):
        match = re.search(rf"({timestamp})\s+-->\s+({timestamp})[^\n]*\n(.+)", block, flags=re.S)
        if not match:
            continue
        start, end = _timestamp(match[1]), _timestamp(match[2])
        text = html.unescape(re.sub(r"<[^>]*>", "", match[3])).strip()
        if text:
            cues.append((start, end, text))
    if not cues or cues[0][0] > min(2, 0.05 * duration) or cues[-1][1] < duration - min(2, 0.05 * duration):
        return None
    previous = 0.0
    covered = 0.0
    for start, end, text in cues:
        if (
            start < previous - 0.05
            or end <= start
            or end > duration + 0.5
            or start - previous > min(5, max(2, 0.05 * duration))
        ):
            return None
        covered += end - start
        previous = end
    text = "\n".join(cue[2] for cue in cues)
    letters = re.findall(r"\w", text)
    chinese = re.findall(r"[\u3400-\u9fff]", text)
    if covered / duration < 0.9 or len(chinese) / max(1, len(letters)) < 0.2:
        return None
    if timed:
        return {
            "text": text,
            "segments": [
                {"id": index, "start": start, "end": min(end, duration), "text": value}
                for index, (start, end, value) in enumerate(cues)
            ],
        }
    return text


def _subtitles(info: dict, duration: float, work: Path, *, timed=False) -> str | dict | None:
    candidates = {**info.get("automatic_captions", {}), **info.get("subtitles", {})}
    for language, entries in candidates.items():
        if not language.lower().startswith(("zh", "cmn")):
            continue
        for entry in entries:
            if entry.get("ext") not in ("vtt", "srt"):
                continue
            try:
                content = entry.get("data")
                if content is None and entry.get("url"):
                    with safe_network():
                        file = work / "caption.txt"
                        _bounded_download(
                            entry["url"], file, info.get("http_headers") or {}, MAX_SUBTITLE_BYTES
                        )
                    content = file.read_text(encoding="utf-8-sig")
                text = subtitle_text(content or "", duration, timed=timed)
                if text:
                    return text
            except (OSError, ValueError):
                continue
    return None


def _accepted_speech(result: dict):
    for segment in result.get("segments") or []:
        if segment.get("no_speech_prob", 0) >= 0.6 and segment.get("avg_logprob", -2) < -0.5:
            continue
        if segment.get("compression_ratio", 0) > 2.4 or segment.get("avg_logprob", 0) < -1.2:
            continue
        if text := segment.get("text", "").strip():
            yield segment, text


def transcript_text(result: dict) -> str:
    text = "\n".join(text for _, text in _accepted_speech(result)).strip()
    if not text:
        raise MediaError("未检测到可识别的语音，请使用人声清晰的视频")
    return text


def transcript_segments(result: dict, duration: float) -> dict:
    segments = []
    previous = 0.0
    for segment, text in _accepted_speech(result):
        start, end = segment.get("start"), segment.get("end")
        if (
            type(start) not in (int, float)
            or type(end) not in (int, float)
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end
        ):
            raise MediaError("识别时间信息无效，请重新提取视频")
        # Whisper may end a final token slightly past the audio boundary.
        start, end = max(start, previous), min(end, duration)
        if start >= end:
            continue
        segments.append({"id": len(segments), "start": start, "end": end, "text": text})
        previous = end
    if not segments:
        raise MediaError("未检测到可识别的语音，请使用人声清晰的视频")
    if len(segments) > 3000:
        raise MediaError("识别分段过多，请将视频分成较短片段后导入")
    return {"text": "\n".join(segment["text"] for segment in segments), "segments": segments}


@contextlib.contextmanager
def _offline_inference():
    original = socket.getaddrinfo
    previous = os.environ.get("HF_HUB_OFFLINE")

    def deny_network(*args, **kwargs):
        raise MediaError("离线识别期间禁止访问网络，请重新准备完整的本地模型")

    socket.getaddrinfo = deny_network
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        yield
    finally:
        socket.getaddrinfo = original
        if previous is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous


def transcribe_audio(audio: Path, model: Path, *, timed=False) -> str | dict:
    with wave.open(str(audio), "rb") as recording:
        duration = recording.getnframes() / recording.getframerate()
        if duration > MAX_DURATION:
            raise MediaError("实际音频超过 30 分钟，请裁剪后重新导入")
        samples = array.array("h", recording.readframes(recording.getnframes()))
    if not samples or math.sqrt(sum(value * value for value in samples) / len(samples)) < 12:
        raise MediaError("未检测到有效语音，视频可能是静音或音量过低")
    model = Path(model).resolve()
    if model_status(model)["status"] != "ready" or not model.is_dir():
        raise MediaError("本地模型尚未就绪，请先准备模型")
    try:
        with _offline_inference():
            import mlx_whisper

            result = mlx_whisper.transcribe(
                str(audio),
                path_or_hf_repo=str(model),
                language="zh",
                task="transcribe",
                verbose=None,
                temperature=0.0,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
                logprob_threshold=-1.0,
                compression_ratio_threshold=2.4,
            )
    except ImportError as error:
        raise MediaError("本地识别组件不可用，请在 Apple 芯片 Mac 上安装本地依赖") from error
    return transcript_segments(result, duration) if timed else transcript_text(result)


def run_pipeline(payload: dict, emit) -> dict:
    work = Path(payload["work_dir"]).resolve()
    if not work.is_dir():
        raise MediaError("任务目录不存在，请重新提交")
    info = {}
    if payload.get("source") == "link":
        emit({"type": "stage", "stage": "downloading", "detail": "正在获取抖音视频"})
        path, info = download_video(extract_link(payload["input"]), work)
    elif payload.get("source") == "file":
        path = Path(payload["input"]).resolve()
        if not path.is_relative_to(work) or path == work or path.suffix.lower() != ".mp4":
            raise MediaError("导入视频必须位于当前任务目录内且为 MP4 文件")
    else:
        raise MediaError("暂不支持此输入类型")
    emit({"type": "stage", "stage": "checking", "detail": "正在检查视频时长和音轨"})
    duration = probe_media(path)
    subtitles = _subtitles(info, duration, work, timed=True)
    emit({"type": "stage", "stage": "converting", "detail": "正在提取音轨"})
    audio = extract_audio(path, work)
    if subtitles:
        return {**subtitles, "source": "subtitle"}
    emit({"type": "stage", "stage": "transcribing", "detail": "正在本机识别完整音频，音视频不会上传"})
    return {**transcribe_audio(audio, Path(payload["model_dir"]), timed=True), "source": "transcription"}


def run_retry(payload: dict, emit) -> dict:
    """Crop the selected PCM frames locally; return a candidate, never edit history."""
    start, end = payload.get("start"), payload.get("end")
    if (
        type(start) not in (int, float)
        or type(end) not in (int, float)
        or not math.isfinite(start)
        or not math.isfinite(end)
        or not 0 <= start < end <= MAX_DURATION
    ):
        raise MediaError("片段时间无效，请重新打开文稿后重试")
    work = Path(payload["work_dir"]).resolve()
    input_path = Path(payload["input"])
    source = input_path.resolve()
    if input_path.is_symlink() or (work / "clip.wav").is_symlink():
        raise MediaError("回听音频不能是符号链接，请重新导入原视频")
    if not work.is_dir() or not source.is_relative_to(work) or source == work or source.suffix != ".wav":
        raise MediaError("回听音频必须位于当前任务目录内")
    clip = work / "clip.wav"
    emit({"type": "stage", "stage": "converting", "detail": "正在准备选中片段的音频"})
    try:
        with wave.open(str(source), "rb") as recording:
            rate, count = recording.getframerate(), recording.getnframes()
            if (recording.getnchannels(), recording.getsampwidth(), rate) != (1, 2, 16000):
                raise MediaError("回听音频格式无效，请重新导入原视频")
            if count > MAX_DURATION * rate or end > count / rate + 0.05:
                raise MediaError("片段时间超出回听音频范围，请重新导入原视频")
            first, last = int(start * rate), min(count, math.ceil(end * rate))
            if first >= last:
                raise MediaError("片段时间内没有音频，请重新导入原视频")
            recording.setpos(first)
            frames = recording.readframes(last - first)
            with wave.open(str(clip), "wb") as output:
                output.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                output.writeframes(frames)
    except (OSError, wave.Error, EOFError) as error:
        raise MediaError("回听音频无法读取，请重新导入原视频") from error
    emit({"type": "stage", "stage": "transcribing", "detail": "正在本机重新识别选中片段"})
    return {"text": transcribe_audio(clip, Path(payload["model_dir"])), "source": "transcription"}
