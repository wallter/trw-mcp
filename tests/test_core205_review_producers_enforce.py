"""CORE-205 review-producer and enforce-mode default-path integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models._evidence_core import ReceiptState
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._delivery_review_gate import _check_review_gate
from trw_mcp.tools._review_manual import handle_manual_mode
from trw_mcp.tools._review_receipt_writer import load_latest_review_evidence


def _project_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    project = tmp_path / "project"
    source = project / "src" / "feature.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    prd = project / "docs" / "requirements-aare-f" / "prds" / "PRD-CORE-205.md"
    prd.parent.mkdir(parents=True)
    prd.write_text("# PRD-CORE-205\n\nAuthoritative requirements.\n", encoding="utf-8")
    run = project / ".trw" / "runs" / "task" / "run-1"
    meta = run / "meta"
    meta.mkdir(parents=True)
    (meta / "events.jsonl").write_text(
        json.dumps({"event": "file_modified", "file": str(source)}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    return project, run, prd


def test_completed_zero_finding_manual_review_writes_current_plan_bound_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, run, prd = _project_run(tmp_path, monkeypatch)

    result = handle_manual_mode(
        [],
        run,
        "review-1",
        "2026-07-10T00:00:00Z",
        ["PRD-CORE-205"],
        review_completed=True,
    )

    assert result["substantive"] is True
    assert result["typed_receipt_state"] == "written"
    assert result["review_receipt_id"].startswith("review-")
    validation, receipt = load_latest_review_evidence(run, project)
    assert validation.state is ReceiptState.VALID
    assert receipt is not None and receipt.findings == ()
    assert {entry.path for entry in receipt.content_binding.entries} == {
        "docs/requirements-aare-f/prds/PRD-CORE-205.md",
        "src/feature.py",
    }

    prd.write_text("# PRD-CORE-205\n\nChanged requirements.\n", encoding="utf-8")
    stale, _ = load_latest_review_evidence(run, project)
    assert stale.state is ReceiptState.STALE_CONTENT


def test_enforce_mode_refuses_stale_typed_receipt_and_legacy_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, run, prd = _project_run(tmp_path, monkeypatch)
    handle_manual_mode(
        [],
        run,
        "review-1",
        "2026-07-10T00:00:00Z",
        ["PRD-CORE-205"],
        review_completed=True,
    )
    prd.write_text("# PRD-CORE-205\n\nMutated after review.\n", encoding="utf-8")

    config = TRWConfig().model_copy(update={"evidence_receipt_mode": "enforce", "review_gate_mode": "block"})
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers._read_complexity_class", lambda *_: "STANDARD")

    block, warning, advisory = _check_review_gate(run, FileStateReader())
    assert block is not None and "No substantive trw_review" in block
    assert warning is None
    assert advisory is None


def test_enforce_mode_refuses_typed_absent_legacy_positive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, run, _ = _project_run(tmp_path, monkeypatch)
    (run / "meta" / "review.yaml").write_text(
        "verdict: pass\nsubstantive: true\nfindings: []\n",
        encoding="utf-8",
    )
    config = TRWConfig().model_copy(update={"evidence_receipt_mode": "enforce", "review_gate_mode": "block"})
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers.get_config", lambda: config)
    monkeypatch.setattr("trw_mcp.tools._delivery_helpers._read_complexity_class", lambda *_: "STANDARD")

    block, _, _ = _check_review_gate(run, FileStateReader())
    assert block is not None and "No substantive trw_review" in block
    assert project.is_dir()  # root proof for the enforce-mode integration fixture
