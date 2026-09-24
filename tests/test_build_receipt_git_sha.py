"""CORE296 FR07: only a clean, server-observed Git HEAD binds build evidence."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trw_mcp.models._evidence_records import BuildReceipt
from trw_mcp.tools._evidence_git import clean_git_sha
from trw_mcp.tools._evidence_writers import record_build_receipt


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".gitignore").write_text(".trw/\n", encoding="utf-8")
    (repo / "code.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", ".gitignore", "code.py")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def test_receipt_stamps_clean_head(git_project: Path) -> None:
    run = git_project / ".trw" / "runs" / "task" / "run1"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    outcome = record_build_receipt(
        run,
        git_project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="unit",
        coverage_pct=None,
        policy_mode="observe",
    )
    assert outcome is not None and outcome.ok
    receipts = list((run / "meta" / "receipts" / "build").glob("*.json"))
    assert len(receipts) == 1
    assert BuildReceipt.model_validate_json(receipts[0].read_text()).git_sha == _git(git_project, "rev-parse", "HEAD")


def test_dirty_and_untracked_worktrees_have_no_sha(git_project: Path) -> None:
    assert clean_git_sha(git_project) == _git(git_project, "rev-parse", "HEAD")
    (git_project / "code.py").write_text("value = 2\n", encoding="utf-8")
    assert clean_git_sha(git_project) is None
    _git(git_project, "restore", "code.py")
    (git_project / "untracked.py").write_text("x = 1\n", encoding="utf-8")
    assert clean_git_sha(git_project) is None


def test_receipt_never_labels_dirty_code_as_clean_head(git_project: Path) -> None:
    (git_project / "code.py").write_text("value = 2\n", encoding="utf-8")
    run = git_project / ".trw" / "runs" / "task" / "run1"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text("", encoding="utf-8")
    outcome = record_build_receipt(
        run,
        git_project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="unit",
        coverage_pct=None,
        policy_mode="observe",
    )
    assert outcome is not None and outcome.ok
    receipt_path = next((run / "meta" / "receipts" / "build").glob("*.json"))
    assert BuildReceipt.model_validate_json(receipt_path.read_text()).git_sha is None


def test_detached_clean_head_still_binds(git_project: Path) -> None:
    sha = _git(git_project, "rev-parse", "HEAD")
    _git(git_project, "switch", "--detach", sha)
    assert clean_git_sha(git_project) == sha


def test_non_git_project_has_no_sha(tmp_path: Path) -> None:
    assert clean_git_sha(tmp_path) is None
