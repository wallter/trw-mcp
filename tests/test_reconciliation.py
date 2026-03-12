"""Tests for spec reconciliation mode in trw_review.

Covers:
- handle_reconcile_mode: no PRDs, with mismatches, clean, persist yaml, log event
- _extract_fr_mismatches: regex extraction
- _extract_identifiers: backtick, flag, class patterns
- _count_frs_in_prd: FR counting
- _extract_section: markdown section extraction
- trw_review tool dispatch: mode="reconcile" reaches handler
- phase_gates: review advisory for missing/drifted reconciliation
- Edge cases: no run dir, missing PRD files
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_helpers import (
    _added_lines_only,
    _count_frs_in_prd,
    _extract_fr_mismatches,
    _extract_identifiers,
    _extract_section,
    handle_reconcile_mode,
)


# ---------------------------------------------------------------------------
# Shared fixtures and constants
# ---------------------------------------------------------------------------


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    d = tmp_path / "runs" / "20260304T120000Z-reconcile-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: reconcile-test\nstatus: active\nphase: review\ntask_name: test-task\n",
        encoding="utf-8",
    )
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


def _make_config(*, prds_relative_path: str = "prds") -> TRWConfig:
    return TRWConfig(prds_relative_path=prds_relative_path)


SAMPLE_PRD = """\
---
id: PRD-TEST-001
status: draft
---
# PRD-TEST-001: Test Feature

## 3. Functional Requirements

### FR01: Implement `UserValidator` class

The `UserValidator` class MUST validate input using the `--strict` flag.

Given a user input
When `validate()` is called with `--strict`
Then validation errors are returned as `ValidationResult` objects.

### FR02: Add `DataProcessor` pipeline

The `DataProcessor` MUST support `--dry-run` mode for testing.

## 4. Non-Functional Requirements

