"""Same-origin local HTTP application and bounded upload entry points."""

import secrets
import shutil
import sqlite3
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .history import HistoryConflict
from .manager import BusyError, JobManager

Mode = Literal["fast", "accurate"]


class LinkInput(BaseModel):
    mode: Mode = "fast"
    share_text: str = Field(min_length=1, max_length=10000)


class HistoryPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0, strict=True)
    edited_text: str | None = Field(default=None, max_length=200000)
    title: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("edited_text", "title", mode="before")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("保存字段不能为 null")
        return value


class BodyLimitExceeded(HTTPException):
    def __init__(self):
        super().__init__(413, "文件或输入内容过大，请使用较小的视频。")


class LocalAccessMiddleware:
    def __init__(self, app, token: str, max_bytes: int):
        self.app, self.token, self.max_bytes = app, token, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode("latin-1")
        try:
            parsed_host = urlsplit("http://" + host)
            valid_host = (
                parsed_host.hostname in ("127.0.0.1", "localhost")
                and parsed_host.username is None
                and parsed_host.password is None
                and not parsed_host.path
                and not parsed_host.query
                and not parsed_host.fragment
            )
            _ = parsed_host.port
        except ValueError:
            valid_host = False
        if not valid_host:
            return await JSONResponse({"detail": "仅允许通过本机地址访问。"}, status_code=400)(
                scope, receive, send
            )
        origin = headers.get(b"origin", b"").decode("latin-1")
        allowed = {f"http://{host}"}
        # Vite development server proxies /api to the local API.
        if host in ("127.0.0.1:8765", "localhost:8765"):
            allowed.add("http://127.0.0.1:5173")
            allowed.add("http://localhost:5173")
        if origin and origin not in allowed:
            return await JSONResponse({"detail": "请求来源不受信任，请从本地工作台重试。"}, status_code=403)(
                scope, receive, send
            )
        if scope["path"].startswith("/api/") and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            token = headers.get(b"x-session-token", b"").decode("latin-1")
            if not secrets.compare_digest(token, self.token):
                return await JSONResponse({"detail": "会话已失效，请刷新页面后重试。"}, status_code=403)(
                    scope, receive, send
                )
        try:
            content_length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "请求大小无效。"}, status_code=400)(scope, receive, send)
        limit = self.max_bytes + 1024 * 1024 if scope["path"] == "/api/tasks/file" else 32 * 1024
        if scope["path"].startswith("/api/history/") and scope["method"] == "PATCH":
            limit = 2500000  # bounded JSON, including escaped Unicode for 200000 characters
        if content_length > limit or content_length < 0:
            return await JSONResponse({"detail": "文件或输入内容过大，请使用较小的视频。"}, status_code=413)(
                scope, receive, send
            )
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            consumed += len(message.get("body", b""))
            if consumed > limit:
                raise BodyLimitExceeded()
            return message

        async def local_send(message):
            if message["type"] == "http.response.start":
                response_headers = message.setdefault("headers", [])
                response_headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'",
                        ),
                    ]
                )
                if scope["path"].startswith("/api/"):
                    response_headers.append((b"cache-control", b"no-store"))
            await send(message)

        try:
            await self.app(scope, bounded_receive, local_send)
        except BodyLimitExceeded:
            await JSONResponse({"detail": "文件过大，请使用较小的视频。"}, status_code=413)(
                scope, receive, send
            )


