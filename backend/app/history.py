"""Durable transcripts; each operation owns and closes its SQLite connection."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class HistoryConflict(ValueError):
    pass


class HistoryStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                original_text TEXT NOT NULL, edited_text TEXT NOT NULL,
                source TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0
            )""")

            columns = {row[1] for row in db.execute("PRAGMA table_info(history)")}
            for name, definition in (
                ("video_id", "TEXT"),
                ("source_key", "TEXT"),
                ("recognition_seconds", "REAL"),
                ("total_seconds", "REAL"),
                ("media_duration_seconds", "REAL"),
                ("segments", "TEXT NOT NULL DEFAULT '[]'"),
                ("audio_retained", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    db.execute(f"ALTER TABLE history ADD COLUMN {name} {definition}")

            from .sharing import link_identity, video_id_from_url

            for row in db.execute(
                "SELECT id,source_ref FROM history WHERE source_type='link' AND source_key IS NULL"
            ).fetchall():
                db.execute(
                    "UPDATE history SET source_key=?,video_id=coalesce(video_id,?) WHERE id=?",
                    (link_identity(row["source_ref"]), video_id_from_url(row["source_ref"]), row["id"]),
                )

    def find_duplicate(self, url=None, video_id=None):
        from .sharing import link_identity

        key = link_identity(url) if url else None
        return next(
            (
                r
                for r in self.list()
                if (video_id and r["video_id"] == video_id) or (key and r["source_key"] == key)
            ),
            None,
        )

    def known_video_ids(self):
        return sorted({r["video_id"] for r in self.list() if r["video_id"]})

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(
        self,
        *,
        id,
        source_type,
        source_ref,
        text,
        source,
        mode,
        title=None,
        segments=None,
        audio_retained=False,
        video_id=None,
        recognition_seconds=None,
        total_seconds=None,
        media_duration_seconds=None,
    ):
        from .sharing import link_identity, video_id_from_url

        self.validate_efficiency(recognition_seconds, total_seconds, media_duration_seconds)
        source_key = link_identity(source_ref) if source_type == "link" else None
        video_id = video_id or (video_id_from_url(source_ref) if source_type == "link" else None)
        segments = self.validate_segments(segments if segments is not None else [])
        edited = "\n".join(item["text"] for item in segments) if segments else text
        if not isinstance(text, str) or len(text) > 200000 or edited != text:
            raise ValueError("文案与分段内容不一致或过长")
        now = datetime.now(UTC).isoformat()
        title = (title or (source_ref if source_type == "file" else "链接文案"))[:200]
        with self._connection() as db:
            db.execute(
                "INSERT INTO history (id,title,source_type,source_ref,created_at,updated_at,original_text,edited_text,source,mode,revision,segments,audio_retained,video_id,source_key,recognition_seconds,total_seconds,media_duration_seconds) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    id,
                    title,
                    source_type,
                    source_ref,
                    now,
                    now,
                    text,
                    edited,
                    source,
                    mode,
                    0,
                    json.dumps(segments),
                    int(audio_retained),
                    video_id,
                    source_key,
                    recognition_seconds,
                    total_seconds,
                    media_duration_seconds,
                ),
            )
        return self.get(id)

    @staticmethod
    def validate_efficiency(recognition_seconds=None, total_seconds=None, media_duration_seconds=None):
        for name, value in (
            ("recognition_seconds", recognition_seconds),
            ("total_seconds", total_seconds),
            ("media_duration_seconds", media_duration_seconds),
        ):
            if value is None:
                continue
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("耗时数据无效")
            if name == "media_duration_seconds" and not 0 < value <= 1800:
                raise ValueError("视频时长无效")

    def get(self, id):
        with self._connection() as db:
            row = db.execute("SELECT * FROM history WHERE id=?", (id,)).fetchone()
            if row is None:
                raise KeyError(id)
            return self._record(row)

    def list(self, q=""):
        with self._connection() as db:
            rows = db.execute(
                """SELECT video_id,source_key,recognition_seconds,total_seconds,media_duration_seconds,id,title,source_type,source_ref,created_at,updated_at,source,mode,revision,
                substr(edited_text,1,160) AS preview FROM history
                WHERE instr(lower(title),lower(?)) > 0 OR instr(lower(edited_text),lower(?)) > 0
                OR instr(lower(source_ref),lower(?)) > 0 ORDER BY updated_at DESC,id DESC""",
                (q, q, q),
            ).fetchall()
            return [dict(row) for row in rows]

    def update(self, id, *, revision, edited_text=None, title=None):
        if title is not None and (not isinstance(title, str) or not 1 <= len(title) <= 200):
            raise ValueError("标题需为 1–200 个字符")
        if edited_text is not None and (not isinstance(edited_text, str) or len(edited_text) > 200000):
            raise ValueError("文案不能超过 200000 个字符")
        with self._connection() as db:
            existing = db.execute("SELECT segments FROM history WHERE id=?", (id,)).fetchone()
            if existing and json.loads(existing[0]) and edited_text is not None:
                if edited_text != "\n".join(x["text"] for x in json.loads(existing[0])):
                    raise HistoryConflict("分段文稿请按段保存。")
            cursor = db.execute(
                """UPDATE history SET title=coalesce(?,title),edited_text=coalesce(?,edited_text),
                revision=revision+1,updated_at=? WHERE id=? AND revision=?""",
                (title, edited_text, datetime.now(UTC).isoformat(), id, revision),
            )
            if cursor.rowcount != 1:
                if db.execute("SELECT 1 FROM history WHERE id=?", (id,)).fetchone() is None:
                    raise KeyError(id)
                raise HistoryConflict("文稿已在其他窗口更新，请重新打开最新文稿后再保存。")
            return self._record(db.execute("SELECT * FROM history WHERE id=?", (id,)).fetchone())

    def delete(self, id):
        with self._connection() as db:
            if db.execute("DELETE FROM history WHERE id=?", (id,)).rowcount != 1:
                raise KeyError(id)

    @staticmethod
    def validate_segments(segments):
        if not isinstance(segments, list) or len(segments) > 3000:
            raise ValueError("无效分段")
        result, ids, previous = [], set(), -1
        for item in segments:
            if not isinstance(item, dict) or not {"id", "start", "end", "text"}.issubset(item):
                raise ValueError("无效分段")
            ident, start, end, text = item["id"], item["start"], item["end"], item["text"]
            if (
                type(ident) is not int
                or ident < 0
                or ident in ids
                or type(start) not in (int, float)
                or type(end) not in (int, float)
                or not math.isfinite(start)
                or not math.isfinite(end)
                or not 0 <= start < end <= 1800
                or start < previous
                or not isinstance(text, str)
                or len(text) > 10000
            ):
                raise ValueError("无效分段")
            ids.add(ident)
            previous = start
            result.append(dict(id=ident, start=start, end=end, text=text, original_text=text))
        if len("\n".join(x["text"] for x in result)) > 200000:
            raise ValueError("文案过长")
        return result

    def audio_path(self, id):
        if not id or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in id
        ):
            raise KeyError(id)
        directory = self.path.parent / "audio"
        path = directory / (id + ".wav")
        if directory.is_symlink() or path.is_symlink():
            raise KeyError(id)
        return path

    def _record(self, row):
        record = dict(row)
        record["segments"] = json.loads(record["segments"])
        record["audio_retained"] = bool(record["audio_retained"])
        try:
            record["audio_available"] = self.audio_path(record["id"]).is_file()
        except KeyError:
            record["audio_available"] = False
        return record

    def update_segment(self, id, segment_id, *, revision, text):
        if not isinstance(text, str) or len(text) > 10000:
            raise ValueError("分段文字过长")
        with self._connection() as db:
            row = db.execute("SELECT * FROM history WHERE id=?", (id,)).fetchone()
            if row is None:
                raise KeyError(id)
            if row["revision"] != revision:
                raise HistoryConflict("文稿已更新，请重新载入。")
            segments = json.loads(row["segments"])
            segment = next((x for x in segments if x["id"] == segment_id), None)
            if segment is None:
                raise KeyError(segment_id)
            segment["text"] = text
            edited = "\n".join(x["text"] for x in segments)
            if len(edited) > 200000:
                raise ValueError("文案过长")
            cursor = db.execute(
                "UPDATE history SET segments=?,edited_text=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (json.dumps(segments), edited, datetime.now(UTC).isoformat(), id, revision),
            )
            if cursor.rowcount != 1:
                raise HistoryConflict("文稿已更新，请重新载入。")
        return self.get(id)