NFR content here.
"""

SAMPLE_DIFF_WITH_MATCHES = """\
diff --git a/src/validator.py b/src/validator.py
+class UserValidator:
+    def validate(self, strict=True):
+        result = validate()
+        return ValidationResult(errors=[])
+    --strict flag handling
+DataProcessor
+--dry-run
"""

SAMPLE_DIFF_WITHOUT_MATCHES = """\
diff --git a/src/other.py b/src/other.py
+class SomethingElse:
+    pass
"""

# Diff where identifiers appear only in REMOVED lines (false-negative scenario)
SAMPLE_DIFF_REMOVED_ONLY = """\
diff --git a/src/validator.py b/src/validator.py
--- a/src/validator.py
+++ b/src/validator.py
-class UserValidator:
-    def validate(self, strict=True):
-        return ValidationResult(errors=[])
+class NewValidator:
+    pass
"""


# ---------------------------------------------------------------------------
# _added_lines_only
# ---------------------------------------------------------------------------


class TestAddedLinesOnly:
    """_added_lines_only: filter diff to only added lines."""

    @pytest.mark.unit
    def test_keeps_added_lines(self) -> None:
        diff = "+added line\n-removed line\n context line"
        result = _added_lines_only(diff)
        assert "+added line" in result
        assert "context line" in result

    @pytest.mark.unit
    def test_removes_deleted_lines(self) -> None:
        diff = "+added\n-removed\n context"
        result = _added_lines_only(diff)
        assert "-removed" not in result

    @pytest.mark.unit
    def test_keeps_diff_header_lines(self) -> None:
        diff = "--- a/file.py\n+++ b/file.py\n-old\n+new"
        result = _added_lines_only(diff)
        assert "--- a/file.py" in result
        assert "+++ b/file.py" in result

    @pytest.mark.unit
    def test_empty_diff(self) -> None:
        assert _added_lines_only("") == ""


# ---------------------------------------------------------------------------
# _extract_section
# ---------------------------------------------------------------------------


class TestExtractSection:
    """_extract_section: markdown section extraction."""

    @pytest.mark.unit
    def test_extracts_numbered_section(self) -> None:
        content = "## 3. Functional Requirements\n\nFR01 content here\n\n## 4. Other\n\nOther content"
        result = _extract_section(content, "Functional Requirements")
        assert "FR01 content here" in result
        assert "Other content" not in result

    @pytest.mark.unit
    def test_extracts_unnumbered_section(self) -> None:
        content = "## Functional Requirements\n\nFR01 content\n\n## Other\n\nMore"
        result = _extract_section(content, "Functional Requirements")
        assert "FR01 content" in result

    @pytest.mark.unit
    def test_returns_empty_for_missing_section(self) -> None:
        content = "## Some Section\n\nContent here"
        result = _extract_section(content, "Nonexistent Section")
        assert result == ""

    @pytest.mark.unit
    def test_extracts_to_end_when_no_following_section(self) -> None:
        content = "## 3. Functional Requirements\n\nFR01 content here\nMore content"
        result = _extract_section(content, "Functional Requirements")
        assert "FR01 content here" in result
        assert "More content" in result

    @pytest.mark.unit
    def test_case_insensitive(self) -> None:
        content = "## functional requirements\n\nContent"
        result = _extract_section(content, "Functional Requirements")
        assert "Content" in result


# ---------------------------------------------------------------------------
# _extract_identifiers
# ---------------------------------------------------------------------------


class TestExtractIdentifiers:
    """_extract_identifiers: extract code identifiers from FR text."""

    @pytest.mark.unit
    def test_extracts_backtick_identifiers(self) -> None:
        text = "The `UserValidator` class MUST call `validate()` method."
        result = _extract_identifiers(text)
        assert "UserValidator" in result
        assert "validate()" in result

    @pytest.mark.unit
    def test_extracts_flag_identifiers(self) -> None:
        text = "Supports --strict and --dry-run flags."
        result = _extract_identifiers(text)
        assert "--strict" in result
        assert "--dry-run" in result

    @pytest.mark.unit
    def test_extracts_pascal_case_class_names(self) -> None:
        text = "The UserValidator and DataProcessor classes are used."
        result = _extract_identifiers(text)
        assert "UserValidator" in result
        assert "DataProcessor" in result

    @pytest.mark.unit
    def test_deduplicates_preserving_order(self) -> None:
        text = "Use `foo` and `foo` again. Also `bar`."
        result = _extract_identifiers(text)
        assert result.count("foo") == 1
        assert result.index("foo") < result.index("bar")

    @pytest.mark.unit
    def test_empty_text_returns_empty(self) -> None:
        result = _extract_identifiers("")
        assert result == []

    @pytest.mark.unit
    def test_no_identifiers_returns_empty(self) -> None:
        text = "This text has no code identifiers or flags."
        result = _extract_identifiers(text)
        # No backticks, no --flags, no PascalCase
        assert all(not item.startswith("--") for item in result)

    @pytest.mark.unit
    def test_single_uppercase_word_not_extracted_as_pascal_case(self) -> None:
        """Single-word uppercase identifiers like 'MUST' are not PascalCase."""
        text = "The system MUST validate."
        result = _extract_identifiers(text)
        assert "MUST" not in result

    @pytest.mark.unit
    def test_combined_extraction(self) -> None:
        text = "The `UserValidator` uses --strict flag and creates ValidationResult objects."
        result = _extract_identifiers(text)
        assert "UserValidator" in result
        assert "--strict" in result
        assert "ValidationResult" in result


# ---------------------------------------------------------------------------
# _extract_fr_mismatches
# ---------------------------------------------------------------------------


class TestExtractFrMismatches:
    """_extract_fr_mismatches: compare FR identifiers against diff."""

    @pytest.mark.unit
    def test_no_mismatches_when_all_identifiers_in_diff(self) -> None:
        mismatches = _extract_fr_mismatches(SAMPLE_PRD, "PRD-TEST-001", SAMPLE_DIFF_WITH_MATCHES)
        assert mismatches == []

    @pytest.mark.unit
    def test_mismatches_when_identifiers_not_in_diff(self) -> None:
        mismatches = _extract_fr_mismatches(SAMPLE_PRD, "PRD-TEST-001", SAMPLE_DIFF_WITHOUT_MATCHES)
        assert len(mismatches) > 0
        # All mismatches should reference the PRD
        assert all(m["prd_id"] == "PRD-TEST-001" for m in mismatches)

    @pytest.mark.unit
    def test_mismatch_has_required_fields(self) -> None:
        mismatches = _extract_fr_mismatches(SAMPLE_PRD, "PRD-TEST-001", SAMPLE_DIFF_WITHOUT_MATCHES)
        assert len(mismatches) > 0
        for m in mismatches:
            assert "prd_id" in m
            assert "fr" in m
            assert "identifier" in m
            assert "recommendation" in m
            assert m["recommendation"] == "update_spec"

    @pytest.mark.unit
    def test_mismatches_reference_fr_numbers(self) -> None:
        mismatches = _extract_fr_mismatches(SAMPLE_PRD, "PRD-TEST-001", SAMPLE_DIFF_WITHOUT_MATCHES)
        fr_refs = {m["fr"] for m in mismatches}
        # Should reference FR01 and/or FR02
        assert fr_refs & {"FR01", "FR02"}

    @pytest.mark.unit
    def test_no_functional_requirements_section_returns_empty(self) -> None:
        content = "# Just a title\n\nSome content without FR section."
        mismatches = _extract_fr_mismatches(content, "PRD-X", "any diff")
        assert mismatches == []

    @pytest.mark.unit
    def test_empty_diff_produces_mismatches_for_all_identifiers(self) -> None:
        mismatches = _extract_fr_mismatches(SAMPLE_PRD, "PRD-TEST-001", "")
        # With empty diff, none of the identifiers match
        assert len(mismatches) > 0

    @pytest.mark.unit
    def test_removed_lines_not_counted_as_present(self) -> None:
        """Identifiers only in removed (-) lines should still be mismatches.

        Regression test for P1-1: deleted code should not mask spec drift.
        """
        mismatches = _extract_fr_mismatches(
            SAMPLE_PRD, "PRD-TEST-001", SAMPLE_DIFF_REMOVED_ONLY,
        )
        # UserValidator and ValidationResult appear only in removed lines,
        # so they should be reported as mismatches
        mismatch_idents = {m["identifier"] for m in mismatches}
        assert "UserValidator" in mismatch_idents
        assert "ValidationResult" in mismatch_idents


# ---------------------------------------------------------------------------
# _count_frs_in_prd
# ---------------------------------------------------------------------------


class TestCountFrsInPrd:
    """_count_frs_in_prd: count FR entries in a PRD file."""

    @pytest.mark.unit
    def test_counts_frs_in_sample_prd(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-TEST-001.md"
        prd_file.write_text(SAMPLE_PRD, encoding="utf-8")
        assert _count_frs_in_prd(prd_file) == 2

    @pytest.mark.unit
    def test_zero_for_nonexistent_file(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "nonexistent.md"
        assert _count_frs_in_prd(prd_file) == 0

    @pytest.mark.unit
    def test_zero_for_prd_without_frs(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "empty.md"
        prd_file.write_text("# PRD with no FRs\n\nJust text.", encoding="utf-8")
        assert _count_frs_in_prd(prd_file) == 0

    @pytest.mark.unit
    def test_counts_multiple_frs(self, tmp_path: Path) -> None:
        content = """\
