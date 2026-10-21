import pytest
from app import models


def fixture_model(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text('{"n_mels":128}')
    (path / "weights.safetensors").write_bytes(b"model weights")


def test_empty_and_unvalidated_model_is_not_ready(tmp_path):
    assert models.model_status(tmp_path / "absent")["status"] == "missing"
    fixture_model(tmp_path / "model")
    assert models.model_status(tmp_path / "model")["status"] == "missing"


def test_preparation_validates_then_marks_ready(tmp_path, monkeypatch):
    target = tmp_path / "model"
    observed = []
    monkeypatch.setattr(models, "_download_model", lambda path, emit: fixture_model(path))
    monkeypatch.setattr(models, "_validate_model", lambda path: observed.append(path))
    events = []
    models.prepare_model(target, events.append)
    assert observed and observed[0] != target
    assert models.model_status(target)["status"] == "ready"
    assert events[-1]["status"] == "ready"
    (target / "weights.safetensors").write_bytes(b"changed")
    assert models.model_status(target)["status"] == "error"


def test_failed_validation_never_marks_model_ready(tmp_path, monkeypatch):
    target = tmp_path / "model"
    monkeypatch.setattr(models, "_download_model", lambda path, emit: fixture_model(path))
    monkeypatch.setattr(
        models, "_validate_model", lambda path: (_ for _ in ()).throw(ValueError("invalid model"))
    )
    with pytest.raises(models.ModelError):
        models.prepare_model(target, lambda e: None)
    assert models.model_status(target)["status"] != "ready"
    assert not list(tmp_path.glob(".model-preparing-*"))


def test_marker_does_not_accept_missing_weight_file(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "_download_model", lambda path, emit: fixture_model(path))
    monkeypatch.setattr(models, "_validate_model", lambda path: None)
    models.prepare_model(tmp_path / "model", lambda e: None)
    (tmp_path / "model" / "weights.safetensors").unlink()
    assert models.model_status(tmp_path / "model")["status"] == "error"


@pytest.mark.parametrize("marker", ["[]", "null", '{"files":null}', "invalid json"])
def test_corrupt_marker_is_reported_as_recoverable_error(tmp_path, marker):
    path = tmp_path / "model"
    fixture_model(path)
    (path / models.MARKER).write_text(marker)
    assert models.model_status(path)["status"] == "error"


def test_retry_cleans_staging_left_by_interrupted_model_worker(tmp_path, monkeypatch):
    stale = tmp_path / ".model-preparing-interrupted"
    fixture_model(stale)
    unrelated = tmp_path / "other-data"
    unrelated.mkdir()
    monkeypatch.setattr(models, "_download_model", lambda path, emit: fixture_model(path))
    monkeypatch.setattr(models, "_validate_model", lambda path: None)
    models.prepare_model(tmp_path / "model", lambda e: None)
    assert not stale.exists()
    assert unrelated.is_dir()
