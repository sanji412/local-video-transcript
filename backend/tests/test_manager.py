import os
import sys
import threading
import time

import pytest
from app.config import Settings
from app.manager import BusyError, JobManager, stop_process_group


def wait_for(manager, task_id, status, timeout=6):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        task = manager.get_task(task_id)
        if task["status"] == status:
            return task
        time.sleep(0.02)
    pytest.fail(f"Task did not reach {status}: {manager.get_task(task_id)}")


@pytest.fixture
def runner(tmp_path):
    worker = tmp_path / "fake_worker.py"
    worker.write_text("""import json,sys,time,subprocess,os
p=json.load(open(sys.argv[1]))
def emit(e): print(json.dumps(e), flush=True)
emit({'type':'stage','stage':'transcribing','detail':'正在本地转写'})
if p['input']=='fail':
    emit({'type':'error','detail':'无法识别有效语音'})
elif p['input']=='hang':
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    open(p['work_dir']+'/child.pid','w').write(str(child.pid))
    time.sleep(60)
else:
    time.sleep(.08)
    emit({'type':'result','text':'完整文案。','source':'transcription'})
""")
    settings = Settings(data_dir=tmp_path / "data", task_timeout=2)
    manager = JobManager(
        settings, command_builder=lambda payload: [sys.executable, str(worker), str(payload)]
    )
    yield manager
    manager.close()


def test_completed_result_survives_media_cleanup(runner):
    task = runner.reserve_task()
    work = runner.task_dir(task["id"])
    (work / "input.mp4").write_bytes(b"original")
    runner.start_reserved(task["id"], "file", "ok")
    result = wait_for(runner, task["id"], "completed")
    assert result["text"] == "完整文案。"
    assert result["source"] == "transcription"
    assert not work.exists()


def test_single_task_and_retry_after_failure(runner):
    first = runner.reserve_task()
    with pytest.raises(BusyError):
        runner.reserve_task()
    runner.start_reserved(first["id"], "file", "fail")
    failed = wait_for(runner, first["id"], "failed")
    assert failed["error"] == "无法识别有效语音"
    assert not runner.task_dir(first["id"]).exists()
    assert runner.reserve_task()["id"] != first["id"]


def test_cancel_stops_entire_process_group_and_cleans(runner):
    task = runner.reserve_task()
    work = runner.task_dir(task["id"])
    runner.start_reserved(task["id"], "file", "hang")
    end = time.monotonic() + 4
    while not (work / "child.pid").exists() and time.monotonic() < end:
        time.sleep(0.02)
    child_pid = int((work / "child.pid").read_text())
    runner.cancel(task["id"])
    assert runner.get_task(task["id"])["status"] == "cancelled"
    assert not work.exists()
    end = time.monotonic() + 3
    while time.monotonic() < end:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("Child process still alive after cancel")


def test_timeout_returns_actionable_failure(runner):
    task = runner.reserve_task()
    runner.start_reserved(task["id"], "file", "hang")
    result = wait_for(runner, task["id"], "failed")
    assert "超时" in result["error"]
    assert not runner.task_dir(task["id"]).exists()


def test_reserved_upload_can_be_cancelled(runner):
    task = runner.reserve_task()
    runner.cancel(task["id"])
    assert runner.get_task(task["id"])["status"] == "cancelled"
    with pytest.raises(ValueError):
        runner.start_reserved(task["id"], "file", "ok")


def test_startup_only_cleans_owned_task_dirs(tmp_path):
    settings = Settings(data_dir=tmp_path)
    root = tmp_path / "tasks"
    root.mkdir()
    (root / "task-old").mkdir()
    (root / "keep-user-file.mp4").write_text("original")
    manager = JobManager(settings)
    try:
        assert not (root / "task-old").exists()
        assert (root / "keep-user-file.mp4").read_text() == "original"
    finally:
        manager.close()


def test_second_server_cannot_cleanup_active_server(tmp_path):
    first = JobManager(Settings(data_dir=tmp_path))
    try:
        with pytest.raises(RuntimeError, match="运行"):
            JobManager(Settings(data_dir=tmp_path))
    finally:
        first.close()


def test_unaddressable_exited_process_group_is_already_stopped(monkeypatch):
    class ExitedProcess:
        pid = 12345678

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def denied(*args):
        raise PermissionError("process group no longer addressable")

    monkeypatch.setattr(os, "killpg", denied)
    stop_process_group(ExitedProcess())


