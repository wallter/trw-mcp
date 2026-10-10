"""E2E-DOCTOR-UNINIT-REMEDY (E2E-INC-014): on a never-initialised project the remedy is init-project, in plain text."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ._stdio_harness import pinned_server_env


def _cli(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    project = tmp_path / "p"
    project.mkdir(exist_ok=True)
    extra = {"HOME": str(tmp_path), "MEMORY_DAEMON_AUTOSTART": "false"}
    return subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", *argv],
        cwd=project,
        env=pinned_server_env({**os.environ, **extra}),
        capture_output=True,
        text=True,
        timeout=180,
    )


def test_doctor_on_an_uninitialised_project_remedies_init_project(tmp_path: Path) -> None:
    done = _cli(tmp_path, "doctor")
    rows = [line for line in done.stdout.splitlines() if line.startswith("[FAIL] memory_backend")]
    assert rows, done.stdout
    assert "init-project" in rows[0] and "update-project" not in rows[0], rows[0]


def test_update_project_error_is_plain_text(tmp_path: Path) -> None:
    done = _cli(tmp_path, "update-project", ".")
    assert done.returncode == 1
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    # Outside git is no longer the refusal (init-project installs there); the missing install is, with its remedy.
    assert any(line.startswith("Error: ") and "init-project" in line for line in lines), done.stderr
    assert not [line for line in lines if line.lstrip().startswith("{")], done.stderr


def test_init_project_outside_git_warns_in_plain_text_only(tmp_path: Path) -> None:
    """E2E-INC-017: the non-git warning printed twice, once plain and once as a JSON log line."""
    done = _cli(tmp_path, "init-project", ".")
    combined = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    assert any("is not a git repository" in line and line.startswith("WARNING") for line in combined), combined
    assert not [line for line in combined if line.lstrip().startswith("{")], combined
