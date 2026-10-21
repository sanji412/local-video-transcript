"""Local paths and bounded runtime settings; importing does not create files."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get(
                "TRANSCRIPT_DATA_DIR", str(Path.home() / "Library/Application Support/LocalVideoTranscript")
            )
        )
    )
    frontend_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / "frontend/dist")
    max_upload_bytes: int = 500 * 1024 * 1024
    max_duration: int = 600
    task_timeout: float = 900
    model_timeout: float = 7200

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "models" / "whisper"
