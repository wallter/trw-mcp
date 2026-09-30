"""E2E-LOCAL-OFFLINE (E2E-INC-009): the documented offline substitutes fail with one clean line naming the remedy."""

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
        timeout=120,
    )


def test_offline_local_recall_prints_one_clean_line_naming_the_start_command(tmp_path: Path) -> None:
    done = _cli(tmp_path, "local", "recall", "-q", "x")
    assert done.returncode == 1
    lines = [line for line in done.stderr.splitlines() if line.strip()]
    assert not [line for line in lines if line.lstrip().startswith("{")], done.stderr  # no JSON log lines
    assert len(lines) == 1 and lines[0].startswith("Error: ") and "trw-memory-server serve http" in lines[0], lines


def test_local_learn_help_lists_the_decision_type(tmp_path: Path) -> None:
    done = _cli(tmp_path, "local", "learn", "--help")
    assert "decision" in " ".join(done.stdout.split()), done.stdout
