import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.history import HistoryConflict, HistoryStore


def save(store, id="one"):
    return store.create(
        id=id,
        source_type="file",
        source_ref="口播.mp4",
        text="原始全文",
        source="transcription",
        mode="accurate",
    )


def test_history_persists_original_edit_title_search_and_delete(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    first = save(store)
    assert first["revision"] == 0
    changed = store.update("one", revision=0, edited_text="编辑后", title="新标题")
    assert changed["original_text"] == "原始全文"
    assert changed["revision"] == 1
    reopened = HistoryStore(path)
    assert reopened.get("one") == changed
    assert reopened.list("新标题")[0]["id"] == "one"
    assert reopened.list("编辑后")[0]["preview"] == "编辑后"
    assert "original_text" not in reopened.list()[0]
    assert reopened.list("不存在") == []
    reopened.delete("one")
    assert reopened.list() == []
    with pytest.raises(KeyError):
        reopened.get("one")


def test_history_compare_and_swap_is_atomic_across_connections(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    save(store)

    def update(value):
        try:
            return store.update("one", revision=0, edited_text=value)
        except HistoryConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["甲", "乙"]))
    assert sum(r is not None for r in results) == 1
    assert store.get("one")["revision"] == 1


def test_search_uses_literal_text_and_parameterized_sql(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    save(store)
    assert store.list("%") == []
    assert store.list("' OR 1=1 --") == []
    with pytest.raises(KeyError):
        store.update("missing", revision=0, title="名称")


@pytest.mark.parametrize("title,expected", [(None, "链接文案"), ("分享标题", "分享标题")])
def test_link_title_persists_and_remains_renameable(tmp_path, title, expected):
    path = tmp_path / "history.sqlite3"
    store = HistoryStore(path)
    store.create(
        id="link",
        source_type="link",
        source_ref="https://v.douyin.com/abc/",
        text="正文",
        source="transcription",
        mode="fast",
        title=title,
    )
    reopened = HistoryStore(path)
    assert reopened.get("link")["title"] == expected
    assert reopened.update("link", revision=0, title="自己的标题")["title"] == "自己的标题"


def test_segment_edit_preserves_original_and_alignment(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    store.create(
        id="abc",
        source_type="file",
        source_ref="a.mp4",
        text="甲\n乙",
        source="transcription",
        mode="fast",
        segments=[
            {"id": 0, "start": 0, "end": 1, "text": "甲"},
            {"id": 1, "start": 1, "end": 2, "text": "乙"},
        ],
    )
    changed = store.update_segment("abc", 0, revision=0, text="改")
    assert changed["edited_text"] == "改\n乙"
    assert changed["segments"][0]["original_text"] == "甲"
    with pytest.raises(HistoryConflict):
        store.update("abc", revision=1, edited_text="错误")


@pytest.mark.parametrize(
    "segments", [{}, [None], [{"id": 0}], [{"id": 0, "start": 0, "end": 1, "text": "不同"}]]
)
def test_reject_inconsistent_or_malformed_segments_without_creating_record(tmp_path, segments):
    store = HistoryStore(tmp_path / "history.sqlite3")
    with pytest.raises(ValueError):
        store.create(
            id="bad",
            source_type="file",
            source_ref="a.mp4",
            text="原文",
            source="transcription",
            mode="fast",
            segments=segments,
        )
    assert store.list() == []


def test_database_upgrade_keeps_legacy_draft_and_revision(tmp_path):
    path = tmp_path / "history.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE history (id TEXT PRIMARY KEY, title TEXT, source_type TEXT, source_ref TEXT, "
            "created_at TEXT, updated_at TEXT, original_text TEXT, edited_text TEXT, source TEXT, mode TEXT, revision INTEGER)"
        )
        db.execute(
            "INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "已命名",
                "file",
                "a.mp4",
                "2026-10-31",
                "2026-11-02",
                "原文",
                "已校对稿",
                "transcription",
                "accurate",
                7,
            ),
        )
    store = HistoryStore(path)
    record = store.get("legacy")
    assert record["edited_text"] == "已校对稿" and record["original_text"] == "原文"
    assert record["revision"] == 7 and record["title"] == "已命名"
    assert record["segments"] == [] and record["audio_available"] is False
    assert store.update("legacy", revision=7, edited_text="继续编辑")["revision"] == 8


def test_segment_compare_and_swap_preserves_whole_record_under_concurrent_edits(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite3")
    before = store.create(
        id="timed",
        source_type="file",
        source_ref="a.mp4",
        text="甲\n乙",
        source="transcription",
        mode="fast",
        segments=[
            {"id": 0, "start": 0, "end": 1, "text": "甲"},
            {"id": 1, "start": 1, "end": 2, "text": "乙"},
        ],
    )

    def change(ident):
        try:
            return store.update_segment("timed", ident, revision=0, text="改")
        except HistoryConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(change, [0, 1]))
    assert sum(value is not None for value in results) == 1
    after = store.get("timed")
    assert after["revision"] == 1 and after["original_text"] == before["original_text"]
    assert [s["original_text"] for s in after["segments"]] == ["甲", "乙"]
    assert after["edited_text"] in ["改\n乙", "甲\n改"]
