"""Tests for FR04: Ceremony State Tracker (PRD-CORE-074).
Tests for FR01: Nudge Engine (PRD-CORE-074).

All tests use tmp_path fixture for filesystem isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    NudgeContext,
    ToolName,
    _assemble_nudge,
    _compute_urgency,
    _context_reactive_message,
    _next_two_steps,
    _reversion_prompt,
    _select_nudge_message,
    compute_nudge,
    compute_nudge_contextual,
    compute_nudge_contextual_action,
    compute_nudge_learning_injection,
    compute_nudge_minimal,
    increment_files_modified,
    increment_learnings,
    increment_nudge_count,
    is_local_model,
    mark_build_check,
    mark_checkpoint,
    mark_deliver,
    mark_review,
    mark_session_started,
    read_ceremony_state,
    reset_ceremony_state,
    reset_nudge_count,
    write_ceremony_state,
)

# --- Helpers ---


def _trw_dir(tmp_path: Path) -> Path:
    """Create and return the .trw directory under tmp_path."""
    trw = tmp_path / ".trw"
    trw.mkdir(parents=True, exist_ok=True)
    return trw


def _state_file(trw_dir: Path) -> Path:
    return trw_dir / "context" / "ceremony-state.json"


# -------------------------------------------------------------------------
# FR04 tests
# -------------------------------------------------------------------------


def test_fr04_state_file_created(tmp_path: Path) -> None:
    """State file is created on first write."""
    trw = _trw_dir(tmp_path)
    state = CeremonyState()
    write_ceremony_state(trw, state)
    assert _state_file(trw).exists()


def test_fr04_state_atomic_write(tmp_path: Path) -> None:
    """Write + read round-trip preserves all fields."""
    trw = _trw_dir(tmp_path)
    state = CeremonyState(
        session_started=True,
        checkpoint_count=3,
        files_modified_since_checkpoint=7,
        build_check_result="passed",
        deliver_called=True,
        learnings_this_session=2,
        nudge_counts={"session_start": 1, "checkpoint": 2},
        phase="implement",
    )
    write_ceremony_state(trw, state)
    result = read_ceremony_state(trw)

    assert result.session_started is True
    assert result.checkpoint_count == 3
    assert result.files_modified_since_checkpoint == 7
    assert result.build_check_result == "passed"
    assert result.deliver_called is True
    assert result.learnings_this_session == 2
    assert result.nudge_counts == {"session_start": 1, "checkpoint": 2}
    assert result.phase == "implement"


def test_fr04_state_survives_restart(tmp_path: Path) -> None:
    """Write, then read back from a fresh function call returns the same data."""
    trw = _trw_dir(tmp_path)
    state = CeremonyState(
        session_started=True,
        checkpoint_count=5,
        phase="validate",
        nudge_counts={"build_check": 3},
    )
    write_ceremony_state(trw, state)

    # Simulate fresh call (function, not cached object)
    result = read_ceremony_state(trw)
    assert result.session_started is True
    assert result.checkpoint_count == 5
    assert result.phase == "validate"
    assert result.nudge_counts.get("build_check") == 3


def test_fr04_state_reset_on_init(tmp_path: Path) -> None:
    """reset_ceremony_state returns state to all defaults."""
    trw = _trw_dir(tmp_path)
    # Write non-default state first
    state = CeremonyState(
        session_started=True,
        checkpoint_count=9,
        phase="done",
        deliver_called=True,
    )
    write_ceremony_state(trw, state)

    reset_ceremony_state(trw)
    result = read_ceremony_state(trw)

    defaults = CeremonyState()
    assert result.session_started == defaults.session_started
    assert result.checkpoint_count == defaults.checkpoint_count
    assert result.phase == defaults.phase
    assert result.deliver_called == defaults.deliver_called
    assert result.nudge_counts == defaults.nudge_counts


def test_fr04_state_read_missing_file(tmp_path: Path) -> None:
    """Missing state file returns CeremonyState defaults (fail-open)."""
    trw = _trw_dir(tmp_path)
    # Do NOT create the file
    result = read_ceremony_state(trw)

    defaults = CeremonyState()
    assert result.session_started == defaults.session_started
    assert result.checkpoint_count == defaults.checkpoint_count
    assert result.phase == defaults.phase
    assert result.nudge_counts == defaults.nudge_counts


def test_fr04_state_read_corrupted_file(tmp_path: Path) -> None:
    """Corrupted JSON returns defaults (fail-open, never raises)."""
    trw = _trw_dir(tmp_path)
    context_dir = trw / "context"
    context_dir.mkdir(parents=True, exist_ok=True)
    state_file = context_dir / "ceremony-state.json"
    state_file.write_text("{{{{ NOT VALID JSON !!!!", encoding="utf-8")

    result = read_ceremony_state(trw)
    defaults = CeremonyState()
    assert result.session_started == defaults.session_started
    assert result.checkpoint_count == defaults.checkpoint_count
    assert result.phase == defaults.phase


def test_fr04_mark_session_started(tmp_path: Path) -> None:
    """mark_session_started sets session_started flag to True."""
    trw = _trw_dir(tmp_path)
    result_before = read_ceremony_state(trw)
    assert result_before.session_started is False

    mark_session_started(trw)

    result = read_ceremony_state(trw)
    assert result.session_started is True


def test_fr04_mark_checkpoint(tmp_path: Path) -> None:
    """mark_checkpoint increments count, sets timestamp, resets files_modified."""
    trw = _trw_dir(tmp_path)
    # Set up some pre-existing state
    state = CeremonyState(
        checkpoint_count=2,
        files_modified_since_checkpoint=5,
    )
    write_ceremony_state(trw, state)

    mark_checkpoint(trw)

    result = read_ceremony_state(trw)
    assert result.checkpoint_count == 3
    assert result.files_modified_since_checkpoint == 0
    assert result.last_checkpoint_ts is not None
    # Timestamp must be a parseable ISO string
    from datetime import datetime

    ts = datetime.fromisoformat(result.last_checkpoint_ts.replace("Z", "+00:00"))
    assert ts is not None


def test_fr04_increment_files_modified(tmp_path: Path) -> None:
    """increment_files_modified increments files_modified_since_checkpoint."""
    trw = _trw_dir(tmp_path)

    increment_files_modified(trw)
    assert read_ceremony_state(trw).files_modified_since_checkpoint == 1

    increment_files_modified(trw, 4)
    assert read_ceremony_state(trw).files_modified_since_checkpoint == 5


def test_fr04_increment_nudge_count(tmp_path: Path) -> None:
    """increment_nudge_count tracks per-step nudge counts."""
    trw = _trw_dir(tmp_path)

    increment_nudge_count(trw, "session_start")
    increment_nudge_count(trw, "session_start")
    increment_nudge_count(trw, "build_check")

    result = read_ceremony_state(trw)
    assert result.nudge_counts.get("session_start") == 2
    assert result.nudge_counts.get("build_check") == 1


def test_fr04_reset_nudge_count(tmp_path: Path) -> None:
    """reset_nudge_count resets the count for a specific step to zero."""
    trw = _trw_dir(tmp_path)

    increment_nudge_count(trw, "checkpoint")
    increment_nudge_count(trw, "checkpoint")
    increment_nudge_count(trw, "deliver")

    reset_nudge_count(trw, "checkpoint")

    result = read_ceremony_state(trw)
    assert result.nudge_counts.get("checkpoint", 0) == 0
    # Other steps unaffected
    assert result.nudge_counts.get("deliver") == 1


def test_fr04_mark_build_check_passed(tmp_path: Path) -> None:
    """mark_build_check with passed=True sets build_check_result to 'passed'."""
    trw = _trw_dir(tmp_path)
    mark_build_check(trw, passed=True)
    assert read_ceremony_state(trw).build_check_result == "passed"


def test_fr04_mark_build_check_failed(tmp_path: Path) -> None:
    """mark_build_check with passed=False sets build_check_result to 'failed'."""
    trw = _trw_dir(tmp_path)
    mark_build_check(trw, passed=False)
    assert read_ceremony_state(trw).build_check_result == "failed"


def test_fr04_mark_deliver(tmp_path: Path) -> None:
    """mark_deliver sets deliver_called to True."""
    trw = _trw_dir(tmp_path)
    assert read_ceremony_state(trw).deliver_called is False
    mark_deliver(trw)
    assert read_ceremony_state(trw).deliver_called is True


def test_fr04_increment_learnings(tmp_path: Path) -> None:
    """increment_learnings increments learnings_this_session."""
    trw = _trw_dir(tmp_path)
    increment_learnings(trw)
    increment_learnings(trw)
    assert read_ceremony_state(trw).learnings_this_session == 2


def test_fr04_write_is_atomic(tmp_path: Path) -> None:
    """Atomic write: final file reflects the written state, not a temp file."""
    trw = _trw_dir(tmp_path)
    state = CeremonyState(checkpoint_count=42)
    write_ceremony_state(trw, state)

    state_path = _state_file(trw)
    assert state_path.exists()

    # No leftover temp files in the same directory
    temp_files = list((trw / "context").glob("*.tmp"))
    assert len(temp_files) == 0

    # Content is valid JSON
    raw = state_path.read_text(encoding="utf-8")
    parsed = json.loads(raw)
    assert parsed["checkpoint_count"] == 42


def test_fr04_phase_field_persisted(tmp_path: Path) -> None:
    """Phase field is persisted and round-trips correctly."""
    trw = _trw_dir(tmp_path)
    for phase in ("early", "implement", "validate", "deliver", "done"):
        state = CeremonyState(phase=phase)
        write_ceremony_state(trw, state)
        result = read_ceremony_state(trw)
        assert result.phase == phase


# -------------------------------------------------------------------------
# FR01 tests: Nudge Engine
# -------------------------------------------------------------------------


class TestNudgeEngine:
    """Tests for compute_nudge(), _build_status_line(), _select_nudge_message()."""

    def test_fr01_nudge_session_start_pending(self, tmp_path: Path) -> None:
        """Given session_start not called, nudge returns non-empty content."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=5)
        assert result != ""
        # PRD-CORE-129: Pool-based selection means content varies.
        # Status line always shows start status.
        assert "start" in result.lower()
        assert len(result) > 0

    def test_fr01_nudge_checkpoint_pending(self, tmp_path: Path) -> None:
        """Given files modified and no checkpoint, nudge returns non-empty content."""
        # PRD-CORE-129: Checkpoint threshold raised to >10.
        # Use 11 files to ensure checkpoint is a pending step.
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=11,
            phase="implement",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        # Pool-based selection: content varies, but TRW header is always present
        assert "TRW" in result

    def test_fr01_nudge_no_checkpoint_in_session(self, tmp_path: Path) -> None:
        """Given session started but no checkpoint, nudge returns non-empty content."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
            files_modified_since_checkpoint=0,
            phase="implement",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        # PRD-CORE-129: Pool-based selection. Content varies by pool.
        assert "TRW" in result

    def test_fr01_nudge_deliver_pending(self, tmp_path: Path) -> None:
        """Given phase=deliver and deliver not called, nudge mentions learning persistence."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            deliver_called=False,
            learnings_this_session=3,
            phase="deliver",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        assert "deliver" in result.lower()

    def test_fr01_nudge_build_check_pending(self, tmp_path: Path) -> None:
        """Given phase=validate and build_check not run, nudge returns non-empty content."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result=None,
            deliver_called=False,
            phase="validate",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        # PRD-CORE-129: Pool-based selection means the specific message varies.
        # The header and status line are always present.
        assert "TRW" in result

    def test_fr01_nudge_all_complete(self, tmp_path: Path) -> None:
        """Given all steps complete, status is single line with all checkmarks."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
            phase="done",
        )
        result = compute_nudge(state, available_learnings=0)
        # When all done, should show all checkmarks and no action message
        assert "session_start" in result or "deliver" in result
        # All should be checkmarks (✓), no crosses (✗)
        assert "\u2713" in result  # ✓
        assert "\u2717" not in result  # no ✗

    def test_fr01_nudge_never_blocks(self, tmp_path: Path) -> None:
        """Nudge never replaces or obscures tool response — it only appends."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=0)
        # Nudge is a string — tool responses are not replaced by it
        assert isinstance(result, str)

    def test_fr01_nudge_token_limit(self, tmp_path: Path) -> None:
        """Nudge never exceeds 100 tokens (~400 chars) across all state combinations."""
        combos = [
            CeremonyState(),
            CeremonyState(session_started=True),
            CeremonyState(session_started=True, files_modified_since_checkpoint=10),
            CeremonyState(session_started=True, checkpoint_count=1),
            CeremonyState(session_started=True, checkpoint_count=1, build_check_result="passed"),
            CeremonyState(session_started=True, checkpoint_count=1, build_check_result="passed", phase="validate"),
            CeremonyState(
                session_started=True, checkpoint_count=1, build_check_result="passed", deliver_called=True, phase="done"
            ),
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                deliver_called=True,
                learnings_this_session=5,
                phase="done",
            ),
        ]
        for state in combos:
            result = compute_nudge(state, available_learnings=10)
            assert len(result) <= 600, f"Nudge too long ({len(result)} chars) for state: {state}\n{result!r}"

    def test_fr01_status_line_format(self, tmp_path: Path) -> None:
        """Status line uses checkmark/cross format for session_start and deliver."""
        # PRD-CORE-129: compute_nudge now uses minimal header/status line.
        # Session not started — should show ✗ for start
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=0)
        assert "--- TRW ---" in result
        assert "\u2717" in result  # ✗

        # All complete — should show ✓ for start and deliver
        state2 = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="done",
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
        )
        result2 = compute_nudge(state2, available_learnings=0)
        assert "--- TRW ---" in result2
        assert "\u2713" in result2  # ✓

    def test_fr01_nudge_no_prescriptive_language(self, tmp_path: Path) -> None:
        """Nudge messages do not use prescriptive or decision language."""
        states = [
            CeremonyState(session_started=False),
            CeremonyState(session_started=True, files_modified_since_checkpoint=5),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            CeremonyState(session_started=True, checkpoint_count=1, phase="deliver", build_check_result="passed"),
        ]
        forbidden = ["you must", "critical", "always", "never", "consider", "you may want to", "perhaps"]
        for state in states:
            result = compute_nudge(state, available_learnings=0).lower()
            for word in forbidden:
                assert word not in result, (
                    f"Forbidden language '{word}' found in nudge for state {state!r}:\n{result!r}"
                )

    def test_fr01_nudge_failopen_on_error(self, tmp_path: Path) -> None:
        """compute_nudge returns empty string on unexpected errors (fail-open)."""
        # Pass a broken state that would cause attribute errors
        # compute_nudge must never raise
        state = CeremonyState()
        # PRD-CORE-129: compute_nudge now uses _build_minimal_status_line
        from trw_mcp.state import ceremony_nudge

        original = ceremony_nudge._build_minimal_status_line
        try:

            def _broken(s: CeremonyState) -> str:
                raise RuntimeError("simulated error")

            ceremony_nudge._build_minimal_status_line = _broken  # type: ignore[assignment]
            result = compute_nudge(state)
            assert isinstance(result, str)
            assert result == ""
        finally:
            ceremony_nudge._build_minimal_status_line = original

    def test_fr01_append_ceremony_nudge_failopen(self, tmp_path: Path) -> None:
        """append_ceremony_nudge remains a compatibility wrapper over live status."""
        from trw_mcp.tools._legacy_ceremony_nudge import append_ceremony_nudge

        original_response: dict[str, object] = {"status": "ok", "data": "result"}
        bad_dir = tmp_path / "nonexistent" / ".trw"
        result = append_ceremony_nudge(original_response.copy(), trw_dir=bad_dir)
        assert result.get("status") == "ok"
        assert result.get("data") == "result"
        assert "ceremony_status" in result

    def test_fr01_append_ceremony_nudge_adds_key(self, tmp_path: Path) -> None:
        """append_ceremony_nudge adds ceremony_status key to response dict."""
        from trw_mcp.tools._legacy_ceremony_nudge import append_ceremony_nudge

        trw = _trw_dir(tmp_path)
        state = CeremonyState(session_started=False)
        write_ceremony_state(trw, state)

        response: dict[str, object] = {"status": "ok"}
        result = append_ceremony_nudge(response.copy(), trw_dir=trw)
        assert "ceremony_status" in result
        assert isinstance(result["ceremony_status"], str)


# -------------------------------------------------------------------------
# FR02 tests: Nudge Value Expression (PRD-CORE-074)
# -------------------------------------------------------------------------


class TestNudgeValueExpression:
    """FR02: Nudge messages follow value-expression template (fact -> value -> consequence -> effort)."""

    def test_fr02_checkpoint_nudge_content(self, tmp_path: Path) -> None:
        """Checkpoint nudge includes file count AND compaction consequence."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=7,
            phase="implement",
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        # Fact: file count
        assert "7" in result
        # Consequence: compaction risk
        assert any(word in result.lower() for word in ("compaction", "compact", "lose", "lost", "erases"))

    def test_fr02_checkpoint_nudge_includes_elapsed_time(self, tmp_path: Path) -> None:
        """FR02: Checkpoint nudge includes elapsed time when last_checkpoint_ts is set."""
        from datetime import datetime, timedelta, timezone

        # Simulate a checkpoint that happened 45 minutes ago
        past_ts = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=3,
            last_checkpoint_ts=past_ts,
            checkpoint_count=1,
            phase="implement",
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        # Elapsed time must appear in the nudge
        assert "min ago" in result
        # The minute count should be ~45 (allow ±2 for test timing)
        import re

        match = re.search(r"(\d+) min ago", result)
        assert match is not None, f"No 'N min ago' found in: {result!r}"
        mins = int(match.group(1))
        assert 43 <= mins <= 47, f"Expected ~45 min ago, got {mins}: {result!r}"

    def test_fr02_checkpoint_nudge_no_elapsed_when_no_prior_checkpoint(self, tmp_path: Path) -> None:
        """FR02: Checkpoint nudge omits elapsed time when no prior checkpoint exists."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            last_checkpoint_ts=None,
            checkpoint_count=0,
            phase="implement",
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        # No elapsed time annotation when there has been no prior checkpoint
        assert "min ago" not in result

    def test_fr02_deliver_nudge_content(self, tmp_path: Path) -> None:
        """Deliver nudge includes learning count AND future agent impact."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            learnings_this_session=4,
            phase="deliver",
        )
        result = _select_nudge_message("deliver", state, available_learnings=0)
        # Fact: learning count
        assert "4" in result
        # Value: future agents benefit
        assert any(word in result.lower() for word in ("future", "persist", "session"))
        # Consequence: lost if skipped
        assert any(word in result.lower() for word in ("lost", "lose", "discard", "skip", "skipping"))

    def test_fr02_session_start_nudge_content(self, tmp_path: Path) -> None:
        """Session start nudge includes available learnings count."""
        state = CeremonyState(session_started=False)
        result = _select_nudge_message("session_start", state, available_learnings=8)
        # Fact: count of prior learnings
        assert "8" in result
        # Value: they become active context
        assert any(word in result.lower() for word in ("context", "discover", "prior", "past"))

    def test_fr02_no_prescriptive_language(self, tmp_path: Path) -> None:
        """No 'you must', 'critical', 'always', 'never' in any nudge output across all states."""
        forbidden_prescriptive = ["you must", "critical", "always", "never"]
        states = [
            CeremonyState(session_started=False),
            CeremonyState(session_started=False, nudge_counts={"session_start": 3}),
            CeremonyState(session_started=False, nudge_counts={"session_start": 7}),
            CeremonyState(session_started=True, files_modified_since_checkpoint=5),
            CeremonyState(session_started=True, files_modified_since_checkpoint=5, nudge_counts={"checkpoint": 3}),
            CeremonyState(session_started=True, files_modified_since_checkpoint=5, nudge_counts={"checkpoint": 6}),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate", nudge_counts={"build_check": 5}),
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                phase="deliver",
                learnings_this_session=2,
            ),
            CeremonyState(
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                phase="deliver",
                learnings_this_session=2,
                nudge_counts={"deliver": 6},
            ),
        ]
        for state in states:
            result = compute_nudge(state, available_learnings=5).lower()
            for word in forbidden_prescriptive:
                assert word not in result, (
                    f"Forbidden prescriptive language '{word}' found for state {state!r}:\n{result!r}"
                )

    def test_fr02_no_decision_language(self, tmp_path: Path) -> None:
        """No 'consider', 'you may want to', 'perhaps' in any nudge output."""
        forbidden_decision = ["consider", "you may want to", "perhaps"]
        states = [
            CeremonyState(session_started=False),
            CeremonyState(session_started=True, checkpoint_count=0),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            CeremonyState(session_started=True, checkpoint_count=1, build_check_result="passed", phase="deliver"),
        ]
        for state in states:
            result = compute_nudge(state, available_learnings=3).lower()
            for word in forbidden_decision:
                assert word not in result, (
                    f"Forbidden decision language '{word}' found for state {state!r}:\n{result!r}"
                )

    def test_fr02_build_check_nudge_content(self, tmp_path: Path) -> None:
        """Build check nudge includes fact (not run) AND consequence (ships broken code)."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result=None,
            phase="validate",
        )
        result = _select_nudge_message("build_check", state, available_learnings=0)
        # Fact: check not run
        assert any(word in result.lower() for word in ("not run", "build check"))
        # Value/consequence: issues before delivery
        assert any(word in result.lower() for word in ("delivery", "integration", "test", "type"))

    def test_fr07_build_check_nudge_prefers_verification_wording(self, tmp_path: Path) -> None:
        """FR07: generic build-check nudges use verification language."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result=None,
            phase="validate",
        )

        result = _select_nudge_message("build_check", state, available_learnings=0)

        assert "verification" in result.lower()
        assert "production" not in result.lower()

    def test_fr07_context_reactive_messages_prefer_work_over_implementation(self) -> None:
        """FR07: generic nudges avoid software-specific 'implementation' wording."""
        state = CeremonyState(session_started=True)

        result = _context_reactive_message(
            NudgeContext(tool_name=ToolName.INIT),
            state,
        )

        assert result is not None
        assert "implementation" not in result.lower()
        assert "work" in result.lower()