def test_cancel_finishing_does_not_cancel_newly_reserved_task(runner):
    old = runner.reserve_task()
    runner.start_reserved(old["id"], "file", "hang")
    real_thread = runner._thread
    stopped, release = threading.Event(), threading.Event()

    class JoinGate:
        def join(self, timeout=None):
            real_thread.join(timeout)
            stopped.set()
            release.wait(3)

        def is_alive(self):
            return real_thread.is_alive()

    runner._thread = JoinGate()
    cancellation = threading.Thread(target=runner.cancel, args=(old["id"],))
    cancellation.start()
    try:
        assert stopped.wait(4)
        new = runner.reserve_task()
    finally:
        release.set()
        cancellation.join(4)
    assert runner.get_task(new["id"])["status"] == "running"


def test_completion_is_durable_before_publishing_and_records_profile(runner):
    task = runner.reserve_task("accurate", "file", "口播.mp4")
    runner.start_reserved(task["id"], "file", "ok")
    result = wait_for(runner, task["id"], "completed")
    record = runner.history.get(result["history_id"])
    assert record["original_text"] == result["text"] == record["edited_text"]
    assert record["mode"] == "accurate"
    assert record["source_ref"] == "口播.mp4"


def test_link_title_is_not_reused_for_the_next_task(runner):
    url = "https://v.douyin.com/abc/"
    task = runner.reserve_task("accurate", "link", url, source_title="表达技巧")
    runner.start_reserved(task["id"], "link", url)
    completed = wait_for(runner, task["id"], "completed")
    assert runner.history.get(completed["history_id"])["title"] == "表达技巧"
    task = runner.reserve_task("fast", "link", url)
    runner.start_reserved(task["id"], "link", url)
    completed = wait_for(runner, task["id"], "completed")
    assert runner.history.get(completed["history_id"])["title"] == "链接文案"


def test_history_write_failure_never_publishes_completion(runner, monkeypatch):
    def fail(**kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(runner.history, "create", fail)
    task = runner.reserve_task()
    runner.start_reserved(task["id"], "file", "ok")
    result = wait_for(runner, task["id"], "failed")
    assert "保存" in result["error"]
    assert runner.history.list() == []
    assert not runner.task_dir(task["id"]).exists()


def test_accurate_payload_uses_separate_directory_and_mode(runner, monkeypatch):
    payloads = []
    monkeypatch.setattr(runner, "_spawn", lambda payload, task_id: payloads.append(payload))
    task = runner.reserve_task("accurate")
    runner.start_reserved(task["id"], "link", "https://v.douyin.com/abc/")
    assert payloads[0]["mode"] == "accurate"
    assert payloads[0]["model_dir"].endswith("/models/whisper-accurate")


def test_deleting_current_completed_history_clears_stale_task(runner):
    task = runner.reserve_task()
    runner.start_reserved(task["id"], "file", "ok")
    completed = wait_for(runner, task["id"], "completed")
    runner.delete_history(completed["history_id"])
    assert runner.current_task() is None
    assert runner.history.list() == []


def test_either_model_download_excludes_task_and_other_model(runner, monkeypatch):
    monkeypatch.setattr(
        "app.manager.inspect_model", lambda path: {"status": "missing", "detail": "缺失", "progress": None}
    )
    monkeypatch.setattr(runner, "_spawn", lambda *args: setattr(runner, "_process", object()))
    try:
        state = runner.start_model_download("accurate")
        assert state["status"] == "downloading"
        assert runner.model_state("accurate")["status"] == "downloading"
        assert runner.model_state("fast")["status"] == "missing"
        with pytest.raises(BusyError):
            runner.reserve_task()
        with pytest.raises(BusyError):
            runner.start_model_download("fast")
    finally:
        runner._process = None


def test_retry_keeps_history_and_preserves_temporary_audio(runner):
    m = runner
    record = m.history.create(
        id="proof",
        source_type="file",
        source_ref="x.mp4",
        text="原稿",
        source="transcription",
        mode="fast",
        segments=[dict(id=0, start=0, end=1, text="原稿")],
    )
    audio = m.history.audio_path("proof")
    audio.parent.mkdir()
    audio.write_bytes(b"cached audio")
    task = m.retry_segment("proof", 0, record["revision"], "fast")
    result = wait_for(m, task["id"], "completed")
    assert result["kind"] == "retry"
    assert result["history_id"] == "proof"
    assert m.history.get("proof")["edited_text"] == "原稿"
    assert audio.exists()
    m.reserve_task()
    assert not audio.exists()


def test_clear_audio_blocked_while_reserved(runner):
    runner.reserve_task()
    with pytest.raises(BusyError):
        runner.clear_audio("missing")
