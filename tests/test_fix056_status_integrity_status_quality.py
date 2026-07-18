"""Status drift and FR annotation tests for FIX-056."""

from __future__ import annotations

import textwrap
from unittest.mock import patch

import pytest

from tests._fix056_status_integrity_support import (
    _FR_WITH_STATUS,
    _FR_WITHOUT_STATUS,
    _make_prd,
)


class TestStatusDriftDetection:
    """Tests for _check_status_drift helper and its integration with validate_prd_quality_v2."""

    def test_no_drift_matching_status(self) -> None:
        """When frontmatter and prose status agree (case-insensitive), no warnings."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="draft", prose_status="Draft")
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert warnings == [], f"Expected no drift warnings, got: {warnings}"

    def test_no_drift_case_insensitive(self) -> None:
        """Case-insensitive comparison: 'done' matches 'Done'."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="done", prose_status="Done")
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert warnings == []

    def test_drift_detected_mismatch(self) -> None:
        """When frontmatter status differs from prose, a warning is returned."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="done", prose_status="Draft")
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert len(warnings) == 1
        assert "Status drift" in warnings[0]
        assert "done" in warnings[0]
        assert "draft" in warnings[0]

    def test_drift_detected_in_quick_reference_without_colon(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = _make_prd(fm_status="done", prose_status="Draft").replace(
            "**Quick Reference**:", "**Quick Reference**"
        )

        warnings = _check_status_drift(parse_frontmatter(content), content)

        assert len(warnings) == 1
        assert "Status drift" in warnings[0]

    def test_no_drift_no_quick_reference_block(self) -> None:
        """When no prose Quick Reference block exists, drift check skips gracefully."""
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

        content = textwrap.dedent("""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: done
              priority: P1
              category: TEST
            ---

            # PRD-TEST-001: Test

            ## 4. Functional Requirements

            No status line anywhere in the body prose.
        """)
        fm = parse_frontmatter(content)
        warnings = _check_status_drift(fm, content)
        assert warnings == [], "Should skip gracefully when no prose status line found"

    def test_unrelated_requirement_status_is_not_quick_reference_drift(self) -> None:
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.prd_quality import _check_status_drift

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

            # PRD-TEST-001: Test

            ## Functional Requirements

            ### FR-1: Example
            - **Status**: done
        """)

        assert _check_status_drift(parse_frontmatter(content), content) == []

    def test_validate_v2_includes_drift_warnings(self) -> None:
        """validate_prd_quality_v2 populates status_drift_warnings when drift found."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(fm_status="done", prose_status="Draft")
        result = validate_prd_quality_v2(content)
        assert isinstance(result.status_drift_warnings, list)
        drift_msgs = [w for w in result.status_drift_warnings if "Status drift" in w]
        assert len(drift_msgs) >= 1, "Expected at least one drift warning from v2 validation"

    def test_validate_v2_no_drift_warnings_when_consistent(self) -> None:
        """validate_prd_quality_v2 produces no status drift warnings when status consistent."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(fm_status="draft", prose_status="Draft")
        result = validate_prd_quality_v2(content)
        drift_msgs = [w for w in result.status_drift_warnings if "Status drift" in w]
        assert drift_msgs == [], f"Expected no drift warnings, got: {drift_msgs}"

    @pytest.mark.parametrize("failing_check", ["status_drift", "fr_annotations", "partially_implemented"])
    def test_integrity_detectors_fail_open_independently(self, failing_check: str) -> None:
        """One broken advisory detector must not suppress later warnings."""
        from trw_mcp.state.validation.prd_quality import validate_prd_quality_v2

        content = _make_prd(fm_status="draft", prose_status="Draft")
        with (
            patch("trw_mcp.state.validation.prd_quality._check_status_drift") as status_drift,
            patch("trw_mcp.state.validation.prd_quality._check_fr_annotations") as fr_annotations,
            patch("trw_mcp.state.validation.prd_quality._check_partially_implemented") as partially_implemented,
        ):
            checks = {
                "status_drift": status_drift,
                "fr_annotations": fr_annotations,
                "partially_implemented": partially_implemented,
            }
            for name, check in checks.items():
                check.return_value = [f"{name} warning"]
            checks[failing_check].side_effect = RuntimeError("broken")
            result = validate_prd_quality_v2(content, include_dynamic_checks=False)

        assert result.status_drift_warnings == [
            f"{name} warning"
            for name in ("status_drift", "fr_annotations", "partially_implemented")
            if name != failing_check
        ]


class TestFRStatusAnnotation:
    """Tests for FR-level **Status**: active injection in _substitute_template."""

    def test_generated_body_contains_fr_status(self) -> None:
        """Generated PRD body includes **Status**: active in each FR block."""
        from trw_mcp.tools.requirements import _generate_prd_body

        body = _generate_prd_body(
            "PRD-TEST-001",
            "Test PRD",
            "Input text for test",
            "TEST",
            priority="P1",
            confidence=0.7,
        )
        assert body.count("**Status**: active") == 1

    def test_status_annotation_follows_priority(self) -> None:
        """**Status**: active appears immediately after **Priority**: in FR blocks."""
        from trw_mcp.tools.requirements import _generate_prd_body

        body = _generate_prd_body(
            "PRD-TEST-002",
            "Another Test",
            "More input text",
            "CORE",
            priority="P2",
            confidence=0.6,
        )
        lines = body.splitlines()
        for i, line in enumerate(lines):
            if "**Priority**: Must Have" in line or "**Priority**: Should Have" in line:
                next_lines = [candidate for candidate in lines[i + 1 : i + 3] if candidate.strip()]
                if next_lines:
                    assert "**Status**: active" in next_lines[0], (
                        f"Expected **Status**: active after priority line at line {i}, got: {next_lines[0]!r}"
                    )
                break

    def test_fr_annotation_warning_in_validation(self) -> None:
        """Validation warns when an FR section lacks a **Status**: annotation."""
        from trw_mcp.state.validation.prd_quality import _check_fr_annotations

        content = _make_prd(fr_content=_FR_WITHOUT_STATUS)
        warnings = _check_fr_annotations(content)
        assert len(warnings) >= 1, "Expected at least one FR annotation warning"
        assert "FR annotation missing" in warnings[0]

    def test_fr_annotation_no_warning_when_present(self) -> None:
        """No FR annotation warnings when all FR sections have **Status**: lines."""
        from trw_mcp.state.validation.prd_quality import _check_fr_annotations

        content = _make_prd(fr_content=_FR_WITH_STATUS)
        warnings = _check_fr_annotations(content)
        assert warnings == [], f"Expected no FR annotation warnings, got: {warnings}"

    def test_no_fr_section_no_annotation_warning(self) -> None:
        """When there's no Functional Requirements section, no annotation warnings."""
        from trw_mcp.state.validation.prd_quality import _check_fr_annotations

        content = "# PRD\n\nSome body without FR sections.\n"
        warnings = _check_fr_annotations(content)
        assert warnings == []
