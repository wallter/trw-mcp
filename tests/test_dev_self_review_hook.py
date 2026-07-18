"""Regression tests for the repo-local self-review Stop hook."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / ".claude" / "hooks"


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)


def _project(tmp_path: Path, *, blocking: bool) -> Path:
    hooks = tmp_path / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(HOOKS / "self-review.sh", hooks / "self-review.sh")
    shutil.copy2(HOOKS / "lib-trw.sh", hooks / "lib-trw.sh")
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text(
        f"self_review_blocking: {'true' if blocking else 'false'}\n",
        encoding="utf-8",
    )
    source = tmp_path / "sample.py"
    source.write_text("def value() -> int:\n    return 1\n", encoding="utf-8")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@example.invalid"],
        ["git", "config", "user.name", "Test"],
        ["git", "add", "sample.py"],
        ["git", "commit", "-qm", "baseline"],
    ):
        result = _run(command, tmp_path)
        assert result.returncode == 0, result.stderr
    return source


def test_configured_placeholder_finding_preserves_blocking_exit(tmp_path: Path) -> None:
    source = _project(tmp_path, blocking=True)
    source.write_text("def value() -> int:\n    raise NotImplementedError\n", encoding="utf-8")

    result = _run(["sh", ".claude/hooks/self-review.sh"], tmp_path)

    assert result.returncode == 2
    assert "INCOMPLETE: sample.py" in result.stderr


def test_advisory_todo_survives_loop_without_false_block(tmp_path: Path) -> None:
    source = _project(tmp_path, blocking=True)
    source.write_text("def value() -> None:\n    # TODO: explain later\n    return None\n", encoding="utf-8")

    result = _run(["sh", ".claude/hooks/self-review.sh"], tmp_path)

    assert result.returncode == 0
    assert "TODO: sample.py" in result.stderr
    assert "placeholder implementation (return None)" not in result.stderr
