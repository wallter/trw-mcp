"""CORE-205 typed build-plan enforcement and legacy closure tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models._evidence_plans import BuildCommandResult
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._delivery_build_gates import build_receipt_content_stale_warning
from trw_mcp.tools._evidence_writers import (
    load_latest_build_evidence,
    parse_build_command_results,
    record_build_receipt,
)


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    source = project / "src" / "feature.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    run = project / ".trw" / "runs" / "task" / "run-1"
    (run / "meta").mkdir(parents=True)
    (run / "meta" / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    return project, run, source


def _passing_results() -> tuple[BuildCommandResult, ...]:
    parsed = parse_build_command_results(
        [
            {"command_id": "tests", "label": "pytest", "command_class": "test", "exit_code": 0},
            {
                "command_id": "static_checks",
                "label": "ruff+mypy",
                "command_class": "static",
                "exit_code": 0,
            },
        ]
    )
    assert parsed is not None
    return parsed


def test_default_evidence_mode_is_enforce() -> None:
    assert TRWConfig().evidence_receipt_mode == "enforce"


def test_enforce_refuses_legacy_only_build_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, run, _ = _run(tmp_path, monkeypatch)
    outcome = record_build_receipt(
        run,
        project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=None,
        policy_mode="enforce",
    )
    assert outcome is None
    state, receipt = load_latest_build_evidence(run, project)
    assert state.state is ReceiptState.LEGACY_UNBOUND
    assert receipt is None


def test_enforce_validates_complete_typed_plan_and_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, run, source = _run(tmp_path, monkeypatch)
    outcome = record_build_receipt(
        run,
        project,
        tests_passed=True,
        static_checks_clean=True,
        scope_label="full",
        coverage_pct=95.0,
        policy_mode="enforce",
        command_results=_passing_results(),
        coverage_threshold=90.0,
    )
    assert outcome is not None and outcome.ok
    state, receipt = load_latest_build_evidence(run, project)
    assert state.state is ReceiptState.VALID
    assert receipt is not None and {item.command_id for item in receipt.command_results} == {
        "tests",
        "static_checks",
    }

    source.write_text("VALUE = 2\n", encoding="utf-8")
    stale, _ = load_latest_build_evidence(run, project)
    assert stale.state is ReceiptState.STALE_CONTENT


def test_enforce_delivery_warning_rejects_missing_typed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, run, _ = _run(tmp_path, monkeypatch)
    config = TRWConfig().model_copy(update={"evidence_receipt_mode": "enforce"})
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)
    warning = build_receipt_content_stale_warning(run)
    assert warning is not None and "No valid content-bound BuildReceipt" in warning
