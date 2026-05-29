"""Tests for reconciliation advisories in phase gates."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig


class TestPhaseGateReconciliation:
    """Phase gate review section: advisory checks for reconciliation."""

    @pytest.mark.unit
    def test_review_no_reconciliation_advisory(self, tmp_path: Path) -> None:
        """When no reconciliation.yaml but PRDs exist -> info advisory."""
        from trw_mcp.models.run import Phase
        from trw_mcp.state.validation.phase_gates import check_phase_exit

        run_d = tmp_path / "runs" / "20260304T120000Z-gate-test"
        meta = run_d / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: gate-test\nstatus: active\nphase: review\ntask_name: gate-task\nprd_scope:\n  - PRD-TEST-001\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")
        (run_d / "reports").mkdir(parents=True)

        config = TRWConfig()

        with patch(
            "trw_mcp.state.prd_utils.discover_governing_prds",
            return_value=["PRD-TEST-001"],
        ):
            result = check_phase_exit(Phase.REVIEW, run_d, config)

        recon_failures = [f for f in result.failures if f.field == "spec_reconciliation"]
        assert len(recon_failures) >= 1
        info_advisory = [f for f in recon_failures if f.rule == "reconciliation_not_run"]
        assert len(info_advisory) == 1
        assert info_advisory[0].severity == "info"
        assert "trw_review(mode='reconcile')" in info_advisory[0].message

    @pytest.mark.unit
    def test_review_drift_warning(self, tmp_path: Path) -> None:
        """When reconciliation.yaml has verdict='drift_detected' -> warning."""
        from trw_mcp.models.run import Phase
        from trw_mcp.state.persistence import FileStateWriter
        from trw_mcp.state.validation.phase_gates import check_phase_exit

        run_d = tmp_path / "runs" / "20260304T120000Z-gate-drift"
        meta = run_d / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: gate-drift\nstatus: active\nphase: review\ntask_name: gate-task\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")
        (run_d / "reports").mkdir(parents=True)

        writer = FileStateWriter()
        recon_data: dict[str, object] = {
            "review_id": "review-drift",
            "verdict": "drift_detected",
            "mismatches": [
                {"prd_id": "PRD-TEST-001", "fr": "FR01", "identifier": "UserValidator"},
                {"prd_id": "PRD-TEST-001", "fr": "FR02", "identifier": "DataProcessor"},
            ],
        }
        writer.write_yaml(meta / "reconciliation.yaml", recon_data)

        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_d, config)

        recon_failures = [f for f in result.failures if f.field == "spec_reconciliation"]
        assert len(recon_failures) >= 1
        drift_warning = [f for f in recon_failures if f.rule == "spec_drift_detected"]
        assert len(drift_warning) == 1
        assert drift_warning[0].severity == "warning"
        assert "2 identifier(s)" in drift_warning[0].message

    @pytest.mark.unit
    def test_review_clean_reconciliation_no_warning(self, tmp_path: Path) -> None:
        """When reconciliation.yaml has verdict='clean' -> no reconciliation failure."""
        from trw_mcp.models.run import Phase
        from trw_mcp.state.persistence import FileStateWriter
        from trw_mcp.state.validation.phase_gates import check_phase_exit

        run_d = tmp_path / "runs" / "20260304T120000Z-gate-clean"
        meta = run_d / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: gate-clean\nstatus: active\nphase: review\ntask_name: gate-task\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")
        (run_d / "reports").mkdir(parents=True)

        writer = FileStateWriter()
        recon_data: dict[str, object] = {
            "review_id": "review-clean",
            "verdict": "clean",
            "mismatches": [],
        }
        writer.write_yaml(meta / "reconciliation.yaml", recon_data)

        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_d, config)

        recon_failures = [f for f in result.failures if f.field == "spec_reconciliation"]
        assert len(recon_failures) == 0

    @pytest.mark.unit
    def test_review_no_prds_no_advisory(self, tmp_path: Path) -> None:
        """When no reconciliation.yaml and no governing PRDs -> no advisory."""
        from trw_mcp.models.run import Phase
        from trw_mcp.state.validation.phase_gates import check_phase_exit

        run_d = tmp_path / "runs" / "20260304T120000Z-gate-no-prds"
        meta = run_d / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: gate-no-prds\nstatus: active\nphase: review\ntask_name: gate-task\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")
        (run_d / "reports").mkdir(parents=True)

        config = TRWConfig()

        with patch(
            "trw_mcp.state.prd_utils.discover_governing_prds",
            return_value=[],
        ):
            result = check_phase_exit(Phase.REVIEW, run_d, config)

        recon_failures = [f for f in result.failures if f.field == "spec_reconciliation"]
        assert len(recon_failures) == 0
