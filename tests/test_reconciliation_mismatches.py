"""Tests for reconciliation FR mismatch extraction and PRD counting."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.tools._review_helpers import _count_frs_in_prd, _extract_fr_mismatches

from ._reconciliation_support import (
    SAMPLE_DIFF_REMOVED_ONLY,
    SAMPLE_DIFF_WITH_MATCHES,
    SAMPLE_DIFF_WITHOUT_MATCHES,
    SAMPLE_PRD,
)


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
        assert fr_refs & {"FR01", "FR02"}

    @pytest.mark.unit
    def test_no_functional_requirements_section_returns_empty(self) -> None:
        content = "# Just a title\n\nSome content without FR section."
        mismatches = _extract_fr_mismatches(content, "PRD-X", "any diff")
        assert mismatches == []

    @pytest.mark.unit
    def test_empty_diff_produces_mismatches_for_all_identifiers(self) -> None:
        mismatches = _extract_fr_mismatches(SAMPLE_PRD, "PRD-TEST-001", "")
        assert len(mismatches) > 0

    @pytest.mark.unit
    def test_removed_lines_not_counted_as_present(self) -> None:
        """Identifiers only in removed (-) lines should still be mismatches.

        Regression test for P1-1: deleted code should not mask spec drift.
        """
        mismatches = _extract_fr_mismatches(
            SAMPLE_PRD,
            "PRD-TEST-001",
            SAMPLE_DIFF_REMOVED_ONLY,
        )
        mismatch_idents = {m["identifier"] for m in mismatches}
        assert "UserValidator" in mismatch_idents
        assert "ValidationResult" in mismatch_idents


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
