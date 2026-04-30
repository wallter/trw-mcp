"""Tests for reconciliation mode handling and tool dispatch."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_helpers import handle_reconcile_mode

from ._reconciliation_support import (
    SAMPLE_DIFF_WITH_MATCHES,
    SAMPLE_DIFF_WITHOUT_MATCHES,
    SAMPLE_PRD,
    make_config,
    run_dir,
)


class TestHandleReconcileMode:
    """handle_reconcile_mode: the main reconciliation handler."""

    @pytest.mark.unit
    def test_no_prds_returns_clean(self, run_dir: Path) -> None:
        """No PRD IDs found -> verdict='clean', empty mismatches."""
        config = make_config()
        with (
            patch("trw_mcp.state.prd_utils.discover_governing_prds", return_value=[]),
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=run_dir.parent,
            ),
        ):
            result = handle_reconcile_mode(config, run_dir, "review-1", "2026-03-04T00:00:00Z", None)
        assert result["verdict"] == "clean"
        assert result["mismatches"] == []
        assert "No governing PRDs found" in str(result.get("message", ""))

    @pytest.mark.unit
    def test_with_mismatches(self, run_dir: Path, tmp_path: Path) -> None:
        """PRD exists with FR identifiers NOT in diff -> verdict='drift_detected'."""
        config = make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=SAMPLE_DIFF_WITHOUT_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config,
                run_dir,
                "review-2",
                "2026-03-04T00:00:00Z",
                ["PRD-TEST-001"],
            )
        assert result["verdict"] == "drift_detected"
        mismatches = result["mismatches"]
        assert isinstance(mismatches, list)
        assert len(mismatches) > 0

    @pytest.mark.unit
    def test_clean_when_all_identifiers_match(self, run_dir: Path, tmp_path: Path) -> None:
        """All FR identifiers present in diff -> verdict='clean'."""
        config = make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config,
                run_dir,
                "review-3",
                "2026-03-04T00:00:00Z",
                ["PRD-TEST-001"],
            )
        assert result["verdict"] == "clean"
        assert result["mismatches"] == []

    @pytest.mark.unit
    def test_persists_reconciliation_yaml(self, run_dir: Path, tmp_path: Path) -> None:
        """Reconciliation.yaml written to meta/ with correct fields."""
        config = make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config,
                run_dir,
                "review-persist",
                "2026-03-04T00:00:00Z",
                ["PRD-TEST-001"],
            )

        recon_path = run_dir / "meta" / "reconciliation.yaml"
        assert recon_path.exists()
        data = FileStateReader().read_yaml(recon_path)
        assert data["review_id"] == "review-persist"
        assert data["verdict"] == "clean"
        assert data["prd_ids"] == ["PRD-TEST-001"]
        assert result["reconciliation_yaml"] == str(recon_path)

    @pytest.mark.unit
    def test_logs_spec_reconciliation_event(self, run_dir: Path, tmp_path: Path) -> None:
        """spec_reconciliation event logged in events.jsonl."""
        config = make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            handle_reconcile_mode(
                config,
                run_dir,
                "review-event",
                "2026-03-04T00:00:00Z",
                ["PRD-TEST-001"],
            )

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) >= 1
        event = json.loads(lines[-1])
        assert event["event"] == "spec_reconciliation"
        assert event["review_id"] == "review-event"

    @pytest.mark.unit
    def test_no_run_dir_returns_clean(self) -> None:
        """resolved_run=None -> graceful clean verdict without persistence."""
        config = make_config()
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="diff"):
            result = handle_reconcile_mode(
                config,
                None,
                "review-norun",
                "2026-03-04T00:00:00Z",
                [],
            )
        assert result["verdict"] == "clean"
        assert "reconciliation_yaml" not in result

    @pytest.mark.unit
    def test_missing_prd_file_skipped(self, run_dir: Path, tmp_path: Path) -> None:
        """PRD file doesn't exist on disk -> skip without error."""
        config = make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)

        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value="some diff"),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config,
                run_dir,
                "review-miss",
                "2026-03-04T00:00:00Z",
                ["PRD-TEST-001"],
            )
        assert result["verdict"] == "clean"
        assert result["prd_count"] == 1

    @pytest.mark.unit
    def test_result_contains_expected_fields(self, run_dir: Path, tmp_path: Path) -> None:
        """Result dict has all expected fields."""
        config = make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config,
                run_dir,
                "review-fields",
                "2026-03-04T00:00:00Z",
                ["PRD-TEST-001"],
            )
        assert "review_id" in result
        assert "verdict" in result
        assert "mismatches" in result
        assert "prd_count" in result
        assert "total_frs" in result
        assert "mismatch_count" in result
        assert result["total_frs"] == 2


class TestTrwReviewReconcileDispatch:
    """trw_review tool dispatch: mode='reconcile' reaches handle_reconcile_mode."""

    @pytest.mark.unit
    def test_reconcile_mode_dispatches_to_handler(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """mode='reconcile' dispatches to handle_reconcile_mode."""
        from tests._ceremony_helpers import make_ceremony_server

        tools = make_ceremony_server(monkeypatch, tmp_path)

        run_d = tmp_path / "docs" / "task" / "runs" / "20260304T120000Z-dispatch-test"
        meta = run_d / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: dispatch-test\nstatus: active\nphase: review\ntask_name: dispatch-task\n",
            encoding="utf-8",
        )
        (meta / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch(
                "trw_mcp.tools._review_manual.handle_reconcile_mode",
                return_value={"verdict": "clean", "mismatches": []},
            ) as mock_handler,
            patch("trw_mcp.state._paths.find_active_run", return_value=run_d),
        ):
            trw_review = tools["trw_review"]
            trw_review.fn(mode="reconcile", prd_ids=["PRD-TEST-001"])

        mock_handler.assert_called_once()
        call_args = mock_handler.call_args
        assert call_args[1].get("prd_ids") == ["PRD-TEST-001"] or call_args[0][4] == ["PRD-TEST-001"]
