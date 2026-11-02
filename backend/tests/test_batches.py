import sqlite3
import sys
import time

import pytest
from app.config import Settings
from app.manager import BusyError, JobManager


def wait_batch(m):
    for _ in range(300):
        batch = m.batches.current()
        if batch["status"] != "running":
            return batch
        time.sleep(0.02)
    pytest.fail(str(batch))


def test_batch_serial_duplicates_metrics_and_restart(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("""import json,sys,time
p=json.load(open(sys.argv[1]));time.sleep(.1)
print(json.dumps(dict(type='result',text='text',source='transcription',video_id='123',recognition_seconds=.05,media_duration_seconds=10)))
""")
    settings = Settings(data_dir=tmp_path / "data")
    m = JobManager(settings, command_builder=lambda p: [sys.executable, str(worker), str(p)])
    try:
        b = m.batches.start(
            [
                dict(share_text="https://www.douyin.com/video/123"),
                dict(share_text="https://www.douyin.com/video/123?x=1"),
            ]
        )
        with pytest.raises(BusyError):
            m.reserve_task()
        b = wait_batch(m)
        assert [x["status"] for x in b["items"]] == ["completed", "skipped"]
        assert len(m.history.list()) == 1
        record = m.history.list()[0]
        assert record["recognition_seconds"] == 0.05
        assert record["total_seconds"] > 0
        assert m.history.find_duplicate("https://www.douyin.com/video/123?foo=2")["id"] == record["id"]
    finally:
        m.close()
    m = JobManager(settings)
    try:
        assert m.batches.current()["id"] == b["id"]
    finally:
        m.close()


def wait_until(predicate):
    for _ in range(300):
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    pytest.fail("condition not reached")


@pytest.fixture
def queue(tmp_path):
    worker = tmp_path / "worker.py"
    worker.write_text("""import json,sys,time
from pathlib import Path
p=json.load(open(sys.argv[1]));time.sleep(.2)
u=p['input']
if '/bad/' in u:
 print(json.dumps(dict(type='error',detail='bad video')))
elif '/other/' in u and '123' in p['known_video_ids'] and not p['force']:
 print(json.dumps(dict(type='duplicate',video_id='123')))
else:
 Path(p['work_dir'],'audio.wav').write_bytes(b'audio')
 print(json.dumps(dict(type='result',text='text',source='transcription',video_id='123',recognition_seconds=.1,media_duration_seconds=5)))
""")
    settings = Settings(data_dir=tmp_path / "data")
    m = JobManager(settings, command_builder=lambda p: [sys.executable, str(worker), str(p)])
    yield m
    m.close()


def inputs(*codes):
    return [dict(share_text=f"https://v.douyin.com/{code}/") for code in codes]


def test_batch_subtitle_items_report_that_no_model_was_called(queue, tmp_path):
    worker = tmp_path / "subtitle.py"
    worker.write_text(
        "import json; print(json.dumps(dict(type='result',text='字幕',source='subtitle',media_duration_seconds=5)))"
    )
    queue._command_builder = lambda payload: [sys.executable, str(worker)]
    queue.batches.start(inputs("subtitle"))
    item = wait_batch(queue)["items"][0]
    assert item["source"] == "subtitle"
    assert item["recognition_seconds"] is None


def test_failure_duplicate_and_force_continue_preserving_audio_and_edits(queue):
    m = queue
    m.batches.start(inputs("bad", "first", "other"))
    b = wait_batch(m)
    assert [x["status"] for x in b["items"]] == ["failed", "completed", "skipped"]
    completed = b["items"][1]
    assert b["items"][2]["history_id"] == completed["history_id"]
    m.history.update(completed["history_id"], revision=0, edited_text="edited")
    assert m.history.get(completed["history_id"])["audio_available"]
    m.batches.action(b["id"], "retry", b["items"][2]["id"], force=True)
    b = wait_batch(m)
    assert b["items"][2]["status"] == "completed"
    assert len(m.history.list()) == 2
    assert m.history.get(completed["history_id"])["edited_text"] == "edited"
    assert all(m.history.get(x["id"])["audio_available"] for x in m.history.list())
    m.reserve_task()
    assert not any(m.history.get(x["id"])["audio_available"] for x in m.history.list())


def test_pause_finishes_current_cancel_pending_resume_and_cancel_all(queue):
    m = queue
    b = m.batches.start(inputs("first", "second", "third"))
    wait_until(lambda: m.batches.current()["items"][0]["status"] == "running")
    m.batches.action(b["id"], "pause")
    with pytest.raises(BusyError):
        m.reserve_task()
    wait_until(lambda: m.batches.current()["items"][0]["status"] == "completed")
    b = m.batches.current()
    assert b["status"] == "paused"
    assert b["items"][1]["status"] == "pending"
    m.batches.action(b["id"], "cancel", b["items"][1]["id"])
    m.batches.action(b["id"], "resume")
    wait_until(lambda: m.batches.current()["items"][2]["status"] == "running")
    m.batches.action(b["id"], "cancel")
    wait_until(lambda: m.batches.current()["items"][2]["status"] == "cancelled")
    assert m._process is None
    assert m.reserve_task()["status"] == "running"


def test_restart_pauses_pending_marks_interrupted_failed(queue):
    m = queue
    b = m.batches.start(inputs("first", "second"))
    wait_until(lambda: m.batches.current()["items"][0]["status"] == "running")
    m.close()
    restarted = JobManager(m.settings, command_builder=m._command_builder)
    try:
        b = restarted.batches.current()
        assert b["status"] == "paused"
        assert [x["status"] for x in b["items"]] == ["failed", "pending"]
        assert b["items"][1]["total_seconds"] is None
        restarted.batches.action(b["id"], "resume")
        b = wait_batch(restarted)
        assert b["items"][1]["status"] == "completed"
        assert b["items"][1]["total_seconds"] < 2
    finally:
        restarted.close()


def test_retry_failed_only_after_queue_stops(queue):
    m = queue
    b = m.batches.start(inputs("bad", "first"))
    with pytest.raises(BusyError):
        m.batches.action(b["id"], "retry_failed")
    b = wait_batch(m)
    old_id = b["items"][0]["task_id"]
    m.batches.action(b["id"], "retry_failed")
    b = wait_batch(m)
    assert b["items"][0]["task_id"] != old_id
    assert b["items"][0]["status"] == "failed"
    assert b["items"][1]["status"] == "completed"


def test_resume_requested_while_current_item_finishes(queue):
    m = queue
    b = m.batches.start(inputs("first", "second"))
    wait_until(lambda: m.batches.current()["items"][0]["status"] == "running")
    m.batches.action(b["id"], "pause")
    m.batches.action(b["id"], "resume")
    assert wait_batch(m)["status"] == "completed"


def test_cancel_all_response_releases_slot_after_process_exit(queue):
    m = queue
    b = m.batches.start(inputs("first", "second"))
    wait_until(lambda: m.batches.current()["items"][0]["status"] == "running")
    b = m.batches.action(b["id"], "cancel")
    assert all(x["status"] == "cancelled" for x in b["items"])
    assert m._process is None
    assert m.reserve_task()["status"] == "running"


def test_failed_queue_save_does_not_create_or_start_a_batch(queue, monkeypatch):
    def fail():
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(queue.batches, "_save", fail)
    with pytest.raises(sqlite3.OperationalError):
        queue.batches.start(inputs("first"))
    assert queue.batches.current() is None
    assert queue.current_task() is None and queue._batch_owner is None


def test_failed_action_save_restores_running_queue_without_stuck_cancellation(queue, monkeypatch):
    b = queue.batches.start(inputs("first", "second"))
    wait_until(lambda: queue.batches.current()["items"][0]["status"] == "running")
    with monkeypatch.context() as patch:
        patch.setattr(
            queue.batches, "_save", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("disk full"))
        )
        with pytest.raises(sqlite3.OperationalError):
            queue.batches.action(b["id"], "cancel")
        assert queue.batches.current()["status"] == "running"
        assert not queue.batches._cancelling
    assert wait_batch(queue)["status"] == "completed"


def test_restart_during_cancellation_finalizes_running_item(queue):
    b = queue.batches.start(inputs("first", "second"))
    wait_until(lambda: queue.batches.current()["items"][0]["status"] == "running")
    queue.batches.close()
    with queue._lock:
        queue.batches.batch["status"] = "cancelled"
        queue.batches.batch["items"][1]["status"] = "cancelled"
        queue.batches._save()
    queue.close()
    restarted = JobManager(queue.settings, command_builder=queue._command_builder)
    try:
        restored = restarted.batches.current()
        assert restored["id"] == b["id"] and restored["status"] == "cancelled"
        assert all(x["status"] == "cancelled" for x in restored["items"])
        assert restarted.reserve_task()["status"] == "running"
    finally:
        restarted.close()
