"""State-machine and transition-guard tests for FIX-056."""

from __future__ import annotations

import textwrap


class TestStatusStateMachine:
    """Tests for validate_status_transition() in prd_status.py."""

    def test_allowed_transitions(self) -> None:
        """Valid transitions return True — derived from canonical VALID_TRANSITIONS in prd_utils.py."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        valid_pairs = [
            ("draft", "review"),
            ("draft", "merged"),
            ("review", "draft"),
            ("review", "approved"),
            ("review", "merged"),
            ("approved", "implemented"),
            ("approved", "deprecated"),
            ("approved", "merged"),
            ("implemented", "done"),
            ("implemented", "deprecated"),
        ]
        for current, target in valid_pairs:
            assert validate_status_transition(current, target), f"Expected {current} -> {target} to be allowed"

    def test_invalid_transitions(self) -> None:
        """Invalid transitions return False — derived from canonical state machine."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        invalid_pairs = [
            ("done", "draft"),
            ("done", "review"),
            ("deprecated", "draft"),
            ("deprecated", "done"),
            ("draft", "done"),
            ("draft", "approved"),
            ("merged", "draft"),
            ("approved", "draft"),
        ]
        for current, target in invalid_pairs:
            assert not validate_status_transition(current, target), f"Expected {current} -> {target} to be blocked"

    def test_identity_transitions_always_valid(self) -> None:
        """Same-to-same transitions are always valid."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        for status in ["draft", "review", "approved", "implemented", "done", "deprecated", "merged"]:
            assert validate_status_transition(status, status), (
                f"Identity transition {status} -> {status} should be valid"
            )

    def test_case_insensitive_input(self) -> None:
        """Transition lookup is case-insensitive."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        assert validate_status_transition("Draft", "Review")
        assert validate_status_transition("APPROVED", "IMPLEMENTED")
        assert not validate_status_transition("Done", "Draft")

    def test_unknown_current_status_returns_false(self) -> None:
        """Unknown current status returns False for any non-identity target."""
        from trw_mcp.state.validation.prd_status import validate_status_transition

        assert not validate_status_transition("unknown_status", "review")
        assert not validate_status_transition("foobar", "done")


class TestApprovalWarningGuard:
    """Tests for the approved_by null warning in check_transition_guards."""

    def _make_simple_prd(self, *, approved_by: str | None = None) -> str:
        """Build a minimal PRD string for guard testing."""
        approved_by_line = f"  approved_by: {approved_by}" if approved_by else "  approved_by: null"
        return textwrap.dedent(f"""\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: implemented
              priority: P1
              category: TEST
            {approved_by_line}
            ---

            # PRD-TEST-001: Test
        """)

    def test_null_approved_by_warning_on_terminal_transition(self) -> None:
        """Transition to DONE with null approved_by includes approval_warning."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by=None)
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DONE, content)
        assert result.allowed is True
        assert "approval_warning" in result.guard_details
        assert result.guard_details["approval_warning"] == "approved_by is null on terminal transition"

    def test_null_approved_by_warning_on_deprecated_transition(self) -> None:
        """Transition to DEPRECATED with null approved_by includes approval_warning."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by=None)
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DEPRECATED, content)
        assert result.allowed is True
        assert "approval_warning" in result.guard_details

    def test_no_warning_when_approved_by_set(self) -> None:
        """No approval_warning when approved_by is populated."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by="Tyler")
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DONE, content)
        assert result.allowed is True
        assert "approval_warning" not in result.guard_details, (
            f"Expected no approval_warning but guard_details={result.guard_details}"
        )

    def test_allowed_remains_true_with_warning(self) -> None:
        """The allowed field stays True even when approval_warning is present."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        content = self._make_simple_prd(approved_by=None)
        result = check_transition_guards(PRDStatus.IMPLEMENTED, PRDStatus.DONE, content)
        assert result.allowed is True, "Warning must not block the transition"

    def test_non_terminal_transition_no_approval_warning(self) -> None:
        """Transitions to non-terminal states do not produce approval_warning."""
        from trw_mcp.models.requirements import PRDStatus
        from trw_mcp.state.prd_utils import check_transition_guards

        minimal = textwrap.dedent(
            """\
            ---
            prd:
              id: PRD-TEST-001
              title: Test
              version: '1.0'
              status: draft
              priority: P1
              category: TEST
              approved_by: null
            ---

            # Test

            Long enough content to meet density threshold. """
            + ("x " * 100)
            + """

            ## 1. Problem Statement
            Content here.
            ## 2. Goals & Non-Goals
            Content here.
            ## 3. User Stories
            Content here.
            ## 4. Functional Requirements
            Content here.
            ## 5. Non-Functional Requirements
            Content here.
            ## 6. Technical Approach
            Content here.
            ## 7. Test Strategy
            Content here.
            ## 8. Rollout Plan
            Content here.
            ## 9. Success Metrics
            Content here.
            ## 10. Dependencies & Risks
            Content here.
            ## 11. Open Questions
            Content here.
            ## 12. Traceability Matrix
            Content here.
        """
        )
        result = check_transition_guards(PRDStatus.DRAFT, PRDStatus.REVIEW, minimal)
        assert "approval_warning" not in result.guard_details
