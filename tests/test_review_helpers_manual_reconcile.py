"""Tests for manual and reconcile review helper modes."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_helpers import (
    _extract_fr_mismatches,
    handle_manual_mode,
    handle_reconcile_mode,
)
from ._review_helpers_support import run_dir  # noqa: F401

from ._review_helpers_support import run_dir  # noqa: F401

from ._review_helpers_support import run_dir  # noqa: F401


class TestHandleManualMode:
    """handle_manual_mode: verdict computation, persistence, edge cases."""

    def test_empty_findings_returns_pass_verdict(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "pass"

    def test_critical_finding_returns_block_verdict(self, run_dir: Path) -> None:
        findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "block"

    def test_warning_finding_returns_warn_verdict(self, run_dir: Path) -> None:
        findings = [{"category": "style", "severity": "warning", "description": "Nit"}]
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["verdict"] == "warn"

    def test_counts_match_findings(self, run_dir: Path) -> None:
        findings = [
            {"category": "correctness", "severity": "critical", "description": "A"},
            {"category": "style", "severity": "warning", "description": "B"},
            {"category": "docs", "severity": "info", "description": "C"},
        ]
        result = handle_manual_mode(findings, run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["critical_count"] == 1
        assert result["warning_count"] == 1
        assert result["info_count"] == 1
        assert result["total_findings"] == 3

    def test_persists_review_yaml_when_run_exists(self, run_dir: Path) -> None:
        findings = [{"category": "style", "severity": "warning", "description": "Nit"}]
        result = handle_manual_mode(findings, run_dir, "review-abc", "2026-03-01T00:00:00Z")
        review_path = run_dir / "meta" / "review.yaml"
        assert review_path.exists()
        assert result["review_yaml"] == str(review_path)

    def test_review_yaml_contains_correct_verdict(self, run_dir: Path) -> None:
        findings = [{"category": "correctness", "severity": "critical", "description": "Bug"}]
        handle_manual_mode(findings, run_dir, "review-abc", "2026-03-01T00:00:00Z")
        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["verdict"] == "block"

    def test_review_yaml_contains_review_id(self, run_dir: Path) -> None:
        handle_manual_mode([], run_dir, "review-xyz", "2026-03-01T00:00:00Z")
        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["review_id"] == "review-xyz"

    def test_no_run_returns_empty_review_yaml(self) -> None:
        findings = [{"category": "style", "severity": "info", "description": "Note"}]
        result = handle_manual_mode(findings, None, "review-none", "2026-03-01T00:00:00Z")
        assert result["review_yaml"] == ""

    def test_no_run_returns_none_run_path(self) -> None:
        result = handle_manual_mode([], None, "review-none", "2026-03-01T00:00:00Z")
        assert result["run_path"] is None

    def test_run_path_in_result_matches_run_dir(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-abc", "2026-03-01T00:00:00Z")
        assert result["run_path"] == str(run_dir)

    def test_result_contains_review_id(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-id-check", "2026-03-01T00:00:00Z")
        assert result["review_id"] == "review-id-check"

    def test_empty_findings_zero_counts(self, run_dir: Path) -> None:
        result = handle_manual_mode([], run_dir, "review-test", "2026-03-01T00:00:00Z")
        assert result["critical_count"] == 0
        assert result["warning_count"] == 0
        assert result["info_count"] == 0
        assert result["total_findings"] == 0


class TestHandleReconcileMode:
    """Reconcile mode counts and parses both bare and fully-qualified FR headings."""

    def test_extract_fr_mismatches_supports_fully_qualified_fr_headings(self) -> None:
        prd_content = """
## Functional Requirements

### PRD-QUAL-059-FR01: Harden review guidance
Use `ReadinessGuard` to verify implementation surfaces.
"""

        mismatches = _extract_fr_mismatches(prd_content, "PRD-QUAL-059", diff="")

        assert mismatches == [
            {
                "prd_id": "PRD-QUAL-059",
                "fr": "FR01",
                "identifier": "ReadinessGuard",
                "recommendation": "update_spec",
            }
        ]

    def test_handle_reconcile_mode_counts_fully_qualified_fr_headings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        prd_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prd_dir.mkdir(parents=True)
        (prd_dir / "PRD-QUAL-059.md").write_text(
            """
# PRD-QUAL-059

## Functional Requirements

### PRD-QUAL-059-FR01: Harden review guidance
The guidance must prioritize executable evidence before prose expansion.
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setattr("trw_mcp.tools._review_helpers._get_git_diff", lambda: "")
        monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: tmp_path)

        result = handle_reconcile_mode(
            TRWConfig(prds_relative_path="docs/requirements-aare-f/prds"),
            None,
            "review-reconcile",
            "2026-04-14T00:00:00Z",
            ["PRD-QUAL-059"],
        )

        assert result["verdict"] == "clean"
        assert result["total_frs"] == 1
        assert result["mismatch_count"] == 0
