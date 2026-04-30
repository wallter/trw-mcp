"""Delivery flywheel tests for audit metrics and promotion persistence."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server
from trw_mcp.state.analytics.report import scan_all_runs
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.state.report import assemble_report
from trw_mcp.tools._deferred_delivery import _run_deferred_steps
from trw_mcp.tools._review_helpers import _persist_review_artifact


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
            "prd_scope": ["PRD-QUAL-056"],
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
        patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_delivery_metrics",
            return_value={"status": "success", "normalized_reward": 0.5},
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

    report = assemble_report(run_dir, reader, trw_dir)
    assert report.session_metrics["audit_cycles"]["PRD-QUAL-056"] == 2
    assert report.session_metrics["finding_categories"]["impl_gap"] == 2

    monkeypatch.setattr("trw_mcp.state.analytics.report.resolve_project_root", lambda: tmp_path)
    monkeypatch.setattr("trw_mcp.state.analytics.report.resolve_trw_dir", lambda: trw_dir)
    analytics = scan_all_runs()
    assert analytics["aggregate"]["sprint_avg_audit_cycles"] == pytest.approx(1.5)
    assert analytics["aggregate"]["sprint_first_pass_compliance_rate"] == pytest.approx(0.5)


def test_deliver_persists_audit_pattern_promotion_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = make_ceremony_server(monkeypatch, tmp_path)
    writer = FileStateWriter()
    reader = FileStateReader()
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "reflections").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)

    run_dir = tmp_path / "docs" / "task" / "runs" / "20260410T120000Z-deliver-promotions"
    meta_dir = run_dir / "meta"
    meta_dir.mkdir(parents=True)
    writer.write_yaml(
        meta_dir / "run.yaml",
        {
            "run_id": run_dir.name,
            "task": "task-a",
            "status": "active",
            "phase": "deliver",
            "prd_scope": ["PRD-QUAL-056"],
        },
    )
    (meta_dir / "events.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.find_active_run", lambda: run_dir)
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_reflect",
        lambda *_a, **_kw: {"status": "success", "events_analyzed": 0, "learnings_produced": 0},
    )
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)

    noop = {"status": "skipped"}
    promotion_candidates = [
        {
            "category": "impl_gap",
            "normalized_pattern": "integration remediation wiring",
            "pattern_summary": "Integration wiring missing in remediation 2",
            "prd_count": 3,
            "prd_ids": ["PRD-CORE-104", "PRD-CORE-125", "PRD-QUAL-056"],
            "sample_summaries": [
                "Integration wiring missing in remediation 2",
                "Integration wiring missing in remediation 3",
                "Integration wiring missing in remediation 1",
            ],
            "synthesized_summary": "Recurring impl gap pattern: Integration wiring missing in remediation 2.",
            "prevention_strategy": "Verify the production call path and integration wiring before closing remediation.",
            "nudge_line": "Recurring impl gap: Integration wiring missing in remediation 2",
        }
    ]

    import trw_mcp.tools._deferred_state as _ds

    _ds._deferred_thread = None
    with (
        patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_consolidation",
            return_value={
                "status": "no_clusters",
                "clusters_found": 0,
                "consolidated_count": 0,
                "audit_pattern_promotions": promotion_candidates,
                "audit_pattern_promotion_threshold": 3,
            },
        ),
        patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
        patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
        patch(
            "trw_mcp.tools._deferred_delivery._step_delivery_metrics",
            return_value={"status": "success", "normalized_reward": 0.5},
        ),
    ):
        result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)
        assert result["deferred"] == "launched"
        assert _ds._deferred_thread is not None
        _ds._deferred_thread.join(timeout=5)
        assert not _ds._deferred_thread.is_alive()
        _ds._deferred_thread = None

    run_data = reader.read_yaml(meta_dir / "run.yaml")
    assert run_data["deferred_results"]["consolidation"]["audit_pattern_promotions"] == promotion_candidates
    assert run_data["promotion_candidates"]["audit_pattern_promotions"] == promotion_candidates
    assert run_data["promotion_candidates"]["source"] == "consolidation"
    assert run_data["promotion_candidates"]["promotion_path"] == "metadata_only"
    assert run_data["promotion_candidates"]["delivery_surface"] == "run.yaml"
    assert run_data["promotion_candidates"]["claude_md_sync_integration"] == "not_applicable_prd_core_093"
    assert run_data["promotion_candidates"]["meta_tune_integration"] == "tool_unavailable"
