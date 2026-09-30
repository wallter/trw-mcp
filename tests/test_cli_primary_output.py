"""INC-038: a command's primary output goes to stdout, never through the logger (plain output logs at WARNING)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests._stdio_harness import pinned_server_env


def _cli(project: Path, home: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    extra = {"HOME": str(home), "MEMORY_DAEMON_AUTOSTART": "false", "TRW_EMBEDDINGS_ENABLED": "false"}
    return subprocess.run(
        [sys.executable, "-m", "trw_mcp.server", *argv],
        cwd=project,
        env=pinned_server_env({**os.environ, **extra}),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "p"
    (project / ".git").mkdir(parents=True)
    init = _cli(project, tmp_path, "init-project", ".")
    assert init.returncode == 0, init.stderr[-2000:]
    return project


def test_export_without_output_prints_the_document_on_stdout(tmp_path: Path) -> None:
    project = _project(tmp_path)
    done = _cli(project, tmp_path, "export", "--scope", "runs", "--format", "json", ".")
    assert done.returncode == 0, done.stderr[-2000:]
    document = json.loads(done.stdout)  # the whole stdout is the export, not a log line
    assert isinstance(document, dict)


def test_audit_without_output_prints_the_report_on_stdout(tmp_path: Path) -> None:
    project = _project(tmp_path)
    done = _cli(project, tmp_path, "audit", "--format", "json", ".")
    assert done.returncode == 0, done.stderr[-2000:]
    assert isinstance(json.loads(done.stdout), dict)


def test_export_with_output_writes_the_file_and_says_where(tmp_path: Path) -> None:
    project = _project(tmp_path)
    target = tmp_path / "out.json"
    done = _cli(project, tmp_path, "export", "--scope", "runs", "--format", "json", "--output", str(target), ".")
    assert done.returncode == 0, done.stderr[-2000:]
    assert isinstance(json.loads(target.read_text(encoding="utf-8")), dict)
    assert f"Wrote {target}" in done.stderr
    assert done.stdout == ""
