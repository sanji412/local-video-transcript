"""Single-job lifecycle, isolated worker protocol and process-group cleanup."""

import fcntl
import json
import os
import selectors
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .config import Settings
from .history import HistoryStore


def inspect_model(path: Path) -> dict:
    from .models import model_status

    return model_status(path)


class BusyError(Exception):
    pass


def stop_process_group(process: subprocess.Popen) -> None:
    """Kill the whole session, even when the worker has already exited."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            raise
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        if process.poll() is None:
            raise
    process.wait(timeout=3)


class JobManager:
    def __init__(self, settings: Settings, command_builder=None):
        self.settings = settings
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._lockfile = (settings.data_dir / "server.lock").open("a+")
        try:
            fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lockfile.close()
            raise RuntimeError("已有一个本地服务正在运行，请使用已打开的工作台。") from None
        self._lock = threading.RLock()
        self._task = None
        self._process = None
        self._thread = None
        self._model = None
        self._model_mode = "fast"
        self.history = HistoryStore(settings.data_dir / "history.sqlite3")
        self._closed = False
        self._cancelled = set()
        self._command_builder = command_builder or (
            lambda payload: [sys.executable, "-m", "app.worker", str(payload)]
        )
        settings.tasks_dir.mkdir(parents=True, exist_ok=True)
        for entry in settings.tasks_dir.iterdir():
            if entry.name.startswith(("task-", "model-")):
                self._remove_directory(entry)

    @staticmethod
    def _remove_directory(path):
        if path.is_symlink():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)

    def task_dir(self, task_id: str) -> Path:
        if not task_id or any(c not in "0123456789abcdef" for c in task_id):
            raise KeyError(task_id)
        return self.settings.tasks_dir / f"task-{task_id}"

    def model_state(self, mode="fast") -> dict:
        with self._lock:
            if self._model is not None and self._model_mode == mode:
                return dict(self._model)
        return inspect_model(self.settings.model_path(mode))

    def current_task(self):
        with self._lock:
            return dict(self._task) if self._task else None

    def get_task(self, task_id: str) -> dict:
        with self._lock:
            if not self._task or self._task["id"] != task_id:
                raise KeyError(task_id)
            return dict(self._task)

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            return bool(
                self._task
                and self._task["id"] == task_id
                and self._task["status"] == "running"
                and task_id not in self._cancelled
            )

    def _ensure_available(self):
        if self._closed:
            raise BusyError("服务正在关闭，请重新启动。")
        if (self._task and self._task["status"] == "running") or self._process is not None:
            raise BusyError("已有任务正在处理，请等待完成或取消当前任务。")

    def reserve_task(self, mode="fast", source_type=None, source_ref=None, *, source_title=None) -> dict:
        with self._lock:
            self._ensure_available()
            self.settings.model_path(mode)
            self._source_info = (source_type, source_ref)
            self._source_title = source_title
            task_id = uuid.uuid4().hex
            self.task_dir(task_id).mkdir()
            self._task = {
                "id": task_id,
                "mode": mode,
                "status": "running",
                "stage": "preparing",
                "detail": "正在准备素材",
                "text": "",
                "source": None,
                "error": None,
            }
            return dict(self._task)

    def start_reserved(self, task_id: str, source: str, input_value: str):
        with self._lock:
            if not self.is_running(task_id):
                raise ValueError("任务已取消，请重新提交。")
            if not self._source_info[0]:
                self._source_info = (source, Path(input_value).name if source == "file" else input_value)
            mode = self._task.get("mode", "fast")
            self._spawn(
                {
                    "kind": "task",
                    "mode": mode,
                    "source": source,
                    "input": input_value,
                    "work_dir": str(self.task_dir(task_id)),
                    "model_dir": str(self.settings.model_path(mode)),
                    "max_bytes": self.settings.max_upload_bytes,
                    "max_duration": self.settings.max_duration,
                },
                task_id,
            )

    def start_model_download(self, mode="fast") -> dict:
        with self._lock:
            self._ensure_available()
            if inspect_model(self.settings.model_path(mode))["status"] == "ready":
                self._model = None
                return self.model_state(mode)
            work = self.settings.tasks_dir / f"model-{uuid.uuid4().hex}"
            work.mkdir()
            self._model_mode = mode
            self._model = {"status": "downloading", "detail": "正在准备模型下载", "progress": None}
            try:
                self._spawn(
                    {
                        "kind": "model",
                        "mode": mode,
                        "work_dir": str(work),
                        "model_dir": str(self.settings.model_path(mode)),
                    },
                    None,
                )
            except Exception:
                self._remove_directory(work)
                self._model = {"status": "error", "detail": "无法启动模型准备，请重试。", "progress": None}
                raise
            return dict(self._model)

    def _spawn(self, payload, task_id):
        work = Path(payload["work_dir"])
        payload["parent_pid"] = os.getpid()
        payload_path = work / "payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        env["PYTHONUNBUFFERED"] = "1"
        env["LOCAL_TRANSCRIPT_LOCK_FD"] = str(self._lockfile.fileno())
        self._process = subprocess.Popen(
            self._command_builder(payload_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
            pass_fds=(self._lockfile.fileno(),),
        )
        self._thread = threading.Thread(
            target=self._watch,
            args=(self._process, payload, task_id),
            daemon=True,
        )
        self._thread.start()

    def _watch(self, process, payload, task_id):
        result = None
        error = None
        timeout = self.settings.task_timeout if task_id else self.settings.model_timeout
        if task_id and payload.get("mode") == "accurate":
            timeout = self.settings.accurate_task_timeout
        deadline = time.monotonic() + timeout
        buffer = b""
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                eof = False
                while not eof:
                    if time.monotonic() >= deadline:
                        error = (
                            "处理超时，请重试或使用较短的视频。"
                            if task_id
                            else "模型下载超时，请检查网络后重试。"
                        )
                        break
                    for key, _ in selector.select(timeout=0.2):
                        chunk = os.read(key.fileobj.fileno(), 65536)
                        if not chunk:
                            eof = True
                            break
                        buffer += chunk
                        if len(buffer) > 2 * 1024 * 1024:
                            raise ValueError("Worker event too large")
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            event = json.loads(line)
                            event_type = event.get("type")
                            with self._lock:
                                if event_type == "stage" and task_id and self.is_running(task_id):
                                    self._task.update(stage=event["stage"], detail=event["detail"])
                                elif event_type == "model" and task_id is None:
                                    self._model = {
                                        "status": "downloading",
                                        "detail": event.get("detail", "正在准备模型"),
                                        "progress": event.get("progress"),
                                    }
                                elif event_type == "result":
                                    if (
                                        not isinstance(event.get("text"), str)
                                        or not event["text"].strip()
                                        or event.get("source") not in ("subtitle", "transcription")
                                    ):
                                        raise ValueError("Invalid transcription result")
                                    result = {"text": event["text"], "source": event["source"]}
                                elif event_type == "error":
                                    error = str(event.get("detail", "处理失败，请重试。"))
                    if self._closed or (task_id and task_id in self._cancelled):
                        break
            if error is None and not self._closed and not (task_id and task_id in self._cancelled):
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
                if process.returncode != 0:
                    error = "处理进程意外退出，请重试。"
        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired):
            error = "处理进程异常，请重试；仍失败时可重新启动工具。"
        finally:
            stop_process_group(process)
            process.stdout.close()
            try:
                self._remove_directory(Path(payload["work_dir"]))
            except OSError:
                error = "临时文件清理失败，请检查磁盘权限后重新启动工具。"
            with self._lock:
                if task_id:
                    if task_id in self._cancelled or self._closed:
                        self._task.update(status="cancelled", stage="cancelled", detail="任务已取消")
                    elif error or not result:
                        self._task.update(
                            status="failed",
                            stage="failed",
                            detail="提取失败",
                            error=error or "没有获得有效文案，请检查视频是否包含清晰语音。",
                        )
                    else:
                        try:
                            record = self.history.create(
                                id=task_id,
                                source_type=self._source_info[0],
                                source_ref=self._source_info[1],
                                text=result["text"],
                                source=result["source"],
                                mode=self._task["mode"],
                                title=self._source_title,
                            )
                        except (OSError, sqlite3.Error, ValueError):
                            self._task.update(
                                status="failed",
                                stage="failed",
                                detail="保存失败",
                                error="文案保存失败，请检查磁盘空间和权限后重试。",
                            )
                        else:
                            self._task.update(
                                status="completed",
                                stage="completed",
                                detail="文案提取完成",
                                history_id=record["id"],
                                **result,
                            )
                elif error:
                    self._model = {"status": "error", "detail": error, "progress": None}
                else:
                    self._model = None
                if self._process is process:
                    self._process = None

    def fail_reserved(self, task_id, detail):
        with self._lock:
            if self.is_running(task_id):
                self._remove_directory(self.task_dir(task_id))
                self._task.update(status="failed", stage="failed", detail="素材准备失败", error=detail)

    def cancel(self, task_id):
        with self._lock:
            task = self.get_task(task_id)
            if task["status"] != "running":
                return task
            self._cancelled.add(task_id)
            process = self._process
            thread = self._thread if process else None
        if process:
            stop_process_group(process)
        if thread:
            thread.join(timeout=6)
            if thread.is_alive():
                raise RuntimeError("任务仍在停止中，请稍后重试。")
        with self._lock:
            self._remove_directory(self.task_dir(task_id))
            cancelled_task = dict(task, status="cancelled", stage="cancelled", detail="任务已取消")
            if self._task and self._task["id"] == task_id:
                self._task.update(cancelled_task)
            return cancelled_task

    def delete_history(self, record_id):
        with self._lock:
            self.history.delete(record_id)
            if self._task and self._task.get("history_id") == record_id:
                self._task = None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            task = self.current_task()
        if process:
            stop_process_group(process)
            self._thread.join(timeout=6)
        elif task and task["status"] == "running":
            self._remove_directory(self.task_dir(task["id"]))
        fcntl.flock(self._lockfile, fcntl.LOCK_UN)
        self._lockfile.close()
