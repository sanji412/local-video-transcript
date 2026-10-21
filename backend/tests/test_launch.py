import os
import subprocess
import sys
from pathlib import Path

import pytest
from app import launch
from app.launch import launch_options


def test_launcher_uses_only_loopback_and_does_not_prepare_models():
    options = launch_options(["--no-browser"])
    assert options.host == "127.0.0.1"
    assert options.port == 8765
    assert options.open_browser is False


def test_start_script_has_valid_shell_syntax_and_locked_dependencies():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(["zsh", "-n", str(root / "start.command")], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    script = (root / "start.command").read_text()
    assert "--locked" in script
    assert "--offline" in script


def test_launcher_help_needs_no_model_or_server():
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    result = subprocess.run(
        [sys.executable, "-m", "app.launch", "--help"], capture_output=True, text=True, env=environment
    )
    assert result.returncode == 0
    assert "--no-browser" in result.stdout


def test_bind_permission_error_is_not_reported_as_occupied_port(monkeypatch):
    class DeniedSocket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def bind(self, address):
            raise PermissionError("listen denied")

    monkeypatch.setattr(launch.socket, "socket", DeniedSocket)
    with pytest.raises(SystemExit, match="权限"):
        launch.main(["--no-browser"])


def test_startup_finds_keg_only_homebrew_node_without_shell_profile(tmp_path):
    root = Path(__file__).resolve().parents[2]
    prefix = tmp_path / "brew"
    npm = prefix / "opt/node@22/bin/npm"
    npm.parent.mkdir(parents=True)
    npm.write_text("#!/bin/sh\nexit 0\n")
    npm.chmod(0o755)
    setup = (root / "start.command").read_text().split("for tool in", 1)[0]
    setup = setup.replace("/opt/homebrew", str(prefix)).replace("/usr/local", str(tmp_path / "usr-local"))
    environment = dict(os.environ, HOME=str(tmp_path), PATH="/usr/bin:/bin")
    result = subprocess.run(
        ["zsh", "-c", setup + "\ncommand -v npm"], env=environment, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(npm)
