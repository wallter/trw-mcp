"""Warning and model-field coverage tests for FIX-056."""

from __future__ import annotations

import textwrap

from tests._fix056_status_integrity_support import _make_prd


class TestPartiallyImplementedFRsWarning:
    """Tests for _check_partially_implemented in prd_quality.py."""

    def test_warning_when_done_with_partial_frs(self) -> None:
        """PRD marked 'done' with partially_implemented_frs produces a warning."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {"status": "done", "partially_implemented_frs": ["FR03"]}
        warnings = _check_partially_implemented(fm)
        assert len(warnings) == 1
        assert "FR03" in warnings[0]
        assert "partially implemented" in warnings[0].lower()

    def test_warning_names_all_partial_frs(self) -> None:
        """Warning message includes all deferred FR IDs."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {
            "status": "done",
            "partially_implemented_frs": ["FR03", "FR06"],
        }
        warnings = _check_partially_implemented(fm)
        assert "FR03" in warnings[0]
        assert "FR06" in warnings[0]

    def test_no_warning_when_not_done(self) -> None:
        """No warning when status is not 'done'."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        for status in ["draft", "review", "approved", "implemented", "deprecated"]:
            fm: dict[str, object] = {
                "status": status,
                "partially_implemented_frs": ["FR03"],
            }
            warnings = _check_partially_implemented(fm)
            assert warnings == [], f"Expected no warning for status={status!r}"

    def test_no_warning_when_partial_frs_empty(self) -> None:
        """No warning when partially_implemented_frs is an empty list."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {"status": "done", "partially_implemented_frs": []}
        warnings = _check_partially_implemented(fm)
        assert warnings == []

    def test_no_warning_when_partial_frs_missing(self) -> None:
        """No warning when partially_implemented_frs key is absent."""
        from trw_mcp.state.validation.prd_quality import _check_partially_implemented

        fm: dict[str, object] = {"status": "done"}
        warnings = _check_partially_implemented(fm)
        assert warnings == []

    def test_validate_v2_includes_partial_frs_warning(self) -> None:
        """validate_prd_quality_v2 includes partial FR warnings in status_drift_warnings."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(
            fm_status="done",
            prose_status="Done",
            partial_frs="[FR03]",
        )
        result = validate_prd_quality_v2(content)
        partial_msgs = [
            warning for warning in result.status_drift_warnings if "partially implemented" in warning.lower()
        ]
        assert len(partial_msgs) >= 1, (
            f"Expected partial FR warning in status_drift_warnings, got: {result.status_drift_warnings}"
        )


class TestModelFields:
    """Verify new fields are present on the models."""

    def test_validation_result_v2_has_status_drift_warnings(self) -> None:
        """ValidationResultV2 has status_drift_warnings field defaulting to []."""
        from trw_mcp.models.requirements import ValidationResultV2

        result = ValidationResultV2()
        assert hasattr(result, "status_drift_warnings")
        assert result.status_drift_warnings == []

    def test_prd_frontmatter_has_approved_by(self) -> None:
        """PRDFrontmatter has approved_by field defaulting to None."""
        from trw_mcp.models.requirements import PRDFrontmatter

        fm = PRDFrontmatter(id="PRD-TEST-001", title="Test")
        assert hasattr(fm, "approved_by")
        assert fm.approved_by is None

    def test_prd_frontmatter_has_partially_implemented_frs(self) -> None:
        """PRDFrontmatter has partially_implemented_frs field defaulting to []."""
        from trw_mcp.models.requirements import PRDFrontmatter

        fm = PRDFrontmatter(id="PRD-TEST-001", title="Test")
        assert hasattr(fm, "partially_implemented_frs")
        assert fm.partially_implemented_frs == []

    def test_prd_frontmatter_backward_compatible_without_new_fields(self) -> None:
        """Existing PRDs without approved_by or partially_implemented_frs still parse."""
        from trw_mcp.state.prd_utils import parse_frontmatter

        content = textwrap.dedent("""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: draft
              priority: P1
              category: TEST
            ---
            # Body
        """)
        fm = parse_frontmatter(content)
        assert fm.get("id") == "PRD-TEST-001"
