"""Explicit model preparation. Inference never resolves a Hub repository name."""

import json
import os
import shutil
import tempfile
from pathlib import Path

MODEL_REPO = "mlx-community/whisper-large-v3-turbo"
MODEL_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
ACCURATE_MODEL_REPO = "mlx-community/whisper-large-v3-mlx"
ACCURATE_MODEL_REVISION = "49e6aa286ad60c14352c404340ded53710378a11"
MARKER = ".ready.json"
WEIGHTS = ("model.safetensors", "weights.safetensors", "weights.npz")


class ModelError(ValueError):
    pass


def _manifest(path: Path) -> dict:
    config = path / "config.json"
    json.loads(config.read_text())
    weight = next((path / name for name in WEIGHTS if (path / name).is_file()), None)
    if weight is None or not weight.stat().st_size:
        raise ValueError("missing weights")
    return {
        file.name: {"size": file.stat().st_size, "mtime_ns": file.stat().st_mtime_ns}
        for file in (config, weight)
    }


def model_profile(path: Path, mode=None):
    mode = mode or ("accurate" if Path(path).name == "whisper-accurate" else "fast")
    if mode == "accurate":
        return ACCURATE_MODEL_REPO, ACCURATE_MODEL_REVISION
    if mode != "fast":
        raise ModelError("无效的识别模式")
    return MODEL_REPO, MODEL_REVISION


def model_status(path: Path, mode=None) -> dict:
    path = Path(path)
    repo, revision = model_profile(path, mode)
    if not (path / MARKER).is_file():
        return {
            "status": "missing",
            "detail": "首次使用需下载精确识别模型（约 3.1 GB）"
            if repo == ACCURATE_MODEL_REPO
            else "首次使用需下载本地识别模型（约 1.6 GB）",
            "progress": None,
        }
    try:
        marker = json.loads((path / MARKER).read_text())
        if not isinstance(marker, dict) or marker.get("repo") != repo or marker.get("revision") != revision:
            raise ValueError("different model")
        if marker.get("files") != _manifest(path):
            raise ValueError("changed model files")
    except (OSError, ValueError, TypeError):
        return {"status": "error", "detail": "模型文件缺失或已改变，请重新下载模型", "progress": None}
    return {"status": "ready", "detail": "本地模型已就绪，可离线转写", "progress": 1.0}


def _download_model(path: Path, emit, mode="fast") -> None:
    repo, revision = model_profile(path, mode)
    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm

    class DownloadProgress(tqdm):
        def update(self, amount=1):
            result = super().update(amount)
            if self.total:
                emit(
                    {
                        "type": "model",
                        "status": "downloading",
                        "detail": f"正在下载模型文件：{min(self.n, self.total)}/{self.total}",
                        "progress": min(0.9, 0.9 * self.n / self.total),
                    }
                )
            return result

    snapshot_download(
        repo_id=repo,
        revision=revision,
        local_dir=str(path),
        allow_patterns=["config.json", "weights.npz" if mode == "accurate" else "weights.safetensors"],
        max_workers=2,
        tqdm_class=DownloadProgress,
    )


def _validate_model(path: Path) -> None:
    import mlx.core as mx
    from mlx_whisper.load_models import load_model

    # Existence is essential: mlx-whisper otherwise treats the argument as a Hub ID.
    if not path.is_dir():
        raise ModelError("模型目录不存在，请重新准备模型")
    model = load_model(str(path.resolve()), dtype=mx.float16)
    mx.eval(model.parameters())
    del model
    mx.clear_cache()


def prepare_model(path: Path, emit, mode="fast") -> None:
    repo, revision = model_profile(path, mode)
    path = Path(path).resolve()
    if model_status(path, mode)["status"] == "ready":
        emit({"type": "model", **model_status(path, mode)})
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # The API allows only one model worker. Recover abandoned downloads after a
    # cancelled worker without touching unrelated directories or following links.
    for abandoned in path.parent.glob(".model-preparing-*"):
        if abandoned.is_dir() and not abandoned.is_symlink():
            shutil.rmtree(abandoned)
    staging = Path(tempfile.mkdtemp(prefix=".model-preparing-", dir=path.parent))
    emit(
        {
            "type": "model",
            "status": "downloading",
            "detail": "正在下载精确识别模型（约 3.1 GB）"
            if mode == "accurate"
            else "正在下载本地识别模型（约 1.6 GB）",
            "progress": 0.0,
        }
    )
    try:
        if mode == "fast":
            _download_model(staging, emit)
        else:
            _download_model(staging, emit, mode=mode)
        _manifest(staging)
        emit(
            {
                "type": "model",
                "status": "downloading",
                "detail": "下载完成，正在验证模型可加载性",
                "progress": 0.95,
            }
        )
        _validate_model(staging)
        marker = {"repo": repo, "revision": revision, "files": _manifest(staging)}
        (staging / MARKER).write_text(json.dumps(marker))
        # Only tool-owned model storage is replaced. No task/user media enters this path.
        if path.exists():
            shutil.rmtree(path)
        os.replace(staging, path)
    except Exception as error:
        emit(
            {
                "type": "model",
                "status": "error",
                "detail": "模型准备失败，请检查网络、磁盘空间后重试",
                "progress": None,
            }
        )
        raise ModelError("模型下载或加载验证失败，请检查网络、磁盘空间后重试") from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    emit({"type": "model", **model_status(path, mode)})
