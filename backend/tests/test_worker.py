import json

from app import worker


def test_worker_json_protocol_survives_library_stdout(tmp_path, monkeypatch, capsys):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"kind": "task"}))

    def run(data, emit):
        print("third party noise")
        emit({"type": "stage", "stage": "transcribing", "detail": "正在转写"})
        return {"text": "全文", "source": "transcription"}

    monkeypatch.setattr(worker, "run_pipeline", run)
    assert worker.main([str(payload)]) == 0
    output = capsys.readouterr()
    assert "third party noise" in output.err
    assert [json.loads(line)["type"] for line in output.out.splitlines()] == ["stage", "result"]


def test_worker_turns_failures_into_json(tmp_path, monkeypatch, capsys):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"kind": "task"}))
    monkeypatch.setattr(worker, "run_pipeline", lambda *a: (_ for _ in ()).throw(ValueError("bad file")))
    assert worker.main([str(payload)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["type"] == "error" and result["detail"]


def test_model_worker_emits_model_events(tmp_path, monkeypatch, capsys):
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"kind": "model", "model_dir": str(tmp_path / "model")}))
    monkeypatch.setattr(
        worker,
        "prepare_model",
        lambda path, emit: emit({"type": "model", "status": "ready", "detail": "就绪", "progress": 1}),
    )
    assert worker.main([str(payload)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
