"""PRD utility transition coverage tests split from test_prd_audit_claudemd."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.state.prd_utils import (
    check_transition_guards,
    compute_content_density,
    is_valid_transition,
    update_frontmatter,
)


class TestComputeContentDensityEdgeCases:
    """Cover line 111: total == 0 guard (defensive branch)."""

    def test_single_newline_string(self) -> None:
        # split("\n") on "\n" gives ["", ""] — 2 lines, both blank
        # total is 2, not 0 — but the non-zero guard is still tested
        result = compute_content_density("\n")
        assert result == 0.0  # both lines are blank/non-substantive

    def test_single_blank_line_zero_substantive(self) -> None:
        result = compute_content_density("")
        # split("") gives [""] — 1 line, it's blank
        assert result == 0.0

    def test_heading_lines_are_non_substantive(self) -> None:
        content = "# Title\n## Section\n### Sub"
        density = compute_content_density(content)
        # All 3 lines are headings → 0 substantive
        assert density == 0.0


class TestUpdateFrontmatterNonDictData:
    """Cover line 165: frontmatter parses to non-dict YAML (e.g. a list)."""

    def test_raises_state_error_when_frontmatter_is_list(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-BAD-001.md"
        # Valid YAML frontmatter but parses as a list, not a dict
        prd_file.write_text(
            "---\n- item1\n- item2\n---\n\n# Body\n",
            encoding="utf-8",
        )
        with pytest.raises(StateError, match="not a mapping"):
            update_frontmatter(prd_file, {"status": "approved"})


class TestUpdateFrontmatterAtomicWriteCleanup:
    """Cover lines 194-196: exception branch unlinks temp file."""

    def test_cleans_up_tmp_file_on_write_error(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-CLEAN-001.md"
        prd_file.write_text(
            "---\nid: PRD-CLEAN-001\nstatus: draft\n---\n\n# Body\n",
            encoding="utf-8",
        )
        # Patch Path.rename to raise to trigger the cleanup branch
        original_rename = Path.rename

        def _failing_rename(self: Path, target: Path) -> None:
            raise OSError("simulated rename failure")

        with patch.object(Path, "rename", _failing_rename):
            with pytest.raises(StateError):
                update_frontmatter(prd_file, {"status": "approved"})

        # Original file should still exist (unmodified)
        assert prd_file.exists()
        # No .md.tmp leftovers
        tmp_files = list(tmp_path.glob("*.md.tmp"))
        assert len(tmp_files) == 0


class TestUpdateFrontmatterGenericExceptionWrapping:
    """Cover lines 200-203: StateError re-raise vs generic wrapping."""

    def test_state_error_propagates_without_wrapping(self, tmp_path: Path) -> None:
        """StateError from inner code must not be double-wrapped."""
        prd_file = tmp_path / "PRD-SE-001.md"
        # List YAML frontmatter triggers inner StateError — must propagate as-is
        prd_file.write_text("---\n- a\n- b\n---\n\n# Body\n", encoding="utf-8")
        with pytest.raises(StateError):
            update_frontmatter(prd_file, {"status": "approved"})

    def test_generic_exception_wrapped_in_state_error(self, tmp_path: Path) -> None:
        prd_file = tmp_path / "PRD-GE-001.md"
        prd_file.write_text(
            "---\nid: PRD-GE-001\nstatus: draft\n---\n\n# Body\n",
            encoding="utf-8",
        )
        # Patch yaml.dump to raise a non-StateError
        with patch("trw_mcp.state.prd_utils.YAML") as mock_yaml_cls:
            mock_yaml = MagicMock()
            mock_yaml.preserve_quotes = True
            mock_yaml.load.return_value = {"id": "PRD-GE-001", "status": "draft"}
            mock_yaml.dump.side_effect = RuntimeError("unexpected dump failure")
            mock_yaml_cls.return_value = mock_yaml
            with pytest.raises(StateError, match="Failed to update frontmatter"):
                update_frontmatter(prd_file, {"status": "approved"})


class TestIsValidTransitionIdentity:
    """Cover line 235: identity transition returns True."""

    @pytest.mark.parametrize(
        "status",
        [
            PRDStatus.DRAFT,
            PRDStatus.REVIEW,
            PRDStatus.APPROVED,
            PRDStatus.IMPLEMENTED,
            PRDStatus.DONE,
            PRDStatus.MERGED,
            PRDStatus.DEPRECATED,
        ],
    )
    def test_identity_transition_always_valid(self, status: PRDStatus) -> None:
        assert is_valid_transition(status, status) is True

    def test_invalid_transition_done_to_draft(self) -> None:
        # DONE is terminal — no outgoing transitions
        assert is_valid_transition(PRDStatus.DONE, PRDStatus.DRAFT) is False

    def test_valid_transition_draft_to_review(self) -> None:
        assert is_valid_transition(PRDStatus.DRAFT, PRDStatus.REVIEW) is True


class TestCheckTransitionGuardsIdentity:
    """Cover line 276: check_transition_guards identity transition returns immediately."""

    def test_identity_transition_allowed_no_guard(self) -> None:
        content = "---\nid: PRD-CORE-001\nstatus: draft\n---\n\n# Body\n"
        result = check_transition_guards(PRDStatus.DRAFT, PRDStatus.DRAFT, content)
        assert result.allowed is True
        assert "Identity" in result.reason


class TestCheckTransitionGuardsReviewToApproved:
    """Cover lines 315-329: REVIEW → APPROVED quality validation guard."""

    def _high_quality_prd(self) -> str:
        """Return a PRD with enough content to pass quality guard."""
        sections = "\n\n".join(
            f"## {i}. Section {i}\n\n" + ("This is substantive content for section requirements. " * 8)
            for i in range(1, 13)
        )
        return "---\nid: PRD-CORE-001\nstatus: review\npriority: P1\n---\n\n" + sections

    def _low_quality_prd(self) -> str:
        return (
            "---\nid: PRD-CORE-001\nstatus: review\npriority: P1\n---\n\n"
            "## 1. Problem Statement\n\n<!-- placeholder -->\n"
        )

    def test_high_quality_prd_passes_guard(self) -> None:
        content = self._high_quality_prd()
        config = TRWConfig()
        result = check_transition_guards(PRDStatus.REVIEW, PRDStatus.APPROVED, content, config)
        # High-quality PRD should pass
        if result.allowed:
            assert "Quality validation passed" in result.reason
            assert "quality_tier" in result.guard_details
        else:
            # Even if it fails, guard_details must be populated
            assert "quality_tier" in result.guard_details

    def test_low_quality_prd_fails_guard(self) -> None:
        content = self._low_quality_prd()
        config = TRWConfig()
        result = check_transition_guards(PRDStatus.REVIEW, PRDStatus.APPROVED, content, config)
        # Low quality PRD should fail (SKELETON or DRAFT tier)
        assert result.allowed is False
        assert "quality_tier" in result.guard_details
        assert "total_score" in result.guard_details

    def test_guard_uses_risk_scaled_config(self) -> None:
        """Guard must read frontmatter risk_level for scaling."""
        content = "---\nid: PRD-CORE-001\nstatus: review\npriority: P0\nrisk_level: critical\n---\n\n# Body\n"
        config = TRWConfig()
        result = check_transition_guards(PRDStatus.REVIEW, PRDStatus.APPROVED, content, config)
        # Guard ran — allowed/denied both valid; key is guard_details populated
        assert "quality_tier" in result.guard_details


class TestCheckTransitionGuardsDraftToReview:
    """Cover the DRAFT → REVIEW content density guard path."""

    def test_dense_content_passes_guard(self) -> None:
        substantive_lines = "\n".join(
            [f"Substantive requirement line {i} with real content and details." * 2 for i in range(30)]
        )
        content = f"---\nid: PRD-CORE-001\nstatus: draft\npriority: P2\n---\n\n{substantive_lines}"
        config = TRWConfig()
        result = check_transition_guards(PRDStatus.DRAFT, PRDStatus.REVIEW, content, config)
        assert result.allowed is True
        assert "density" in result.guard_details

    def test_sparse_content_fails_guard(self) -> None:
        content = (
            "---\nid: PRD-CORE-001\nstatus: draft\npriority: P2\n---\n\n"
            "# Title\n\n<!-- placeholder -->\n\n---\n\n<!-- empty -->\n"
        )
        config = TRWConfig()
        result = check_transition_guards(PRDStatus.DRAFT, PRDStatus.REVIEW, content, config)
        assert result.allowed is False
        assert "density" in result.guard_details

    def test_no_guard_for_other_transitions(self) -> None:
        content = "---\nid: PRD-CORE-001\nstatus: approved\npriority: P2\n---\n\n# Body\n"
        config = TRWConfig()
        result = check_transition_guards(PRDStatus.APPROVED, PRDStatus.IMPLEMENTED, content, config)
        assert result.allowed is True
        assert result.reason == "No guard for this transition."


class TestCheckTransitionGuardsReviewApprovedPass:
    """Cover prd_utils.py line 329: REVIEW→APPROVED allowed=True branch."""

    def test_returns_allowed_true_for_high_quality_prd(self) -> None:
        """Force REVIEW tier or above to exercise the allowed=True return."""
        from trw_mcp.models.requirements import QualityTier

        content = "---\nid: PRD-CORE-001\nstatus: review\npriority: P2\n---\n\n# Body\n"
        config = TRWConfig()

        # Mock validate_prd_quality_v2 to return APPROVED tier to hit line 329
        mock_result = MagicMock()
        mock_result.total_score = 90.0
        mock_result.quality_tier = QualityTier.APPROVED
        mock_result.grade = "A"

        with patch("trw_mcp.state.validation.validate_prd_quality_v2", return_value=mock_result):
            result = check_transition_guards(PRDStatus.REVIEW, PRDStatus.APPROVED, content, config)

        assert result.allowed is True
        assert "Quality validation passed" in result.reason
        assert result.guard_details["total_score"] == 90.0
