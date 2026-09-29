"""trw_build_check writes a typed BuildReceipt from scalar arguments (BUILD-CHECK-TYPED-RECEIPT-DEFAULT)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models._evidence_records import BuildReceipt


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    source = tmp_path / "src" / "feature.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True, exist_ok=True)
    (run / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n", encoding="utf-8"
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return run


def _receipts(run: Path) -> list[BuildReceipt]:
    files = sorted((run / "meta" / "receipts" / "build").glob("*.json"))
    return [BuildReceipt.model_validate_json(p.read_text()) for p in files]


def test_scalar_call_writes_typed_receipt_with_synthesized_results(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    result = build_check_invoke(tests_passed=True, static_checks_clean=True, scope="pytest -q unit", run_path=str(run))

    assert result["typed_receipt_state"] == "written"
    (receipt,) = _receipts(run)
    assert result["build_receipt_id"] == receipt.receipt_id
    by_id = {r.command_id: r for r in receipt.command_results}
    assert set(by_id) == {"tests", "static_checks"}
    assert by_id["tests"].label == "pytest -q unit"
    assert by_id["tests"].command_class.value == "test"
    assert by_id["tests"].exit_code == 0
    assert by_id["static_checks"].command_class.value == "static"
    assert by_id["static_checks"].exit_code == 0


def test_static_checks_unreported_is_recorded_as_not_run_never_as_passed(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    result = build_check_invoke(tests_passed=True, scope="quick", run_path=str(run), static_checks_clean=None)

    assert result["typed_receipt_state"] == "written"
    (receipt,) = _receipts(run)
    by_id = {r.command_id: r for r in receipt.command_results}
    assert set(by_id) == {"tests", "static_checks"}, "the plan requires both commands"
    static = by_id["static_checks"]
    assert static.exit_code != 0
    assert static.limitations == "not run (static_checks_clean omitted)"
    assert receipt.legacy_static_checks_clean is not True
    assert receipt.derived_outcome(("tests", "static_checks"), None) is False


def test_oversized_scope_still_writes_a_receipt_with_bounded_labels(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.models._evidence_core import EvidenceLimits

    run = _run(tmp_path, monkeypatch)
    scope = "é" * (EvidenceLimits.MAX_FREE_TEXT_BYTES + 100)
    result = build_check_invoke(tests_passed=True, static_checks_clean=True, scope=scope, run_path=str(run))

    assert result["typed_receipt_state"] == "written"
    assert result["scope"] == scope, "the full scope stays in the response/status cache"
    (receipt,) = _receipts(run)
    for r in receipt.command_results:
        assert 0 < len(r.label.encode("utf-8")) <= EvidenceLimits.MAX_FREE_TEXT_BYTES


def test_failed_tests_synthesize_exit_code_one(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    build_check_invoke(tests_passed=False, static_checks_clean=False, scope="full", run_path=str(run))

    (receipt,) = _receipts(run)
    assert {r.command_id: r.exit_code for r in receipt.command_results} == {"tests": 1, "static_checks": 1}


def test_explicit_command_results_are_not_replaced(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    supplied = [
        {"command_id": "tests", "label": "pytest -q", "command_class": "test", "exit_code": 0, "test_count": 7},
        {"command_id": "static_checks", "label": "ruff", "command_class": "static", "exit_code": 0},
    ]
    build_check_invoke(command_results=supplied, run_path=str(run))

    (receipt,) = _receipts(run)
    assert [r.label for r in receipt.command_results] == ["pytest -q", "ruff"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)


def test_receipt_git_sha_is_none_outside_a_git_tree(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    build_check_invoke(scope="full", run_path=str(run))
    assert _receipts(run)[0].git_sha is None


def test_receipt_git_sha_is_head_of_a_clean_git_tree(
    build_check_invoke: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run(tmp_path, monkeypatch)
    (tmp_path / ".gitignore").write_text(".trw/\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "T")
    _git(tmp_path, "add", ".gitignore", "src")
    _git(tmp_path, "commit", "-qm", "base")
    head = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    build_check_invoke(scope="full", run_path=str(run))
    assert _receipts(run)[0].git_sha == head
