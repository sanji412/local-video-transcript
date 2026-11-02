"""Persistent serial queue sharing the manager's lock and sole worker."""

import copy
import json
import sqlite3
import threading
import uuid

from .manager import BusyError
from .sharing import link_identity, parse_batch_share_text


class BatchController:
    def __init__(self, manager):
        self.manager = manager
        self.path = manager.settings.data_dir / "queue.sqlite3"
        self.batch = None
        self._stop = threading.Event()
        self._cancelling = False
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshot (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            row = db.execute("SELECT body FROM snapshot WHERE id=1").fetchone()
            if row:
                self.batch = json.loads(row[0])
                if self.batch["status"] in ("running", "paused"):
                    self.batch["status"] = "paused"
                for item in self.batch["items"]:
                    if item["status"] == "running":
                        if self.batch["status"] == "cancelled":
                            item.update(status="cancelled", detail="已取消")
                        else:
                            item.update(
                                status="failed", error="服务重启中断了此项，请重试。", detail="处理已中断"
                            )
        if self.batch:
            self._save()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _save(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("INSERT OR REPLACE INTO snapshot VALUES (1,?)", (json.dumps(self.batch),))

    def preview(self, text):
        if not 1 <= len(text) <= 200000:
            raise ValueError("批量文本需为 1–200000 个字符")
        items = parse_batch_share_text(text)
        if len(items) > 20:
            raise ValueError("每批最多 20 项")
        seen = {}
        for item in items:
            if item["status"] == "invalid":
                continue
            key = link_identity(item["url"])
            existing = self.manager.history.find_duplicate(item["url"])
            duplicate = seen.get(key)
            item.update(existing=existing, duplicate_of=duplicate)
            if existing or duplicate is not None:
                item["status"] = "duplicate"
            seen.setdefault(key, item["index"])
        return {"items": items}

    def current(self):
        with self.manager._lock:
            result = copy.deepcopy(self.batch)
            if result:
                for item in result["items"]:
                    if item["status"] == "running":
                        self._copy_task(item, self.manager.current_task(), terminal=False)
            return result

    @staticmethod
    def _copy_task(item, task, terminal=True):
        if not task or task["id"] != item["task_id"]:
            return
        for name in (
            "source",
            "history_id",
            "error",
            "detail",
            "total_seconds",
            "recognition_seconds",
            "media_duration_seconds",
            "stage",
        ):
            if name in task:
                item[name] = task[name]
        if terminal:
            item["status"] = "skipped" if task["stage"] == "duplicate" else task["status"]

    def start(self, inputs, mode="fast", retain_audio=False):
        if not 1 <= len(inputs) <= 20 or sum(len(x["share_text"]) for x in inputs) > 200000:
            raise ValueError("每批需为 1–20 项，文本不能超过 200000 字符")
        parsed = []
        for source in inputs:
            entries = self.preview(source["share_text"])["items"]
            if len(entries) != 1 or entries[0]["status"] == "invalid":
                raise ValueError("每项必须包含一条有效抖音链接")
            item = entries[0]
            parsed.append(
                dict(
                    id=uuid.uuid4().hex,
                    index=len(parsed),
                    url=item["url"],
                    title=item["title"],
                    share_text=source["share_text"],
                    status="pending",
                    force=source.get("force", False),
                    task_id=None,
                    history_id=None,
                    error=None,
                    detail="等待处理",
                    total_seconds=None,
                    recognition_seconds=None,
                    media_duration_seconds=None,
                    duplicate_of=None,
                )
            )
        with self.manager._lock:
            self.manager._ensure_available()
            if self.batch and self.batch["status"] in ("running", "paused"):
                raise BusyError("请先完成或停止当前批次。")
            self.manager.settings.model_path(mode)
            self.manager.expire_audio()
            previous = self.batch
            self.batch = dict(
                id=uuid.uuid4().hex, status="running", mode=mode, retain_audio=retain_audio, items=parsed
            )
            self.manager._batch_owner = self.batch["id"]
            try:
                self._save()
            except (OSError, sqlite3.Error):
                self.batch = previous
                self.manager._batch_owner = None
                raise
            return self.current()

    def action(self, batch_id, action, item_id=None, force=False):
        cancel_task = None
        with self.manager._lock:
            if not self.batch or self.batch["id"] != batch_id:
                raise KeyError(batch_id)
            b = self.batch
            previous = copy.deepcopy(b)
            previous_owner = self.manager._batch_owner
            if item_id:
                item = next((x for x in b["items"] if x["id"] == item_id), None)
                if item is None:
                    raise KeyError(item_id)
                if action == "cancel" and item["status"] == "pending":
                    item.update(status="cancelled", detail="已取消")
                elif (
                    action == "retry"
                    and b["status"] != "running"
                    and not any(x["status"] == "running" for x in b["items"])
                ):
                    if item["status"] not in ("failed", "cancelled", "skipped"):
                        raise ValueError("此项不可重试")
                    self.manager._ensure_available()
                    item.update(
                        status="pending",
                        force=force,
                        error=None,
                        task_id=None,
                        history_id=None,
                        total_seconds=None,
                        recognition_seconds=None,
                        media_duration_seconds=None,
                    )
                    b["status"] = "running"
                    self.manager._batch_owner = b["id"]
                else:
                    raise BusyError("当前状态无法执行此操作")
            elif action == "pause":
                if b["status"] == "running":
                    b["status"] = "paused"
            elif action == "resume":
                if b["status"] != "paused":
                    raise ValueError("只有暂停的队列可以继续")
                if self.manager._batch_owner != b["id"]:
                    self.manager._ensure_available(b["id"])
                b["status"] = "running"
                self.manager._batch_owner = b["id"]
            elif action == "cancel":
                b["status"] = "cancelled"
                for item in b["items"]:
                    if item["status"] == "pending":
                        item.update(status="cancelled", detail="已取消")
                    elif item["status"] == "running":
                        cancel_task = item["task_id"]
                self._cancelling = cancel_task is not None
            elif action == "retry_failed":
                if b["status"] == "running" or any(x["status"] == "running" for x in b["items"]):
                    raise BusyError("请等待当前处理完成")
                self.manager._ensure_available()
                for item in b["items"]:
                    if item["status"] == "failed":
                        item.update(
                            status="pending",
                            error=None,
                            task_id=None,
                            total_seconds=None,
                            recognition_seconds=None,
                            media_duration_seconds=None,
                        )
                b["status"] = "running"
                self.manager._batch_owner = b["id"]
            else:
                raise ValueError("未知队列操作")
            self._settle()
            try:
                self._save()
            except (OSError, sqlite3.Error):
                self.batch = previous
                self.manager._batch_owner = previous_owner
                self._cancelling = False
                raise
        if cancel_task:
            try:
                task = self.manager.cancel(cancel_task)
                with self.manager._lock:
                    item = next(x for x in b["items"] if x["task_id"] == cancel_task)
                    self._copy_task(item, task)
                    self._cancelling = False
                    self._settle()
                    self._save()
            finally:
                with self.manager._lock:
                    self._cancelling = False
        return self.current()

    def _settle(self):
        b = self.batch
        if not any(x["status"] == "running" for x in b["items"]):
            if b["status"] == "running" and not any(x["status"] == "pending" for x in b["items"]):
                b["status"] = "completed"
            if b["status"] != "running" and not self._cancelling:
                self.manager._batch_owner = None

    def _tick(self):
        m, b = self.manager, self.batch
        if not b or m._closed or self._cancelling:
            return
        active = next((x for x in b["items"] if x["status"] == "running"), None)
        if active:
            task = m.current_task()
            if not task or task["status"] == "running" or m._process is not None:
                return
            self._copy_task(active, task)
            self._settle()
            self._save()
        if b["status"] != "running":
            return
        item = next((x for x in b["items"] if x["status"] == "pending"), None)
        if item is None:
            self._settle()
            self._save()
            return
        existing = m.history.find_duplicate(item["url"])
        previous = next(
            (
                x
                for x in b["items"]
                if x["index"] < item["index"]
                and link_identity(x["url"]) == link_identity(item["url"])
                and x["status"] in ("completed", "skipped")
            ),
            None,
        )
        if not item["force"] and (existing or previous):
            item.update(
                status="skipped",
                detail="已跳过重复视频",
                history_id=existing["id"] if existing else previous["history_id"],
                duplicate_of=previous["id"] if previous else None,
            )
        else:
            try:
                task = m.reserve_task(
                    b["mode"],
                    "link",
                    item["url"],
                    source_title=item["title"],
                    retain_audio=b["retain_audio"],
                    force=item["force"],
                    batch_id=b["id"],
                )
                item.update(status="running", task_id=task["id"], detail="正在准备素材")
                self._save()
                m.start_reserved(task["id"], "link", item["url"])
            except Exception as exc:
                if item["task_id"]:
                    m.fail_reserved(item["task_id"], "无法开始处理，请重试。")
                item.update(status="failed", error=str(exc), detail="处理失败")
        self._settle()
        self._save()

    def _run(self):
        while not self._stop.wait(0.05):
            with self.manager._lock:
                try:
                    self._tick()
                except (OSError, sqlite3.Error):
                    # Retain ownership while an active worker stops; retry persistence next tick.
                    if self.batch:
                        self.batch["status"] = "paused"
                        self._settle()

    def close(self):
        self._stop.set()
        self._thread.join(timeout=6)
