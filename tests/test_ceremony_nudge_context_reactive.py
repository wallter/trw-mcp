"""Tests for ceremony nudge context-reactive messaging."""

from __future__ import annotations

from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    NudgeContext,
    ToolName,
    _context_reactive_message,
    compute_nudge,
)


class TestFR02NudgeContext:
    """FR02: NudgeContext dataclass."""

    def test_fr02_nudge_context_defaults(self) -> None:
        """NudgeContext has sensible defaults."""
        ctx = NudgeContext()
        assert ctx.tool_name == ""
        assert ctx.tool_success is True
        assert ctx.build_passed is None
        assert ctx.review_verdict is None
        assert ctx.review_p0_count == 0
        assert ctx.is_subagent is False

    def test_fr02_nudge_context_custom(self) -> None:
        """NudgeContext accepts all fields."""
        ctx = NudgeContext(
            tool_name="build_check",
            tool_success=True,
            build_passed=True,
            review_verdict="pass",
            review_p0_count=0,
            is_subagent=True,
        )
        assert ctx.tool_name == "build_check"
        assert ctx.is_subagent is True


class TestFR03ContextReactiveMessages:
    """FR03: Context-reactive messages based on tool/result combinations."""

    def test_fr03_build_check_failed_message(self) -> None:
        """build_check with build_passed=False returns design flaw message."""
        ctx = NudgeContext(tool_name="build_check", build_passed=False)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Build failed" in msg
        assert "PLAN" in msg

    def test_fr03_build_check_passed_message(self) -> None:
        """build_check with build_passed=True returns review next message."""
        ctx = NudgeContext(tool_name="build_check", build_passed=True)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "review" in msg.lower()

    def test_fr03_review_with_p0s_message(self) -> None:
        """review with p0_count > 0 returns remediation message."""
        ctx = NudgeContext(tool_name="review", review_p0_count=3)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "P0" in msg
        assert "separate agent" in msg.lower() or "remediate" in msg.lower()

    def test_fr03_review_no_p0s_message(self) -> None:
        """review with p0_count == 0 returns deliver next message."""
        ctx = NudgeContext(tool_name="review", review_p0_count=0)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "deliver" in msg.lower()

    def test_fr03_checkpoint_message(self) -> None:
        """checkpoint tool returns progress saved message."""
        ctx = NudgeContext(tool_name="checkpoint")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Progress saved" in msg

    def test_fr03_learn_message(self) -> None:
        """learn tool returns learning persisted message."""
        ctx = NudgeContext(tool_name="learn")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Learning persisted" in msg

    def test_fr03_session_start_message(self) -> None:
        """session_start tool returns FRAMEWORK.md next message."""
        ctx = NudgeContext(tool_name="session_start")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "FRAMEWORK" in msg

    def test_fr03_deliver_message(self) -> None:
        """deliver tool returns session complete message."""
        ctx = NudgeContext(tool_name="deliver")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Session complete" in msg

    def test_fr03_init_message(self) -> None:
        """init tool returns run bootstrapped message."""
        ctx = NudgeContext(tool_name="init")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Run bootstrapped" in msg

    def test_fr03_recall_message(self) -> None:
        """recall tool returns learnings recalled message."""
        ctx = NudgeContext(tool_name="recall")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Learnings recalled" in msg

    def test_fr03_unknown_tool_returns_none(self) -> None:
        """Unknown tool_name returns None (triggers fallback)."""
        ctx = NudgeContext(tool_name="unknown_tool")
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is None

    def test_fr03_prd_create_message(self) -> None:
        """prd_create tool context returns message mentioning trw_prd_validate."""
        ctx = NudgeContext(tool_name=ToolName.PRD_CREATE)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "trw_prd_validate" in msg

    def test_fr03_prd_validate_message(self) -> None:
        """prd_validate tool context returns message mentioning trw_init."""
        ctx = NudgeContext(tool_name=ToolName.PRD_VALIDATE)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "trw_init" in msg

    def test_fr03_status_message(self) -> None:
        """status tool context returns message mentioning Resume."""
        ctx = NudgeContext(tool_name=ToolName.STATUS)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state)
        assert msg is not None
        assert "Resume" in msg

    def test_fr03_compute_nudge_uses_context(self) -> None:
        """compute_nudge with context returns non-empty content."""
        state = CeremonyState(session_started=True, checkpoint_count=1)
        ctx = NudgeContext(tool_name="checkpoint")
        result = compute_nudge(state, context=ctx)
        assert "TRW" in result
        assert len(result) > 0


class TestFR06ProgressiveUrgencyDirectiveness:
    """FR06: Progressive urgency directiveness for context-reactive messages."""

    def test_fr06_low_urgency_no_rfc_terms(self) -> None:
        """Low urgency context message has no RFC 2119 terms."""
        ctx = NudgeContext(tool_name="build_check", build_passed=True)
        state = CeremonyState(nudge_counts={})
        msg = _context_reactive_message(ctx, state, urgency="low")
        assert msg is not None
        assert "SHOULD" not in msg
        assert "recommended" not in msg.lower()

    def test_fr06_medium_urgency_advisory(self) -> None:
        """Medium urgency context message uses advisory language."""
        ctx = NudgeContext(tool_name="build_check", build_passed=True)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state, urgency="medium")
        assert msg is not None
        assert "recommended" in msg.lower()

    def test_fr06_high_urgency_directive(self) -> None:
        """High urgency context message uses directive language."""
        ctx = NudgeContext(tool_name="build_check", build_passed=True)
        state = CeremonyState()
        msg = _context_reactive_message(ctx, state, urgency="high")
        assert msg is not None
        assert "SHOULD" in msg

    def test_fr06_first_nudge_concise(self) -> None:
        """nudge_count=0 (first time) -> message is concise, no urgency escalation."""
        state = CeremonyState(
            session_started=True,
            phase="validate",
            build_check_result="failed",
            nudge_counts={},
        )
        ctx = NudgeContext(tool_name="build_check", build_passed=False)
        msg = _context_reactive_message(ctx, state, urgency="low")
        assert msg is not None
        assert len(msg) < 250
        assert "SHOULD" not in msg
