"""Tests for ceremony nudge next-step projection and reversion prompting."""

from __future__ import annotations

from trw_mcp.state.ceremony_nudge import CeremonyState, NudgeContext, _next_two_steps, _reversion_prompt, compute_nudge


class TestFR04NextTwoSteps:
    """FR04: Next-two-steps projection."""

    def test_fr04_early_phase_steps(self) -> None:
        """Early phase: session_start and checkpoint are applicable."""
        state = CeremonyState(
            session_started=False,
            checkpoint_count=0,
            phase="early",
        )
        nxt, then = _next_two_steps(state)
        assert nxt == "session_start"
        assert then == "checkpoint"

    def test_fr04_implement_phase_after_start(self) -> None:
        """Implement phase with session started: checkpoint is next."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
            phase="implement",
        )
        nxt, then = _next_two_steps(state)
        assert nxt == "checkpoint"
        assert then is None

    def test_fr04_validate_phase_all_applicable(self) -> None:
        """Validate phase: session_start, checkpoint, build_check applicable."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result=None,
            phase="validate",
        )
        nxt, then = _next_two_steps(state)
        assert nxt == "build_check"
        assert then is None

    def test_fr04_review_phase_shows_review(self) -> None:
        """Review phase with build passed: review is next."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=False,
            phase="review",
        )
        nxt, then = _next_two_steps(state)
        assert nxt == "review"

    def test_fr04_deliver_phase_all_steps(self) -> None:
        """Deliver phase: all 5 steps applicable."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=True,
            deliver_called=False,
            phase="deliver",
        )
        nxt, then = _next_two_steps(state)
        assert nxt == "deliver"
        assert then is None

    def test_fr04_all_complete_returns_none_none(self) -> None:
        """All steps complete: returns (None, None)."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
            phase="done",
        )
        nxt, then = _next_two_steps(state)
        assert nxt is None
        assert then is None

    def test_fr04_step_rationale_defined(self) -> None:
        """All steps have rationale strings."""
        from trw_mcp.state.ceremony_nudge import _STEP_RATIONALE

        for step in ("session_start", "checkpoint", "build_check", "review", "deliver"):
            assert step in _STEP_RATIONALE
            assert len(_STEP_RATIONALE[step]) > 0

    def test_fr04_fallback_uses_next_two_when_no_context(self) -> None:
        """compute_nudge without context returns non-empty content."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=False,
            phase="review",
        )
        result = compute_nudge(state, context=None)
        assert "TRW" in result
        assert len(result) > 0

    def test_fr04_review_complete_next_deliver(self) -> None:
        """phase=deliver, review_called=True -> NEXT=deliver only."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="deliver",
            build_check_result="passed",
            review_called=True,
        )
        nxt, then = _next_two_steps(state)
        assert nxt == "deliver"
        assert then is None


class TestFR05ReversionPrompting:
    """FR05: Phase reversion active prompting."""

    def test_fr05_build_failed_reversion(self) -> None:
        """Build failure triggers reversion prompt."""
        ctx = NudgeContext(tool_name="build_check", build_passed=False)
        state = CeremonyState()
        prompt = _reversion_prompt(ctx, state)
        assert prompt is not None
        assert "PLAN" in prompt

    def test_fr05_p0_findings_reversion(self) -> None:
        """P0 findings trigger reversion prompt."""
        ctx = NudgeContext(tool_name="review", review_p0_count=2)
        state = CeremonyState()
        prompt = _reversion_prompt(ctx, state)
        assert prompt is not None
        assert "PLAN" in prompt

    def test_fr05_scope_creep_reversion(self) -> None:
        """Many checkpoint nudges + many files triggers scope creep reversion."""
        ctx = NudgeContext(tool_name="checkpoint")
        state = CeremonyState(
            nudge_counts={"checkpoint": 5},
            files_modified_since_checkpoint=11,
        )
        prompt = _reversion_prompt(ctx, state)
        assert prompt is not None
        assert "Scope" in prompt or "plan" in prompt.lower()

    def test_fr05_no_reversion_normal(self) -> None:
        """No reversion prompt under normal conditions."""
        ctx = NudgeContext(tool_name="checkpoint", build_passed=None)
        state = CeremonyState()
        prompt = _reversion_prompt(ctx, state)
        assert prompt is None

    def test_fr05_subagent_suppression(self) -> None:
        """Reversion prompt is suppressed for subagents."""
        ctx = NudgeContext(
            tool_name="build_check",
            build_passed=False,
            is_subagent=True,
        )
        state = CeremonyState()
        prompt = _reversion_prompt(ctx, state)
        assert prompt is None

    def test_fr05_no_context_returns_none(self) -> None:
        """No context (None) returns no reversion prompt."""
        state = CeremonyState()
        prompt = _reversion_prompt(None, state)
        assert prompt is None

    def test_fr05_scope_below_nudge_threshold(self) -> None:
        """nudge_counts[checkpoint]=3 AND files_modified=15 -> NO scope-creep reversion."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="implement",
            nudge_counts={"checkpoint": 3},
            files_modified_since_checkpoint=15,
        )
        ctx = NudgeContext(tool_name="checkpoint")
        result = _reversion_prompt(ctx, state)
        assert result is None
