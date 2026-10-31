"""Durable transcripts; each operation owns and closes its SQLite connection."""

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

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, *, id, source_type, source_ref, text, source, mode):
        now = datetime.now(UTC).isoformat()
        title = (source_ref if source_type == "file" else "链接文案")[:200]
        with self._connection() as db:
            db.execute(
                "INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (id, title, source_type, source_ref, now, now, text, text, source, mode, 0),
            )
        return self.get(id)

    def get(self, id):
        with self._connection() as db:
            row = db.execute("SELECT * FROM history WHERE id=?", (id,)).fetchone()
            if row is None:
                raise KeyError(id)
            return dict(row)

    def list(self, q=""):
        with self._connection() as db:
            rows = db.execute(
                """SELECT id,title,source_type,source_ref,created_at,updated_at,source,mode,revision,
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
            cursor = db.execute(
                """UPDATE history SET title=coalesce(?,title),edited_text=coalesce(?,edited_text),
                revision=revision+1,updated_at=? WHERE id=? AND revision=?""",
                (title, edited_text, datetime.now(UTC).isoformat(), id, revision),
            )
            if cursor.rowcount != 1:
                if db.execute("SELECT 1 FROM history WHERE id=?", (id,)).fetchone() is None:
                    raise KeyError(id)
                raise HistoryConflict("文稿已在其他窗口更新，请重新打开最新文稿后再保存。")
            return dict(db.execute("SELECT * FROM history WHERE id=?", (id,)).fetchone())

    def delete(self, id):
        with self._connection() as db:
            if db.execute("DELETE FROM history WHERE id=?", (id,)).rowcount != 1:
                raise KeyError(id)