def create_app(settings=None, manager=None):
    settings = settings or Settings()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        app.state.manager = manager or JobManager(settings)
        try:
            yield
        finally:
            await run_in_threadpool(app.state.manager.close)

    app = FastAPI(title="视频文案提取器", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(LocalAccessMiddleware, token=token, max_bytes=settings.max_upload_bytes)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "输入内容不完整或格式不正确，请检查后重试。"}, status_code=422)

    @app.exception_handler(BusyError)
    async def busy_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(BodyLimitExceeded)
    async def body_error(request, exc):
        return JSONResponse({"detail": "文件过大，请使用较小的视频。"}, status_code=413)

    def get_manager(request):
        return request.app.state.manager

    def dependencies():
        return {"ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe"))}

    def ensure_ready(m, mode="fast"):
        if m.model_state(mode)["status"] != "ready":
            raise HTTPException(409, "请先完成本地模型准备，再提取文案。")
        if not all(dependencies().values()):
            raise HTTPException(409, "未找到 FFmpeg，请安装 FFmpeg 后重新启动工具。")

    @app.get("/api/session")
    def session(request: Request):
        m = get_manager(request)
        return {
            "token": token,
            "model": m.model_state(),
            "models": {mode: m.model_state(mode) for mode in ("fast", "accurate")},
            "dependencies": dependencies(),
            "active_task": m.current_task(),
        }

    @app.get("/api/model")
    def model_state(request: Request, mode: Mode = "fast"):
        return get_manager(request).model_state(mode)

    @app.post("/api/model/download", status_code=202)
    def prepare_model(request: Request, mode: Mode = "fast"):
        return get_manager(request).start_model_download(mode)

    @app.post("/api/tasks/link", status_code=202)
    def link_task(body: LinkInput, request: Request):
        from .media import extract_link, extract_share_title

        m = get_manager(request)
        try:
            url = extract_link(body.share_text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        ensure_ready(m, body.mode)
        task = m.reserve_task(body.mode, "link", url, source_title=extract_share_title(body.share_text))
        try:
            m.start_reserved(task["id"], "link", url)
        except Exception:
            m.fail_reserved(task["id"], "无法开始处理，请重试。")
            raise HTTPException(500, "无法开始处理，请重试。") from None
        return m.get_task(task["id"])

    @app.post("/api/tasks/file", status_code=202)
    async def file_task(request: Request, file: UploadFile, mode: Mode = Form("fast")):
        m = get_manager(request)
        try:
            if not file.filename or not file.filename.lower().endswith(".mp4"):
                raise HTTPException(422, "请选择 MP4 视频文件。")
            ensure_ready(m, mode)
            filename = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
            task = m.reserve_task(mode, "file", filename)
            path = m.task_dir(task["id"]) / "input.mp4"
            size = 0
            try:
                with path.open("wb") as stream:
                    while chunk := await file.read(1024 * 1024):
                        if not m.is_running(task["id"]):
                            raise HTTPException(409, "任务已取消，请重新提交。")
                        size += len(chunk)
                        if size > settings.max_upload_bytes:
                            raise HTTPException(413, "视频文件超过大小限制，请使用较小的视频。")
                        await run_in_threadpool(stream.write, chunk)
                if not size:
                    raise HTTPException(422, "视频文件为空，请重新选择。")
                m.start_reserved(task["id"], "file", str(path))
                return m.get_task(task["id"])
            except BaseException as exc:
                m.fail_reserved(task["id"], "素材导入中断，请重新选择视频。")
                if isinstance(exc, (HTTPException, BusyError)):
                    raise
                if isinstance(exc, Exception):
                    raise HTTPException(500, "视频导入失败，请检查文件后重试。") from None
                raise
        finally:
            await file.close()

    @app.get("/api/tasks/{task_id}")
    def task_state(task_id: str, request: Request):
        try:
            return get_manager(request).get_task(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在或已被新任务替换，请重新提交。") from None

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, request: Request):
        try:
            return get_manager(request).cancel(task_id)
        except KeyError:
            raise HTTPException(404, "任务不存在，请刷新页面。") from None

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exc):
        return JSONResponse({"detail": "文稿读取或保存失败，请检查磁盘空间和权限后重试。"}, status_code=503)

    @app.get("/api/history")
    def history_list(request: Request, q: str = ""):
        return {"items": get_manager(request).history.list(q)}

    @app.get("/api/history/{record_id}")
    def history_get(record_id: str, request: Request):
        try:
            return get_manager(request).history.get(record_id)
        except KeyError:
            raise HTTPException(404, "文稿不存在或已删除。") from None

    @app.patch("/api/history/{record_id}")
    def history_patch(record_id: str, body: HistoryPatch, request: Request):
        try:
            return get_manager(request).history.update(record_id, **body.model_dump(exclude_unset=True))
        except HistoryConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except KeyError:
            raise HTTPException(404, "文稿不存在或已删除。") from None

    @app.delete("/api/history/{record_id}", status_code=204)
    def history_delete(record_id: str, request: Request):
        try:
            get_manager(request).delete_history(record_id)
        except KeyError:
            raise HTTPException(404, "文稿不存在或已删除。") from None
        return Response(status_code=204)

    if settings.frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    else:

        @app.get("/")
        def frontend_missing():
            return JSONResponse({"detail": "网页尚未构建，请运行启动脚本或 npm run build。"}, status_code=503)

    return app


app = create_app()