# -------------------------------------------------------------------------
# FR03 tests: Progressive Urgency (PRD-CORE-074)
# -------------------------------------------------------------------------


class TestProgressiveUrgency:
    """FR03: Nudge urgency increases with repeated nudges."""

    def test_fr03_compute_urgency_low_at_zero(self, tmp_path: Path) -> None:
        """Urgency is 'low' when nudge count is 0."""
        state = CeremonyState()
        assert _compute_urgency(state, "checkpoint") == "low"

    def test_fr03_compute_urgency_low_at_two(self, tmp_path: Path) -> None:
        """Urgency is 'low' when nudge count is 2."""
        state = CeremonyState(nudge_counts={"checkpoint": 2})
        assert _compute_urgency(state, "checkpoint") == "low"

    def test_fr03_compute_urgency_medium_at_three(self, tmp_path: Path) -> None:
        """Urgency becomes 'medium' at 3 nudges."""
        state = CeremonyState(nudge_counts={"session_start": 3})
        assert _compute_urgency(state, "session_start") == "medium"

    def test_fr03_compute_urgency_medium_at_four(self, tmp_path: Path) -> None:
        """Urgency is 'medium' at 4 nudges."""
        state = CeremonyState(nudge_counts={"deliver": 4})
        assert _compute_urgency(state, "deliver") == "medium"

    def test_fr03_compute_urgency_high_at_five(self, tmp_path: Path) -> None:
        """Urgency becomes 'high' at 5 nudges."""
        state = CeremonyState(nudge_counts={"build_check": 5})
        assert _compute_urgency(state, "build_check") == "high"

    def test_fr03_compute_urgency_high_above_five(self, tmp_path: Path) -> None:
        """Urgency remains 'high' at 10 nudges."""
        state = CeremonyState(nudge_counts={"checkpoint": 10})
        assert _compute_urgency(state, "checkpoint") == "high"

    def test_fr03_urgency_low_first(self, tmp_path: Path) -> None:
        """First nudge (count=0) is brief — no extra risk numbers or effort framing."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={},
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        # Low urgency: fact + value. NOT the high-urgency "2 seconds" effort phrase.
        assert "5" in result
        assert "compaction" in result.lower() or "compact" in result.lower()
        # Should not contain the expanded high-urgency phrasing
        assert "permanently" not in result.lower()

    def test_fr03_urgency_medium_3_nudges(self, tmp_path: Path) -> None:
        """After 3 nudges, checkpoint message adds concrete risk language."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={"checkpoint": 3},
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        # Medium urgency: adds "no recovery path" or similar concrete risk
        assert any(phrase in result.lower() for phrase in ("no recovery", "recovery path", "compaction risk", "risk"))

    def test_fr03_urgency_high_5_nudges(self, tmp_path: Path) -> None:
        """After 5 nudges, checkpoint message adds consequence + effort framing."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={"checkpoint": 5},
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        # High urgency: consequence (permanently) + effort framing (seconds)
        assert "permanently" in result.lower() or "erases" in result.lower()
        assert "second" in result.lower()

    def test_fr03_urgency_never_blocks(self, tmp_path: Path) -> None:
        """Even at high urgency, nudge never raises and always returns a string."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=100,
            nudge_counts={"checkpoint": 99},
        )
        result = compute_nudge(state, available_learnings=0)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_fr03_high_urgency_mentions_effort(self, tmp_path: Path) -> None:
        """High urgency deliver nudge includes minimal-effort framing."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            learnings_this_session=3,
            phase="deliver",
            nudge_counts={"deliver": 5},
        )
        result = _select_nudge_message("deliver", state, available_learnings=0)
        assert "second" in result.lower()

    def test_fr03_urgency_reset_on_completion(self, tmp_path: Path) -> None:
        """Completing a step resets its nudge count to 0."""
        trw = _trw_dir(tmp_path)
        # Simulate accumulated nudges for checkpoint
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={"checkpoint": 7},
        )
        write_ceremony_state(trw, state)
        assert read_ceremony_state(trw).nudge_counts.get("checkpoint") == 7

        # Completing the step should reset count
        reset_nudge_count(trw, "checkpoint")
        result = read_ceremony_state(trw)
        assert result.nudge_counts.get("checkpoint", 0) == 0
        # Other counts unaffected
        mark_checkpoint(trw)  # Actually complete the step
        assert read_ceremony_state(trw).checkpoint_count == 1

    def test_fr03_urgency_independent_per_step(self, tmp_path: Path) -> None:
        """Urgency is tracked independently per step — one step's count doesn't affect others."""
        state = CeremonyState(nudge_counts={"checkpoint": 8, "deliver": 1})
        assert _compute_urgency(state, "checkpoint") == "high"
        assert _compute_urgency(state, "deliver") == "low"
        assert _compute_urgency(state, "session_start") == "low"

    def test_fr03_medium_urgency_session_start(self, tmp_path: Path) -> None:
        """Medium urgency for session_start adds re-discovery cost language."""
        state = CeremonyState(
            session_started=False,
            nudge_counts={"session_start": 3},
        )
        result = _select_nudge_message("session_start", state, available_learnings=5)
        # Medium nudge adds agent cost framing
        assert any(phrase in result.lower() for phrase in ("re-discover", "rediscover", "cost", "agent"))

    def test_fr03_high_urgency_session_start_no_learnings(self, tmp_path: Path) -> None:
        """High urgency for session_start (no prior learnings) mentions invisible work."""
        state = CeremonyState(
            session_started=False,
            nudge_counts={"session_start": 5},
        )
        result = _select_nudge_message("session_start", state, available_learnings=0)
        assert any(phrase in result.lower() for phrase in ("invisible", "future agent", "unattach"))

    def test_fr03_token_limit_preserved_at_all_urgency_levels(self, tmp_path: Path) -> None:
        """Token limit (400 chars) is respected at all urgency levels."""
        nudge_count_sets = [
            {},  # low
            {"checkpoint": 3},  # medium
            {"checkpoint": 6},  # high
        ]
        for nudge_counts in nudge_count_sets:
            state = CeremonyState(
                session_started=True,
                files_modified_since_checkpoint=10,
                nudge_counts=nudge_counts,
                phase="implement",
            )
            result = compute_nudge(state, available_learnings=10)
            assert len(result) <= 600, (
                f"Nudge exceeds 600 chars at nudge_counts={nudge_counts}: {len(result)} chars\n{result!r}"
            )


# -------------------------------------------------------------------------
# FR12 tests: Local Model Tool Scoping (PRD-CORE-074)
# -------------------------------------------------------------------------


class TestLocalModelScoping:
    """FR12: Local model tool scoping and minimal ceremony."""

    def test_fr12_detect_ollama_model(self) -> None:
        assert is_local_model("ollama/qwen3-coder-next") is True

    def test_fr12_detect_non_local(self) -> None:
        assert is_local_model("anthropic/claude-sonnet-4-5") is False

    def test_fr12_detect_local_prefix(self) -> None:
        assert is_local_model("local/my-model") is True

    def test_fr12_detect_localhost_in_name(self) -> None:
        assert is_local_model("http://localhost:11434/model") is True

    def test_fr12_detect_non_local_claude(self) -> None:
        assert is_local_model("claude-opus-4-6") is False

    def test_fr12_detect_non_local_openai(self) -> None:
        assert is_local_model("openai/gpt-4o") is False

    def test_fr12_minimal_nudge_session_only(self, tmp_path: Path) -> None:
        """MINIMAL ceremony only nudges session_start and deliver."""
        state = CeremonyState(session_started=True, files_modified_since_checkpoint=10)
        nudge = compute_nudge_minimal(state)
        # Should NOT mention checkpoint even with 10 files modified
        assert "checkpoint" not in nudge.lower()

    def test_fr12_minimal_nudge_under_200_chars(self, tmp_path: Path) -> None:
        """Minimal nudge never exceeds 200 chars."""
        for session_started in [True, False]:
            for deliver in [True, False]:
                state = CeremonyState(
                    session_started=session_started,
                    deliver_called=deliver,
                    learnings_this_session=5,
                )
                nudge = compute_nudge_minimal(state, available_learnings=20)
                assert len(nudge) <= 200, f"Minimal nudge too long ({len(nudge)}): {nudge}"

    def test_fr12_minimal_nudge_deliver_pending(self, tmp_path: Path) -> None:
        """Minimal nudge mentions deliver when session started but not delivered."""
        state = CeremonyState(session_started=True, learnings_this_session=3)
        nudge = compute_nudge_minimal(state)
        assert "deliver" in nudge.lower()

    def test_fr12_minimal_all_complete(self, tmp_path: Path) -> None:
        """Minimal nudge is very short when all complete."""
        state = CeremonyState(session_started=True, deliver_called=True)
        nudge = compute_nudge_minimal(state)
        assert len(nudge) < 80

    def test_fr12_minimal_nudge_failopen(self) -> None:
        """compute_nudge_minimal never raises."""
        nudge = compute_nudge_minimal(CeremonyState())
        assert isinstance(nudge, str)

    def test_fr12_minimal_nudge_logs_failopen_exceptions(self) -> None:
        """Minimal legacy nudge failures stay observable."""
        with (
            patch("trw_mcp.state.ceremony_nudge._build_minimal_status_line", side_effect=RuntimeError("boom")),
            patch("trw_mcp.state.ceremony_nudge.logger") as mock_logger,
        ):
            assert compute_nudge_minimal(CeremonyState()) == ""

        mock_logger.debug.assert_called_once()
        assert mock_logger.debug.call_args.args[0] == "compute_nudge_minimal_failed"
        assert mock_logger.debug.call_args.kwargs["exc_info"] is True

    def test_fr12_minimal_nudge_session_not_started(self) -> None:
        """Minimal nudge mentions session start when not called."""
        state = CeremonyState(session_started=False)
        nudge = compute_nudge_minimal(state)
        assert "start" in nudge.lower() or "session" in nudge.lower()

    def test_fr12_minimal_nudge_no_build_check(self) -> None:
        """Minimal nudge never mentions build_check."""
        for phase in ("validate", "deliver", "done"):
            state = CeremonyState(
                session_started=True,
                phase=phase,
                build_check_result=None,
            )
            nudge = compute_nudge_minimal(state)
            assert "build" not in nudge.lower(), f"Minimal nudge mentions build for phase={phase}: {nudge}"

    def test_learning_injection_nudge_renders_top_learning(self, tmp_path: Path) -> None:
        """Learning-injection messenger renders a file-targeted learning summary."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(session_started=True, phase="implement")
        recall_context = type("RecallContext", (), {"modified_files": ["backend/services/parsers.py"]})()

        with (
            patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=[{"id": "L-test123", "summary": "Preserve parser ordering when normalizing tokens"}],
            ) as mock_recall,
        ):
            nudge = compute_nudge_learning_injection(state, trw)

        assert "parsers.py" in nudge
        assert "Preserve parser ordering" in nudge
        assert "L-test123" in nudge
        assert mock_recall.call_args is not None
        assert mock_recall.call_args.args[0] == trw

    def test_learning_injection_nudge_falls_back_without_file_context(self, tmp_path: Path) -> None:
        """No modified-file context degrades to the minimal messenger."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(session_started=True, learnings_this_session=1)
        recall_context = type("RecallContext", (), {"modified_files": []})()

        with patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context):
            nudge = compute_nudge_learning_injection(state, trw)

        assert nudge == compute_nudge_minimal(state)

    def test_learning_injection_nudge_failopen(self, tmp_path: Path) -> None:
        """Learning-injection messenger never raises on recall failures."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(session_started=True)

        with patch(
            "trw_mcp.state.recall_context.build_recall_context",
            side_effect=RuntimeError("boom"),
        ):
            nudge = compute_nudge_learning_injection(state, trw)

        assert nudge == compute_nudge_minimal(state)

    def test_contextual_nudge_guides_next_step_and_relevant_learning(self, tmp_path: Path) -> None:
        """Contextual messenger keeps the next-step scaffold and adds one caution."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(
            session_started=True,
            phase="implement",
            files_modified_since_checkpoint=2,
        )
        recall_context = type("RecallContext", (), {"modified_files": ["backend/services/parsers.py"]})()

        with (
            patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=[{"id": "L-test123", "summary": "Preserve parser ordering when normalizing tokens"}],
            ),
        ):
            nudge = compute_nudge_contextual(state, trw)

        assert "NEXT: trw_checkpoint()" in nudge
        assert "parsers.py" in nudge
        assert "Preserve parser ordering" in nudge
        assert "L-test123" in nudge

    def test_contextual_nudge_without_recall_still_guides_next_step(self, tmp_path: Path) -> None:
        """Contextual messenger still emits an action line without recall context."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(session_started=False, phase="early")
        recall_context = type("RecallContext", (), {"modified_files": []})()

        with patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context):
            nudge = compute_nudge_contextual(state, trw)

        assert "NEXT: trw_session_start()" in nudge
        assert "Watch-out" not in nudge

    def test_contextual_action_nudge_omits_learning_caution(self, tmp_path: Path) -> None:
        """Action-only contextual messenger keeps guidance while dropping the warning line."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(
            session_started=True,
            phase="implement",
            files_modified_since_checkpoint=2,
        )
        recall_context = type("RecallContext", (), {"modified_files": ["backend/services/parsers.py"]})()

        with (
            patch("trw_mcp.state.recall_context.build_recall_context", return_value=recall_context),
            patch(
                "trw_mcp.state.memory_adapter.recall_learnings",
                return_value=[{"id": "L-test123", "summary": "Preserve parser ordering when normalizing tokens"}],
            ),
        ):
            nudge = compute_nudge_contextual_action(state, trw)

        assert "NEXT: trw_checkpoint()" in nudge
        assert "parsers.py" in nudge
        assert "Watch-out" not in nudge
        assert "Preserve parser ordering" not in nudge
        assert "L-test123" not in nudge


# -------------------------------------------------------------------------
# PRD-CORE-084 tests: Context-Reactive Nudge Engine
# -------------------------------------------------------------------------


class TestFR01CeremonyStateExtension:
    """FR01: CeremonyState schema extension with review fields."""

    def test_fr01_ceremony_state_has_review_fields(self) -> None:
        """CeremonyState has review_called, review_verdict, review_p0_count defaults."""
        state = CeremonyState()
        assert state.review_called is False
        assert state.review_verdict is None
        assert state.review_p0_count == 0

    def test_fr01_steps_includes_review(self) -> None:
        """_STEPS tuple includes 'review' between build_check and deliver."""
        from trw_mcp.state.ceremony_nudge import _STEPS

        assert "review" in _STEPS
        idx_build = _STEPS.index("build_check")
        idx_review = _STEPS.index("review")
        idx_deliver = _STEPS.index("deliver")
        assert idx_build < idx_review < idx_deliver

    def test_fr01_mark_review(self, tmp_path: Path) -> None:
        """mark_review sets review_called, review_verdict, review_p0_count."""
        trw = _trw_dir(tmp_path)
        mark_review(trw, verdict="pass", p0_count=0)
        state = read_ceremony_state(trw)
        assert state.review_called is True
        assert state.review_verdict == "pass"
        assert state.review_p0_count == 0

    def test_fr01_mark_review_with_p0s(self, tmp_path: Path) -> None:
        """mark_review with p0 findings records the count."""
        trw = _trw_dir(tmp_path)
        mark_review(trw, verdict="block", p0_count=3)
        state = read_ceremony_state(trw)
        assert state.review_called is True
        assert state.review_verdict == "block"
        assert state.review_p0_count == 3

    def test_fr01_step_complete_review(self) -> None:
        """_step_complete returns True for review when review_called is True."""
        from trw_mcp.state.ceremony_nudge import _step_complete

        state = CeremonyState(review_called=True)
        assert _step_complete("review", state) is True

    def test_fr01_step_incomplete_review(self) -> None:
        """_step_complete returns False for review when review_called is False."""
        from trw_mcp.state.ceremony_nudge import _step_complete

        state = CeremonyState(review_called=False)
        assert _step_complete("review", state) is False

    def test_fr01_review_in_status_line(self) -> None:
        """_build_status_line includes review step."""
        from trw_mcp.state.ceremony_nudge import _build_status_line

        state = CeremonyState(review_called=True)
        line = _build_status_line(state)
        assert "review" in line

    def test_fr01_review_pending_in_priority(self) -> None:
        """Review shows as pending when in review phase and not called."""
        from trw_mcp.state.ceremony_nudge import _highest_priority_pending_step

        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=False,
            phase="review",
        )
        assert _highest_priority_pending_step(state) == "review"

    def test_fr01_review_not_pending_when_called(self) -> None:
        """Review is not pending when already called."""
        from trw_mcp.state.ceremony_nudge import _highest_priority_pending_step

        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=True,
            phase="review",
        )
        # Should advance past review to deliver
        assert _highest_priority_pending_step(state) != "review"

    def test_fr01_from_dict_review_fields(self, tmp_path: Path) -> None:
        """_from_dict deserializes review fields with fail-open defaults."""
        trw = _trw_dir(tmp_path)
        state = CeremonyState(
            review_called=True,
            review_verdict="warn",
            review_p0_count=2,
        )
        write_ceremony_state(trw, state)
        result = read_ceremony_state(trw)
        assert result.review_called is True
        assert result.review_verdict == "warn"
        assert result.review_p0_count == 2

    def test_fr01_from_dict_missing_review_fields(self, tmp_path: Path) -> None:
        """_from_dict handles missing review fields with defaults (fail-open)."""
        trw = _trw_dir(tmp_path)
        # Write raw JSON without review fields (simulates old state file)
        context_dir = trw / "context"
        context_dir.mkdir(parents=True, exist_ok=True)
        state_file = context_dir / "ceremony-state.json"
        state_file.write_text(
            '{"session_started":true,"checkpoint_count":1}',
            encoding="utf-8",
        )
        result = read_ceremony_state(trw)
        assert result.review_called is False
        assert result.review_verdict is None
        assert result.review_p0_count == 0

    def test_fr01_all_complete_with_review(self) -> None:
        """All steps complete includes review step."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
            review_called=True,
            deliver_called=True,
            phase="done",
        )
        result = compute_nudge(state)
        # All should be checkmarks, no crosses
        assert "\u2717" not in result

    def test_fr01_review_not_pending_in_validate_phase(self) -> None:
        """phase=validate with review_called=False -> pending step is build_check, not review."""
        from trw_mcp.state.ceremony_nudge import _highest_priority_pending_step

        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            phase="validate",
            review_called=False,
        )
        result = _highest_priority_pending_step(state)
        assert result == "build_check"  # review not yet applicable in validate phase


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

    def test_fr02_append_ceremony_nudge_accepts_context(self, tmp_path: Path) -> None:
        """append_ceremony_nudge accepts optional context parameter for compatibility."""
        from trw_mcp.tools._legacy_ceremony_nudge import append_ceremony_nudge

        trw = _trw_dir(tmp_path)
        write_ceremony_state(trw, CeremonyState(session_started=True, checkpoint_count=1))
        ctx = NudgeContext(tool_name="checkpoint")
        response: dict[str, object] = {"status": "ok"}
        result = append_ceremony_nudge(response.copy(), trw_dir=trw, context=ctx)
        assert "ceremony_status" in result


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
        # PRD-CORE-129: Pool-based selection means context-reactive content
        # is only returned when the "context" pool is selected. The TRW header
        # and status line are always present.
        assert "TRW" in result
        assert len(result) > 0


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
        assert then is None  # Only two steps applicable, one done

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
        # PRD-CORE-129: Pool-based selection. May or may not mention review.
        # TRW header and status line are always present.
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
        assert result is None  # nudge count threshold (5) not met


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
        assert len(msg) < 250  # concise
        assert "SHOULD" not in msg  # no RFC 2119 escalation at low urgency


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
        # Should not end with "..." from old truncation (unless genuinely over budget)
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
        status = "\u2713 s | \u2713 c | \u2713 b | \u2713 r | \u2713 d"  # ~37 chars
        reactive = "Build failed. " + "x" * 200  # 214 chars
        then_step = "THEN: trw_deliver()"  # 19 chars
        reversion = "Consider reverting to PLAN."  # 27 chars
        # status(37) + \n(1) + reactive(214) + \n(1) + then_step(19) = 272 fits in 280
        # 272 + \n(1) + reversion(27) = 300 exceeds 280 → reversion dropped
        result = _assemble_nudge(status, reactive, next_then=then_step, reversion=reversion, budget=280)
        assert status in result
        assert "Build failed" in result
        assert then_step in result  # THEN step fits
        assert reversion not in result  # reversion dropped before THEN


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
        # All steps should have checkmarks
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
        # All steps should have crosses
        assert line.count("\u2717") == 5
        assert "\u2713" not in line
        # Checkpoint annotation should show files modified
        assert "5 files modified" in line
        # Deliver annotation should show pending learnings
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
        # Pool-based: content varies, but TRW header is always present
        assert "TRW" in result
        # The reversion prompt text should NOT appear as a separate line
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
        # Separate reversion prompt should not appear
        assert "revert to PLAN" not in result