## 3. Functional Requirements

### FR01: First requirement
Content.

### FR02: Second requirement
Content.

### FR03: Third requirement
Content.

## 4. Other
"""
        prd_file = tmp_path / "multi.md"
        prd_file.write_text(content, encoding="utf-8")
        assert _count_frs_in_prd(prd_file) == 3


# ---------------------------------------------------------------------------
# handle_reconcile_mode
# ---------------------------------------------------------------------------


class TestHandleReconcileMode:
    """handle_reconcile_mode: the main reconciliation handler."""

    @pytest.mark.unit
    def test_no_prds_returns_clean(self, run_dir: Path) -> None:
        """No PRD IDs found -> verdict='clean', empty mismatches."""
        config = _make_config()
        with (
            patch("trw_mcp.state.prd_utils.discover_governing_prds", return_value=[]),
            patch("trw_mcp.tools.review._get_git_diff", return_value="some diff"),
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
        config = _make_config(prds_relative_path="prds")
        # Create PRD file at the expected location
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value=SAMPLE_DIFF_WITHOUT_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config, run_dir, "review-2", "2026-03-04T00:00:00Z", ["PRD-TEST-001"],
            )
        assert result["verdict"] == "drift_detected"
        mismatches = result["mismatches"]
        assert isinstance(mismatches, list)
        assert len(mismatches) > 0

    @pytest.mark.unit
    def test_clean_when_all_identifiers_match(self, run_dir: Path, tmp_path: Path) -> None:
        """All FR identifiers present in diff -> verdict='clean'."""
        config = _make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config, run_dir, "review-3", "2026-03-04T00:00:00Z", ["PRD-TEST-001"],
            )
        assert result["verdict"] == "clean"
        assert result["mismatches"] == []

    @pytest.mark.unit
    def test_persists_reconciliation_yaml(self, run_dir: Path, tmp_path: Path) -> None:
        """Reconciliation.yaml written to meta/ with correct fields."""
        config = _make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config, run_dir, "review-persist", "2026-03-04T00:00:00Z", ["PRD-TEST-001"],
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
        config = _make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            handle_reconcile_mode(
                config, run_dir, "review-event", "2026-03-04T00:00:00Z", ["PRD-TEST-001"],
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
        config = _make_config()
        with patch("trw_mcp.tools.review._get_git_diff", return_value="diff"):
            result = handle_reconcile_mode(
                config, None, "review-norun", "2026-03-04T00:00:00Z", [],
            )
        assert result["verdict"] == "clean"
        assert "reconciliation_yaml" not in result

    @pytest.mark.unit
    def test_missing_prd_file_skipped(self, run_dir: Path, tmp_path: Path) -> None:
        """PRD file doesn't exist on disk -> skip without error."""
        config = _make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        # No PRD-TEST-001.md created — file does not exist

        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value="some diff"),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config, run_dir, "review-miss", "2026-03-04T00:00:00Z", ["PRD-TEST-001"],
            )
        # Should not raise, verdict is clean (no mismatches from missing PRD)
        assert result["verdict"] == "clean"
        assert result["prd_count"] == 1

    @pytest.mark.unit
    def test_result_contains_expected_fields(self, run_dir: Path, tmp_path: Path) -> None:
        """Result dict has all expected fields."""
        config = _make_config(prds_relative_path="prds")
        project_root = tmp_path / "project"
        prds_dir = project_root / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-TEST-001.md").write_text(SAMPLE_PRD, encoding="utf-8")

        with (
            patch("trw_mcp.tools.review._get_git_diff", return_value=SAMPLE_DIFF_WITH_MATCHES),
            patch(
                "trw_mcp.state._paths.resolve_project_root",
                return_value=project_root,
            ),
        ):
            result = handle_reconcile_mode(
                config, run_dir, "review-fields", "2026-03-04T00:00:00Z", ["PRD-TEST-001"],
            )
        assert "review_id" in result
        assert "verdict" in result
        assert "mismatches" in result
        assert "prd_count" in result
        assert "total_frs" in result
        assert "mismatch_count" in result
        assert result["total_frs"] == 2


