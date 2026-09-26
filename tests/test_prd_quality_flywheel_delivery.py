"""Delivery flywheel tests for audit metrics and promotion persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.analytics.report import scan_all_runs
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._deferred_delivery import _run_deferred_steps
from trw_mcp.tools._review_helpers import _persist_review_artifact


def _write_scoped_prd(project_root: Path, prd_id: str = "PRD-QUAL-056") -> str:
    """Write the PRD file this fixture's run names in ``prd_scope``.

    PRD-CORE-255-FR03 (2026-09-04 amendment): declaring NO scope is inert, but
    declaring a scope whose PRD file cannot be read resolves ``unknown`` and
    fails closed into the FR04 adversarial gate. These fixtures genuinely are
    scoped -- the flywheel report correlates on ``prd_scope`` -- so they must be
    able to show the PRD they claim.
    """
    prds = project_root / "docs" / "requirements-aare-f" / "prds"
    prds.mkdir(parents=True, exist_ok=True)
    (prds / f"{prd_id}.md").write_text(
        f'---\nprd:\n  id: {prd_id}\n  title: "flywheel fixture"\n  safety_critical: false\n---\n\n# {prd_id}\n',
        encoding="utf-8",
    )
    return prd_id


def test_delivery_report_rework_metrics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writer = FileStateWriter()
    reader = FileStateReader()
    trw_dir = tmp_path / ".trw"
    run_dir = trw_dir / "runs" / "task-a" / "20260408T120000Z-wave0001"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    writer.write_yaml(
        meta_dir / "run.yaml",
        {
            "run_id": run_dir.name,
            "task": "task-a",
            "status": "active",
            "phase": "deliver",
            "prd_scope": [_write_scoped_prd(tmp_path)],
        },
    )
    _persist_review_artifact(
        run_dir,
        {
            "review_id": "rev-001",
            "timestamp": "2026-04-08T12:00:00Z",
            "verdict": "block",
            "findings": [
                {"category": "impl_gap", "severity": "critical", "description": "Missing wire-up"},
                {"category": "test_gap", "severity": "warning", "description": "Missing regression test"},
            ],
        },
        {
            "review_id": "rev-001",
            "verdict": "block",
        },
    )
    _persist_review_artifact(
        run_dir,
        {
            "review_id": "rev-002",
            "timestamp": "2026-04-08T12:05:00Z",
            "verdict": "pass",
            "findings": [
                {"category": "impl_gap", "severity": "info", "description": "Wire-up verified"},
            ],
        },
        {
            "review_id": "rev-002",
            "verdict": "pass",
        },
    )
    _persist_review_artifact(
        run_dir,
        {
            "review_id": "rev-003",
            "timestamp": "2026-04-08T12:10:00Z",
            "verdict": "pass",
            "findings": [
                {"category": "spec_gap", "severity": "info", "description": "Spec clarified"},
            ],
        },
        {
            "review_id": "rev-003",
            "verdict": "pass",
            "prd_ids": ["PRD-CORE-104"],
        },
    )
    events = reader.read_jsonl(meta_dir / "events.jsonl")
    assert [event["event"] for event in events if event["event"] == "audit_cycle_complete"] == [
        "audit_cycle_complete",
        "audit_cycle_complete",
        "audit_cycle_complete",
    ]
    assert [event["prd_id"] for event in events if event["event"] == "audit_cycle_complete"] == [
        "PRD-QUAL-056",
        "PRD-QUAL-056",
        "PRD-CORE-104",
    ]

    noop = {"status": "skipped"}
    with (
        patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_delivery_metrics",
            return_value={"status": "success"},
        ),
    ):
        _run_deferred_steps(trw_dir, run_dir, {})

    run_data = reader.read_yaml(meta_dir / "run.yaml")
    session_metrics = run_data["session_metrics"]
    assert session_metrics["audit_cycles"] == {"PRD-QUAL-056": 2, "PRD-CORE-104": 1}
    assert session_metrics["first_pass_compliance"] == {
        "PRD-QUAL-056": False,
        "PRD-CORE-104": True,
    }
    assert session_metrics["finding_categories"] == {
        "impl_gap": 2,
        "test_gap": 1,
        "spec_gap": 1,
    }
    assert session_metrics["sprint_avg_audit_cycles"] == pytest.approx(1.5)
    assert session_metrics["sprint_first_pass_compliance_rate"] == pytest.approx(0.5)

    monkeypatch.setattr("trw_mcp.state.analytics.report.resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr("trw_mcp.state.analytics.report.resolve_trw_dir", lambda: trw_dir)
    analytics = scan_all_runs()
    assert analytics["aggregate"]["sprint_avg_audit_cycles"] == pytest.approx(1.5)
    assert analytics["aggregate"]["sprint_first_pass_compliance_rate"] == pytest.approx(0.5)
