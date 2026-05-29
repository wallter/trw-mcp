"""Tests for ceremony nudge assembly, suppression paths, and compatibility."""

from __future__ import annotations

from pathlib import Path

from tests._ceremony_nudge_support import _trw_dir
from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    NudgeContext,
    ToolName,
    _assemble_nudge,
    compute_nudge,
    write_ceremony_state,
)


class TestFR09ComponentAwareTruncation:
    """FR09: Component-aware truncation replaces hardcoded truncation."""

    def test_fr09_assemble_nudge_basic(self) -> None:
        """_assemble_nudge assembles status + reactive message."""
        result = _assemble_nudge("status line", "reactive message")
        assert "status line" in result
        assert "reactive message" in result

    def test_fr09_assemble_nudge_optional_components(self) -> None:
        """_assemble_nudge includes optional components within budget."""
        result = _assemble_nudge(
            "status",
            "msg",
            next_then="NEXT: foo THEN: bar",
            reversion="reversion hint",
            budget=600,
        )
        assert "NEXT" in result
        assert "reversion" in result

    def test_fr09_assemble_nudge_respects_budget(self) -> None:
        """_assemble_nudge drops optional components that exceed budget."""
        long_next = "N" * 500
        result = _assemble_nudge(
            "status",
            "msg",
            next_then=long_next,
            budget=100,
        )
        assert long_next not in result
        assert "status" in result

    def test_fr09_assemble_nudge_no_reactive(self) -> None:
        """_assemble_nudge works with None reactive message."""
        result = _assemble_nudge("status", None)
        assert result == "status"

    def test_fr09_compute_nudge_uses_assembly(self) -> None:
        """compute_nudge uses component-aware assembly instead of truncation."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="implement",
        )
        ctx = NudgeContext(tool_name="checkpoint")
        result = compute_nudge(state, context=ctx)
        assert isinstance(result, str)
        assert len(result) <= 600

    def test_fr09_compute_nudge_budget_600(self) -> None:
        """compute_nudge respects 600-char budget with context."""
        state = CeremonyState(session_started=False)
        ctx = NudgeContext(tool_name="session_start")
        result = compute_nudge(state, context=ctx)
        assert len(result) <= 600

    def test_fr09_truncation_order(self) -> None:
        """Reversion prompt dropped before THEN step in truncation.

        Budget is set so that status+reactive+then_step fit, but adding
        reversion would exceed it. Verifies reversion is dropped first.
        """
        status = "\u2713 s | \u2713 c | \u2713 b | \u2713 r | \u2713 d"
        reactive = "Build failed. " + "x" * 200
        then_step = "THEN: trw_deliver()"
        reversion = "Consider reverting to PLAN."
        result = _assemble_nudge(status, reactive, next_then=then_step, reversion=reversion, budget=280)
        assert status in result
        assert "Build failed" in result
        assert then_step in result
        assert reversion not in result


class TestC103BuildStatusLineAndReversionSuppression:
    """C1-03: Test coverage for deferred import + reversion suppression path."""

    def test_build_status_line_complete_steps(self) -> None:
        """_build_status_line shows checkmarks for completed steps.

        Exercises the deferred import of _step_complete from _nudge_rules
        inside _build_status_line (_nudge_messages module).
        """
        from trw_mcp.state._nudge_messages import _build_status_line

        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            files_modified_since_checkpoint=0,
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
        )
        line = _build_status_line(state)
        assert line.count("\u2713") == 5
        assert "\u2717" not in line

    def test_build_status_line_incomplete_steps(self) -> None:
        """_build_status_line shows crosses for incomplete steps with annotations."""
        from trw_mcp.state._nudge_messages import _build_status_line

        state = CeremonyState(
            session_started=False,
            checkpoint_count=0,
            files_modified_since_checkpoint=5,
            build_check_result="",
            review_called=False,
            deliver_called=False,
            learnings_this_session=3,
        )
        line = _build_status_line(state)
        assert line.count("\u2717") == 5
        assert "\u2713" not in line
        assert "5 files modified" in line
        assert "3 learnings pending" in line

    def test_compute_nudge_build_check_context_reversion_is_none(self) -> None:
        """compute_nudge with BUILD_CHECK context returns non-empty content.

        PRD-CORE-129: Pool-based selection. When context pool is selected,
        the reactive message is used. Reversion prompt should not appear
        as a separate line.
        """
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="validate",
            build_check_result="",
        )
        ctx = NudgeContext(tool_name=ToolName.BUILD_CHECK, build_passed=True)
        result = compute_nudge(state, context=ctx)
        assert "TRW" in result
        assert "If failures reveal a design flaw, revert to PLAN. If implementation bugs, fix in-phase." not in result

    def test_compute_nudge_review_context_reversion_is_none(self) -> None:
        """compute_nudge suppresses reversion field when tool_name is REVIEW.

        The REVIEW reactive message already includes reversion guidance for P0s.
        """
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="review",
            review_called=True,
        )
        ctx = NudgeContext(tool_name=ToolName.REVIEW, review_p0_count=0)
        result = compute_nudge(state, context=ctx)
        assert "deliver" in result.lower()
        assert "revert to PLAN" not in result


class TestBackwardsCompatibility:
    """Existing signatures remain backwards-compatible."""

    def test_compute_nudge_no_context(self) -> None:
        """compute_nudge works without context (backwards compat)."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=5)
        assert "start" in result.lower()
        assert len(result) > 0

    def test_compute_nudge_failopen_with_context(self) -> None:
        """compute_nudge with context still fail-open on errors."""
        state = CeremonyState()
        from trw_mcp.state import ceremony_nudge

        original = ceremony_nudge._build_minimal_status_line
        try:

            def _broken(s: CeremonyState) -> str:
                raise RuntimeError("simulated error")

            ceremony_nudge._build_minimal_status_line = _broken  # type: ignore[assignment]
            ctx = NudgeContext(tool_name="checkpoint")
            result = compute_nudge(state, context=ctx)
            assert result == ""
        finally:
            ceremony_nudge._build_minimal_status_line = original

    def test_append_ceremony_nudge_without_context(self, tmp_path: Path) -> None:
        """append_ceremony_nudge still works without context (backwards compat)."""
        from trw_mcp.tools._legacy_ceremony_nudge import append_ceremony_nudge

        trw = _trw_dir(tmp_path)
        write_ceremony_state(trw, CeremonyState())
        response: dict[str, object] = {"status": "ok"}
        result = append_ceremony_nudge(response.copy(), trw_dir=trw)
        assert "ceremony_status" in result

    def test_existing_static_messages_unchanged(self) -> None:
        """compute_nudge returns non-empty content when session not started."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=5)
        assert "TRW" in result
        assert len(result) > 0
