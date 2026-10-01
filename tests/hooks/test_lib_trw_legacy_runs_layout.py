"""REMOVE-S1: lib-trw.sh no longer scans the legacy ``{task_root}/{task}/runs/{run_id}`` layout.

Runs live at ``.trw/runs/{task}/{run_id}`` (Pattern 2); nothing writes the old ``docs/`` layout
and the Python resolver never read it. A leftover legacy run must not be picked as the active run
or count as a recent delivery.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_HOOKS = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "lib-trw.sh"
_BUNDLED = Path(__file__).resolve().parents[2] / "src" / "trw_mcp" / "data" / "hooks" / "lib-trw.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(project: Path, home: Path, snippet: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f'. "{_BUNDLED}"; {snippet}'],
        cwd=project,
        env={
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": str(project),
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _run_dir(base: Path, events: str = "") -> Path:
    meta = base / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text("status: active\n", encoding="utf-8")
    (meta / "events.jsonl").write_text(events, encoding="utf-8")
    return base


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    _run_dir(root / "docs" / "task" / "runs" / "20990101T000000Z-legacy", '{"event": "trw_deliver_complete"}\n')
    _run_dir(root / ".trw" / "runs" / "task" / "20250101T000000Z-current")
    return root


def test_the_repo_and_bundled_copies_are_identical() -> None:
    if not _REPO_HOOKS.is_file():
        pytest.skip(".claude/hooks exists only in the monorepo, not in a standalone trw-mcp checkout")
    assert _REPO_HOOKS.read_bytes() == _BUNDLED.read_bytes()


def test_find_active_run_ignores_the_legacy_runs_layout(project: Path, tmp_path: Path) -> None:
    result = _run(project, tmp_path, "find_active_run docs")

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip("/").endswith("20250101T000000Z-current")


def test_has_recent_deliver_ignores_the_legacy_runs_layout(project: Path, tmp_path: Path) -> None:
    result = _run(project, tmp_path, "has_recent_deliver 600; echo rc=$?")

    assert "rc=1" in result.stdout, (result.stdout, result.stderr)
