import tempfile
import unittest
from pathlib import Path

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_owned_directories_are_inside_configured_data_root(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(data_dir=Path(directory))
            self.assertEqual(settings.tasks_dir, Path(directory) / "tasks")
            self.assertEqual(settings.model_dir, Path(directory) / "models" / "whisper")
            self.assertEqual(settings.max_upload_bytes, 500 * 1024 * 1024)
            self.assertEqual(settings.max_duration, 1800)

    def test_frontend_directory_points_to_production_bundle(self):
        self.assertEqual(Settings().frontend_dir.name, "dist")
        self.assertEqual(Settings().frontend_dir.parent.name, "frontend")
