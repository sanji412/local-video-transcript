"""Exercise actual worker parent-death protection without an ASR model or network."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from app.config import Settings
from app.manager import JobManager


def running(pid):
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True
    ).stdout.strip()
    return bool(state and not state.startswith("Z"))


def test_api_crash_stops_worker_tree_before_new_server_accepts_jobs(tmp_path):
    wrapper = tmp_path / "controlled_pipeline.py"
    wrapper.write_text("""import sys,time,subprocess
from pathlib import Path
from app import worker
def pipeline(payload, emit):
    child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    Path(payload['work_dir'],'descendant.pid').write_text(str(child.pid))
    time.sleep(60)
    return {'text':'done','source':'transcription'}
worker.run_pipeline=pipeline
raise SystemExit(worker.main())
""")
    parent_script = tmp_path / "api_parent.py"
    parent_script.write_text("""import sys,time,json
from pathlib import Path
from app.config import Settings
from app.manager import JobManager
m=JobManager(Settings(data_dir=Path(sys.argv[1]),task_timeout=60),
    command_builder=lambda path:[sys.executable,sys.argv[2],str(path)])
t=m.reserve_task();m.start_reserved(t['id'],'file','controlled')
print(json.dumps({'worker':m._process.pid,'task':t['id']}),flush=True)
time.sleep(60)
""")
    data_dir = tmp_path / "data"
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    parent = subprocess.Popen(
        [sys.executable, str(parent_script), str(data_dir), str(wrapper)],
        stdout=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    worker_pid = None
    new_manager = None
    try:
        info = json.loads(parent.stdout.readline())
        worker_pid = info["worker"]
        old_work = data_dir / "tasks" / ("task-" + info["task"])
        descendant_file = old_work / "descendant.pid"
        deadline = time.monotonic() + 5
        while not descendant_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        descendant_pid = int(descendant_file.read_text())
        parent.kill()
        parent.wait(timeout=3)
        deadline = time.monotonic() + 5
        while new_manager is None and time.monotonic() < deadline:
            try:
                new_manager = JobManager(Settings(data_dir=data_dir))
            except RuntimeError:
                assert old_work.exists(), "Must not clean active worker files while lock is held"
                time.sleep(0.02)
        assert new_manager is not None, "Dead parent must not leave worker holding the instance lock"
        assert not running(worker_pid), "A new server must never overlap the crashed server worker"
        deadline = time.monotonic() + 2
        while running(descendant_pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not running(descendant_pid), "Parent death must stop FFmpeg/download descendants too"
        assert not old_work.exists()
        assert new_manager.reserve_task()["status"] == "running"
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        parent.stdout.close()
        if worker_pid:
            try:
                os.killpg(worker_pid, signal.SIGKILL)
            except OSError:
                pass
        if new_manager:
            new_manager.close()
