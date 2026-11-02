import json
import sys
import time

import pytest
from app.config import Settings
from app.main import create_app
from app.manager import JobManager
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.manager.inspect_model",
        lambda path: {
            "status": "ready",
            "detail": "模型已就绪",
            "progress": None,
        },
    )
    settings = Settings(data_dir=tmp_path, max_upload_bytes=1024)
    manager = JobManager(settings)
    monkeypatch.setattr(manager, "start_reserved", lambda *args: None)
    with TestClient(create_app(settings, manager), base_url="http://127.0.0.1:8765") as test_client:
        yield test_client, manager


def auth(client):
    session = client.get("/api/session").json()
    return {"X-Session-Token": session["token"], "Origin": "http://127.0.0.1:8765"}


def test_session_and_mutations_require_token_and_same_origin(client):
    c, _ = client
    session = c.get("/api/session").json()
    assert session["model"]["status"] == "ready"
    assert c.post("/api/tasks/link", json={"share_text": "bad"}).status_code == 403
    headers = auth(c)
    headers["Origin"] = "https://evil.example"
    assert c.post("/api/tasks/link", headers=headers, json={"share_text": "bad"}).status_code == 403
    assert c.get("/api/session", headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.get("/api/session", headers={"Host": "evil.example"}).status_code == 400


def test_invalid_and_multiple_links_are_rejected(client):
    c, _ = client
    for text in ["bad", "https://example.com/x", "https://v.douyin.com/a/ https://v.douyin.com/b/"]:
        response = c.post("/api/tasks/link", headers=auth(c), json={"share_text": text})
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], str)


def test_share_title_is_saved_and_searchable_without_changing_source(client, monkeypatch):
    c, manager = client
    monkeypatch.setattr(manager, "start_reserved", JobManager.start_reserved.__get__(manager))
    event = json.dumps({"type": "result", "text": "这里只保存完整口播正文。", "source": "transcription"})
    monkeypatch.setattr(
        manager,
        "_command_builder",
        lambda payload: [
            sys.executable,
            "-c",
            "import json,sys; "
            "assert json.load(open(sys.argv[1]))['input'] == 'https://v.douyin.com/BCVusZue21g/'; "
            f"print({event!r})",
            str(payload),
        ],
    )
    response = c.post(
        "/api/tasks/link",
        headers=auth(c),
        json={
            "share_text": "0.71 :1pm b@n.qR EHv:/ 07/18 学就完了，祝大家都是表达高手 "
            "# 表达 # 干货 https://v.douyin.com/BCVusZue21g/ "
            "复制此链接，打开Dou音搜索，直接观看视频！"
        },
    )
    assert response.status_code == 202
    task_id = response.json()["id"]
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        task = c.get(f"/api/tasks/{task_id}").json()
        if task["status"] != "running":
            break
        time.sleep(0.02)
    assert task["status"] == "completed"
    record = c.get(f"/api/history/{task['history_id']}").json()
    assert record["title"] == "学就完了，祝大家都是表达高手"
    assert record["source_ref"] == "https://v.douyin.com/BCVusZue21g/"
    assert record["original_text"] == record["edited_text"] == "这里只保存完整口播正文。"
    assert c.get("/api/history", params={"q": "表达高手"}).json()["items"][0]["id"] == task_id


def test_task_submission_busy_cancel_and_retry(client):
    c, manager = client
    headers = auth(c)
    response = c.post("/api/tasks/link", headers=headers, json={"share_text": "https://v.douyin.com/abc/"})
    assert response.status_code == 202
    task = response.json()
    assert c.get(f"/api/tasks/{task['id']}").json()["status"] == "running"
    assert (
        c.post(
            "/api/tasks/link", headers=headers, json={"share_text": "https://v.douyin.com/abc/"}
        ).status_code
        == 409
    )
    assert c.post(f"/api/tasks/{task['id']}/cancel", headers=headers).json()["status"] == "cancelled"
    assert not manager.task_dir(task["id"]).exists()
    assert c.get("/api/tasks/not-an-id").status_code == 404