# ---------------------------------------------------------------------------
# trw_review tool dispatch: mode="reconcile"
# ---------------------------------------------------------------------------


class TestTrwReviewReconcileDispatch:
    """trw_review tool dispatch: mode='reconcile' reaches handle_reconcile_mode."""

    @pytest.mark.unit
    def test_reconcile_mode_dispatches_to_handler(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    ) -> None:
        """mode='reconcile' dispatches to handle_reconcile_mode."""
        from tests._ceremony_helpers import make_ceremony_server

        tools = make_ceremony_server(monkeypatch, tmp_path)

        # Create run dir for phase update
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
                "trw_mcp.tools._review_helpers.handle_reconcile_mode",
                return_value={"verdict": "clean", "mismatches": []},
            ) as mock_handler,
            patch("trw_mcp.state._paths.find_active_run", return_value=run_d),
        ):
            trw_review = tools["trw_review"]
            result = trw_review.fn(mode="reconcile", prd_ids=["PRD-TEST-001"])

        mock_handler.assert_called_once()
        call_args = mock_handler.call_args
        assert call_args[1].get("prd_ids") == ["PRD-TEST-001"] or call_args[0][4] == ["PRD-TEST-001"]


# ---------------------------------------------------------------------------
# Phase gate advisory tests
# ---------------------------------------------------------------------------


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
            "run_id: gate-test\nstatus: active\nphase: review\n"
            "task_name: gate-task\nprd_scope:\n  - PRD-TEST-001\n",
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

        # Should have an info advisory about reconciliation not run
        recon_failures = [
            f for f in result.failures if f.field == "spec_reconciliation"
        ]
        assert len(recon_failures) >= 1
        info_advisory = [f for f in recon_failures if f.rule == "reconciliation_not_run"]
        assert len(info_advisory) == 1
        assert info_advisory[0].severity == "info"
        assert "trw_review(mode='reconcile')" in info_advisory[0].message

    @pytest.mark.unit
    def test_review_drift_warning(self, tmp_path: Path) -> None:
        """When reconciliation.yaml has verdict='drift_detected' -> warning."""
        from trw_mcp.models.run import Phase
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

        # Write a reconciliation.yaml with drift
        from trw_mcp.state.persistence import FileStateWriter

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

        recon_failures = [
            f for f in result.failures if f.field == "spec_reconciliation"
        ]
        assert len(recon_failures) >= 1
        drift_warning = [f for f in recon_failures if f.rule == "spec_drift_detected"]
        assert len(drift_warning) == 1
        assert drift_warning[0].severity == "warning"
        assert "2 identifier(s)" in drift_warning[0].message

    @pytest.mark.unit
    def test_review_clean_reconciliation_no_warning(self, tmp_path: Path) -> None:
        """When reconciliation.yaml has verdict='clean' -> no reconciliation failure."""
        from trw_mcp.models.run import Phase
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

        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        recon_data: dict[str, object] = {
            "review_id": "review-clean",
            "verdict": "clean",
            "mismatches": [],
        }
        writer.write_yaml(meta / "reconciliation.yaml", recon_data)

        config = TRWConfig()
        result = check_phase_exit(Phase.REVIEW, run_d, config)

        recon_failures = [
            f for f in result.failures if f.field == "spec_reconciliation"
        ]
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

        recon_failures = [
            f for f in result.failures if f.field == "spec_reconciliation"
        ]
        assert len(recon_failures) == 0
