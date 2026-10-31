"""Isolated worker; stdout is reserved for newline-delimited JSON events."""

import contextlib
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .media import MediaError, run_pipeline
from .models import ModelError, prepare_model


def _watch_parent(parent_pid):
    """Retain the instance lock and stop the entire worker session if the API dies."""
    if not isinstance(parent_pid, int) or parent_pid <= 1:
        return
    inherited_fd = os.environ.get("LOCAL_TRANSCRIPT_LOCK_FD")
    if inherited_fd is not None:
        os.set_inheritable(int(inherited_fd), False)

    def watchdog():
        while True:
            if os.getppid() != parent_pid:
                os.killpg(os.getpgrp(), signal.SIGKILL)
                return
            time.sleep(0.1)

    threading.Thread(target=watchdog, daemon=True).start()


def main(args=None, protocol=None) -> int:
    args = sys.argv[1:] if args is None else args
    protocol = sys.stdout if protocol is None else protocol

    def emit(event):
        protocol.write(json.dumps(event, ensure_ascii=False) + "\n")
        protocol.flush()

    try:
        with contextlib.redirect_stdout(sys.stderr):
            if len(args) != 1:
                raise MediaError("工作任务参数无效，请重新提交任务")
            payload = json.loads(Path(args[0]).read_text())
            _watch_parent(payload.get("parent_pid"))
            if payload.get("kind") == "model":
                if payload.get("mode", "fast") == "fast":
                    prepare_model(Path(payload["model_dir"]), emit)
                else:
                    prepare_model(Path(payload["model_dir"]), emit, mode=payload["mode"])
            elif payload.get("kind") == "task":
                emit({"type": "result", **run_pipeline(payload, emit)})
            else:
                raise MediaError("工作任务类型无效，请重新提交任务")
        return 0
    except (MediaError, ModelError) as error:
        emit({"type": "error", "detail": str(error)})
    except Exception:
        emit({"type": "error", "detail": "处理失败，请检查文件、模型和本地依赖后重试"})
    return 1


if __name__ == "__main__":
    # Also divert native-library writes to fd 1. Child processes inherit this and
    # the parent's process group, allowing cancellation to stop the entire tree.
    with os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1) as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        raise SystemExit(main(protocol=protocol))
