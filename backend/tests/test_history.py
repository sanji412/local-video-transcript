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
