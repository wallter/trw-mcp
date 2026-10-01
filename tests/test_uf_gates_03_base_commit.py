"""UF-GATES-03 (PRD-CORE-213 FR08): the status->implemented gate sees transitions committed since trw_init.

Nothing recorded ``base_commit`` at ``trw_init``, so the transition detector fell back to ``git diff HEAD``
(uncommitted changes only). Under a commit-frequently workflow the ``status: implemented`` edit is usually
committed before ``trw_deliver``, so the gate never saw it while reading as "block". ``trw_init`` now records
the checkout's HEAD, and the detector diffs the working tree against it (committed AND uncommitted changes).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests._tools_orchestration_support import orch_tools  # noqa: F401

_PRD_REL = "docs/requirements-aare-f/prds/PRD-CORE-998.md"
_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}  # fmt: skip


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, env=_ENV, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _prd(root: Path, status: str) -> None:
    path = root / _PRD_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nprd:\n  id: PRD-CORE-998\n  status: {status}\n---\n\n# body\n", encoding="utf-8")


def _repo(root: Path) -> str:
    _git(root, "init", "-q")
    _prd(root, "draft")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "base")
    return _git(root, "rev-parse", "HEAD")


def test_trw_init_records_the_checkouts_head_as_base_commit(
    orch_tools: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state.persistence import FileStateReader

    head = _repo(tmp_path)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    result = orch_tools["trw_init"].fn(task_name="basecommit")
    run_yaml = FileStateReader().read_yaml(Path(str(result["run_path"])) / "meta" / "run.yaml")
    assert run_yaml["base_commit"] == head


def test_outside_git_trw_init_records_no_base_commit(
    orch_tools: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.state.persistence import FileStateReader

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    result = orch_tools["trw_init"].fn(task_name="nogit")
    run_yaml = FileStateReader().read_yaml(Path(str(result["run_path"])) / "meta" / "run.yaml")
    assert run_yaml.get("base_commit") in (None, "")


def test_a_committed_transition_since_the_base_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.tools._prd_transition_gate import _prd_status_diff, detect_status_transitions

    base = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    _prd(tmp_path, "implemented")
    _git(tmp_path, "commit", "-q", "-am", "mark implemented")  # committed before delivery, as policy says

    assert detect_status_transitions(_prd_status_diff(None)) == []  # the old fallback sees nothing
    assert detect_status_transitions(_prd_status_diff(base)) == ["PRD-CORE-998"]


def test_an_uncommitted_transition_is_still_detected_with_a_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._prd_transition_gate import _prd_status_diff, detect_status_transitions

    base = _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    _prd(tmp_path, "implemented")  # not committed
    assert detect_status_transitions(_prd_status_diff(base)) == ["PRD-CORE-998"]


def test_a_non_hex_base_is_ignored_rather_than_passed_to_git() -> None:
    from trw_mcp.tools._prd_transition_gate import _run_base_ref

    assert _run_base_ref({"base_commit": "--output=/tmp/x"}) is None
    assert _run_base_ref({"base_commit": " abc1234 "}) == "abc1234"