def test_upload_only_mp4_size_limited_and_uses_own_copy(client, tmp_path):
    c, manager = client
    headers = auth(c)
    assert c.post("/api/tasks/file", headers=headers, files={"file": ("x.mov", b"a")}).status_code == 422
    assert c.post("/api/tasks/file", headers=headers, files={"file": ("x.mp4", b"")}).status_code == 422
    assert (
        c.post("/api/tasks/file", headers=headers, files={"file": ("x.mp4", b"x" * 1025)}).status_code == 413
    )
    original = tmp_path / "original.mp4"
    original.write_bytes(b"video")
    with original.open("rb") as f:
        response = c.post("/api/tasks/file", headers=headers, files={"file": ("../../x.mp4", f, "video/mp4")})
    assert response.status_code == 202
    target = manager.task_dir(response.json()["id"]) / "input.mp4"
    assert target.read_bytes() == b"video"
    c.post(f"/api/tasks/{response.json()['id']}/cancel", headers=headers)
    assert original.read_bytes() == b"video"


def test_missing_model_prevents_task_with_next_step(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(
        "app.manager.inspect_model",
        lambda path: {
            "status": "missing",
            "detail": "请先下载模型",
            "progress": None,
        },
    )
    response = c.post("/api/tasks/link", headers=auth(c), json={"share_text": "https://v.douyin.com/abc/"})
    assert response.status_code == 409
    assert "模型" in response.json()["detail"]


def test_validation_errors_are_human_readable(client):
    c, _ = client
    response = c.post("/api/tasks/link", headers=auth(c), json={})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_static_app_served_without_exposing_api_or_paths(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>工作台</html>")
    settings = Settings(data_dir=tmp_path / "data", frontend_dir=dist)
    with TestClient(create_app(settings), base_url="http://127.0.0.1:8765") as c:
        assert "工作台" in c.get("/").text
        assert c.get("/api/nonexistent").status_code == 404
        assert c.get("/../../pyproject.toml").status_code == 404


@pytest.mark.parametrize(
    "content_type,body",
    [
        ("application/json", b'{"share_text":"' + b"x" * 40000 + b'"}'),
        (
            "multipart/form-data; boundary=test",
            b'--test\r\nContent-Disposition: form-data; name="file"; filename="x.mp4"\r\n'
            b"Content-Type: video/mp4\r\n\r\n" + b"x" * (2 * 1024 * 1024) + b"\r\n--test--\r\n",
        ),
    ],
    ids=["chunked-json", "chunked-multipart"],
)
def test_streaming_body_without_content_length_is_bounded(client, content_type, body):
    c, _ = client
    headers = {**auth(c), "Content-Type": content_type}
    endpoint = "/api/tasks/link" if content_type == "application/json" else "/api/tasks/file"
    response = c.post(endpoint, headers=headers, content=iter([body]))
    assert response.status_code == 413
    assert "过大" in response.json()["detail"]


def test_history_crud_revision_and_original_protection(client):
    c, manager = client
    manager.history.create(
        id="saved",
        source_type="file",
        source_ref="a.mp4",
        text="原始",
        source="transcription",
        mode="accurate",
    )
    assert c.get("/api/history").json()["items"][0]["id"] == "saved"
    assert c.get("/api/history?q=不存在").json() == {"items": []}
    assert c.get("/api/history/saved").json()["original_text"] == "原始"
    assert c.patch("/api/history/saved", json={"revision": 0, "edited_text": "修改"}).status_code == 403
    changed = c.patch(
        "/api/history/saved", headers=auth(c), json={"revision": 0, "edited_text": "修改", "title": "标题"}
    )
    assert changed.status_code == 200
    assert changed.json()["revision"] == 1
    assert changed.json()["original_text"] == "原始"
    assert (
        c.patch(
            "/api/history/saved", headers=auth(c), json={"revision": 0, "edited_text": "旧稿"}
        ).status_code
        == 409
    )
    for body in [
        {"edited_text": "无修订号"},
        {"revision": 1, "original_text": "篡改"},
        {"revision": 1, "title": "x" * 201},
        {"revision": 1, "edited_text": None},
        {"revision": "1", "title": "坏"},
    ]:
        assert c.patch("/api/history/saved", headers=auth(c), json=body).status_code == 422
    assert c.delete("/api/history/saved", headers=auth(c)).status_code == 204
    assert c.get("/api/history/saved").status_code == 404


def test_history_accepts_large_chinese_draft_without_relaxing_link_limit(client):
    c, manager = client
    manager.history.create(
        id="saved", source_type="file", source_ref="a.mp4", text="原文", source="subtitle", mode="fast"
    )
    text = "中文测试" * 50000
    result = c.patch("/api/history/saved", headers=auth(c), json={"revision": 0, "edited_text": text})
    assert result.status_code == 200
    assert result.json()["edited_text"] == text
    assert (
        c.patch(
            "/api/history/saved", headers=auth(c), json={"revision": 1, "edited_text": text + "多"}
        ).status_code
        == 422
    )


def test_mode_api_defaults_and_validation(client):
    c, manager = client
    session = c.get("/api/session").json()
    assert set(session["models"]) == {"fast", "accurate"}
    assert c.get("/api/model?mode=accurate").status_code == 200
    assert c.get("/api/model?mode=wrong").status_code == 422
    response = c.post(
        "/api/tasks/link",
        headers=auth(c),
        json={"share_text": "https://v.douyin.com/abc/", "mode": "accurate"},
    )
    assert response.status_code == 202
    assert response.json()["mode"] == "accurate"
    c.post(f"/api/tasks/{response.json()['id']}/cancel", headers=auth(c))
    response = c.post(
        "/api/tasks/file",
        headers=auth(c),
        data={"mode": "accurate"},
        files={"file": ("../../口播.mp4", b"video")},
    )
    assert response.json()["mode"] == "accurate"
    assert manager._source_info == ("file", "口播.mp4")


def test_history_remains_available_without_model_and_storage_errors_are_actionable(client, monkeypatch):
    import sqlite3

    c, manager = client
    monkeypatch.setattr(
        "app.manager.inspect_model",
        lambda path: {"status": "missing", "detail": "模型未准备", "progress": None},
    )
    assert c.get("/api/history").json() == {"items": []}

    def fail(q):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(manager.history, "list", fail)
    response = c.get("/api/history")
    assert response.status_code == 503
    assert "磁盘" in response.json()["detail"]
    assert "disk I/O" not in response.text


def test_segment_audio_range_and_clear_keep_the_manuscript(client):
    c, manager = client
    record = manager.history.create(
        id="timed",
        source_type="file",
        source_ref="a.mp4",
        text="甲\n乙",
        source="transcription",
        mode="accurate",
        audio_retained=True,
        segments=[dict(id=0, start=0, end=1, text="甲"), dict(id=1, start=1, end=2, text="乙")],
    )
    path = manager.history.audio_path("timed")
    path.parent.mkdir()
    path.write_bytes(b"RIFFtestaudio")
    response = c.get("/api/history/timed/audio", headers={"Range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == b"RIFF"
    assert response.headers["content-range"] == "bytes 0-3/13"
    assert c.delete("/api/history/timed/audio").status_code == 403
    cleared = c.delete("/api/history/timed/audio", headers=auth(c))
    assert cleared.status_code == 200 and cleared.json()["audio_available"] is False
    assert c.get("/api/history/timed/audio").status_code == 404
    assert cleared.json()["original_text"] == record["original_text"]
    assert cleared.json()["segments"] == record["segments"]


def test_segment_save_retry_validation_and_exclusion(client, monkeypatch):
    c, manager = client
    manager.history.create(
        id="timed",
        source_type="file",
        source_ref="a.mp4",
        text="甲\n乙",
        source="transcription",
        mode="accurate",
        segments=[dict(id=0, start=0, end=1, text="甲"), dict(id=1, start=1, end=2, text="乙")],
    )
    changed = c.patch(
        "/api/history/timed/segments/0", headers=auth(c), json={"revision": 0, "text": "校对甲"}
    )
    assert changed.status_code == 200 and changed.json()["edited_text"] == "校对甲\n乙"
    assert changed.json()["segments"][1]["text"] == "乙"
    assert (
        c.patch(
            "/api/history/timed/segments/0", headers=auth(c), json={"revision": 0, "text": "旧改"}
        ).status_code
        == 409
    )
    assert (
        c.patch(
            "/api/history/timed/segments/0", headers=auth(c), json={"revision": 1, "text": None}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/history/timed/segments/0/recognize",
            headers=auth(c),
            json={"revision": 1, "mode": "accurate"},
        ).status_code
        == 404
    )
    audio = manager.history.audio_path("timed")
    audio.parent.mkdir()
    audio.write_bytes(b"cache")
    payloads = []
    monkeypatch.setattr(manager, "_spawn", lambda payload, ident: payloads.append(payload))
    retry = c.post(
        "/api/history/timed/segments/0/recognize", headers=auth(c), json={"revision": 1, "mode": "accurate"}
    )
    assert retry.status_code == 202 and retry.json()["kind"] == "retry"
    assert retry.json()["history_id"] == "timed" and retry.json()["segment_id"] == 0
    assert payloads[0]["kind"] == "retry" and payloads[0]["start"] == 0
    assert c.delete("/api/history/timed", headers=auth(c)).status_code == 409
    assert c.delete("/api/history/timed/audio", headers=auth(c)).status_code == 409
    assert (
        c.post(
            "/api/tasks/link", headers=auth(c), json={"share_text": "https://v.douyin.com/abc/"}
        ).status_code
        == 409
    )
    assert c.post(f"/api/tasks/{retry.json()['id']}/cancel", headers=auth(c)).status_code == 200
    assert c.get("/api/history/timed").json()["edited_text"] == "校对甲\n乙"


def test_batch_api_preview_controls_and_limits(client):
    c, m = client
    headers = auth(c)
    assert c.post("/api/batches/preview", json={"share_text": "x"}).status_code == 403
    response = c.post(
        "/api/batches/preview",
        headers=headers,
        json={"share_text": "https://v.douyin.com/ABC/\nhttps://v.douyin.com/ABC/?foo=1"},
    )
    assert response.status_code == 200
    assert [x["status"] for x in response.json()["items"]] == ["ready", "duplicate"]
    assert (
        c.post("/api/batches/preview", headers=headers, json={"share_text": "中" * 200001}).status_code == 422
    )
    assert c.post("/api/tasks/link", headers=headers, json={"share_text": "中" * 12000}).status_code == 413
    response = c.post(
        "/api/batches", headers=headers, json={"items": [{"share_text": "https://v.douyin.com/ABC/"}]}
    )
    assert response.status_code == 202
    batch = response.json()
    assert c.get("/api/session").json()["batch"]["id"] == batch["id"]
    assert c.get("/api/batches/current").json()["batch"]["id"] == batch["id"]
    assert (
        c.post(f"/api/batches/{batch['id']}/actions", headers=headers, json={"action": "cancel"}).status_code
        == 200
    )


def test_duplicate_link_409_and_force_preserves_old_edits(client):
    c, m = client
    m.history.create(
        id="old",
        source_type="link",
        source_ref="https://v.douyin.com/AbC/",
        text="original",
        source="transcription",
        mode="fast",
    )
    m.history.update("old", revision=0, edited_text="edited")
    response = c.post(
        "/api/tasks/link", headers=auth(c), json={"share_text": "https://v.douyin.com/AbC/?track=1"}
    )
    assert response.status_code == 409
    assert response.json()["duplicate"]["id"] == "old"
    response = c.post(
        "/api/tasks/link", headers=auth(c), json={"share_text": "https://v.douyin.com/AbC/", "force": True}
    )
    assert response.status_code == 202
    assert m.history.get("old")["edited_text"] == "edited"