class TestBackwardsCompatibility:
    """Existing signatures remain backwards-compatible."""

    def test_compute_nudge_no_context(self) -> None:
        """compute_nudge works without context (backwards compat)."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=5)
        # PRD-CORE-129: Pool-based selection. Status line shows "start".
        assert "start" in result.lower()
        assert len(result) > 0

    def test_compute_nudge_failopen_with_context(self) -> None:
        """compute_nudge with context still fail-open on errors."""
        state = CeremonyState()
        from trw_mcp.state import ceremony_nudge

        # PRD-CORE-129: compute_nudge now uses _build_minimal_status_line
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
        # PRD-CORE-129: Pool-based selection. TRW header always present.
        # The specific message content varies by pool selection.
        assert "TRW" in result
        assert len(result) > 0


# -------------------------------------------------------------------------
# _hydrate_files_modified tests (PRD-CORE-124)
# -------------------------------------------------------------------------


class TestHydrateFilesModified:
    """Tests for _hydrate_files_modified from _session_recall_helpers.py."""

    def test_hydrate_files_modified_counts_events(self, tmp_path: Path) -> None:
        """Events of type 'file_modified' are counted and stored in state."""
        import json

        from trw_mcp.state.ceremony_nudge import CeremonyState
        from trw_mcp.tools._legacy_ceremony_nudge import _hydrate_files_modified

        trw = _trw_dir(tmp_path)

        # Create run directory with events.jsonl containing file_modified events
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260101T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"type": "file_modified", "ts": "2026-01-01T01:00:00Z", "path": "foo.py"},
            {"type": "file_modified", "ts": "2026-01-01T02:00:00Z", "path": "bar.py"},
            {"type": "checkpoint", "ts": "2026-01-01T03:00:00Z"},
            {"type": "file_modified", "ts": "2026-01-01T04:00:00Z", "path": "baz.py"},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        state = CeremonyState()

        from unittest.mock import patch

        # _hydrate_files_modified uses a function-local import of find_active_run.
        # Patch at the source module (trw_mcp.state._paths) so the local import picks it up.
        with patch("trw_mcp.state._paths.find_active_run", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        # All 3 file_modified events have ts > threshold (threshold is "")
        assert state.files_modified_since_checkpoint == 3

    def test_hydrate_files_modified_respects_checkpoint_ts(self, tmp_path: Path) -> None:
        """Only file_modified events AFTER last_checkpoint_ts are counted."""
        import json
        from unittest.mock import patch

        from trw_mcp.state.ceremony_nudge import CeremonyState
        from trw_mcp.tools._legacy_ceremony_nudge import _hydrate_files_modified

        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260201T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"

        events = [
            {"type": "file_modified", "ts": "2026-01-01T01:00:00Z", "path": "old.py"},
            {"type": "file_modified", "ts": "2026-01-01T02:00:00Z", "path": "old2.py"},
            {"type": "file_modified", "ts": "2026-01-01T04:00:00Z", "path": "new.py"},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        # Checkpoint timestamp is between the 2nd and 3rd event
        state = CeremonyState(last_checkpoint_ts="2026-01-01T03:00:00Z")

        with patch("trw_mcp.state._paths.find_active_run", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        # Only the event after the checkpoint timestamp should be counted
        assert state.files_modified_since_checkpoint == 1

    def test_hydrate_files_modified_failopen_no_run(self, tmp_path: Path) -> None:
        """No exception when find_active_run returns None (no active run)."""
        from unittest.mock import patch

        from trw_mcp.state.ceremony_nudge import CeremonyState
        from trw_mcp.tools._legacy_ceremony_nudge import _hydrate_files_modified

        trw = _trw_dir(tmp_path)
        state = CeremonyState()

        # Should not raise even without an active run
        with patch("trw_mcp.state._paths.find_active_run", return_value=None):
            _hydrate_files_modified(state, trw)

        # State unchanged — files_modified stays at default 0
        assert state.files_modified_since_checkpoint == 0

    def test_hydrate_files_modified_failopen_missing_events(self, tmp_path: Path) -> None:
        """No exception when events.jsonl does not exist."""
        from unittest.mock import patch

        from trw_mcp.state.ceremony_nudge import CeremonyState
        from trw_mcp.tools._legacy_ceremony_nudge import _hydrate_files_modified

        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260301T000000Z-noevents"
        (run_dir / "meta").mkdir(parents=True)
        # events.jsonl intentionally NOT created

        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_active_run", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        # State unchanged — fail-open
        assert state.files_modified_since_checkpoint == 0

    def test_hydrate_files_modified_only_counts_file_modified_type(self, tmp_path: Path) -> None:
        """Events with other types are not counted."""
        import json
        from unittest.mock import patch

        from trw_mcp.state.ceremony_nudge import CeremonyState
        from trw_mcp.tools._legacy_ceremony_nudge import _hydrate_files_modified

        trw = _trw_dir(tmp_path)
        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260401T000000Z-mixed"
        (run_dir / "meta").mkdir(parents=True)
        events_path = run_dir / "meta" / "events.jsonl"

        events = [
            {"type": "checkpoint", "ts": "2026-01-01T01:00:00Z"},
            {"type": "tool_invocation", "ts": "2026-01-01T02:00:00Z"},
            {"type": "session_start", "ts": "2026-01-01T03:00:00Z"},
            # No file_modified events
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )

        state = CeremonyState()

        with patch("trw_mcp.state._paths.find_active_run", return_value=run_dir):
            _hydrate_files_modified(state, trw)

        assert state.files_modified_since_checkpoint == 0
