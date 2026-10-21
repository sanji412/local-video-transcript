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
