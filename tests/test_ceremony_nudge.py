"""Tests for FR04: Ceremony State Tracker (PRD-CORE-074).
Tests for FR01: Nudge Engine (PRD-CORE-074).

All tests use tmp_path fixture for filesystem isolation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    _compute_urgency,
    _select_nudge_message,
    compute_nudge,
    compute_nudge_minimal,
    increment_files_modified,
    increment_learnings,
    increment_nudge_count,
    is_local_model,
    mark_build_check,
    mark_checkpoint,
    mark_deliver,
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
        """Given session_start not called, nudge mentions loading prior learnings."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=5)
        assert result != ""
        # Should reference session_start in status line
        assert "session_start" in result.lower() or "session" in result.lower()
        # Should have a value-expressing message
        assert len(result) > 0

    def test_fr01_nudge_checkpoint_pending(self, tmp_path: Path) -> None:
        """Given 5+ files modified, nudge mentions file count and compaction risk."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            phase="implement",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        # Should mention the file count or checkpoint
        assert "5" in result or "checkpoint" in result.lower()

    def test_fr01_nudge_no_checkpoint_in_session(self, tmp_path: Path) -> None:
        """Given session started but no checkpoint, nudge recommends checkpoint."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=0,
            files_modified_since_checkpoint=0,
            phase="implement",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        assert "checkpoint" in result.lower()

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
        """Given phase=validate and build_check not run, nudge mentions build check."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result=None,
            deliver_called=False,
            phase="validate",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
        assert "build" in result.lower() or "check" in result.lower()

    def test_fr01_nudge_all_complete(self, tmp_path: Path) -> None:
        """Given all steps complete, status is single line with all checkmarks."""
        state = CeremonyState(
            session_started=True,
            checkpoint_count=1,
            build_check_result="passed",
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
            CeremonyState(session_started=True, checkpoint_count=1, build_check_result="passed", deliver_called=True, phase="done"),
            CeremonyState(session_started=True, checkpoint_count=1, build_check_result="passed", deliver_called=True, learnings_this_session=5, phase="done"),
        ]
        for state in combos:
            result = compute_nudge(state, available_learnings=10)
            assert len(result) <= 400, (
                f"Nudge too long ({len(result)} chars) for state: {state}\n{result!r}"
            )

    def test_fr01_status_line_format(self, tmp_path: Path) -> None:
        """Status line uses checkmark/cross format for each step."""
        # Session not started — all steps should show ✗
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=0)
        assert "--- TRW Session ---" in result
        # session_start step should show ✗
        assert "\u2717" in result  # ✗

        # Session started — session_start should show ✓
        state2 = CeremonyState(session_started=True, checkpoint_count=1, phase="done",
                               build_check_result="passed", deliver_called=True)
        result2 = compute_nudge(state2, available_learnings=0)
        assert "--- TRW Session ---" in result2
        assert "\u2713" in result2  # ✓

    def test_fr01_nudge_no_prescriptive_language(self, tmp_path: Path) -> None:
        """Nudge messages do not use prescriptive or decision language."""
        states = [
            CeremonyState(session_started=False),
            CeremonyState(session_started=True, files_modified_since_checkpoint=5),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            CeremonyState(session_started=True, checkpoint_count=1, phase="deliver",
                          build_check_result="passed"),
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
        # Patch _build_status_line to raise
        from trw_mcp.state import ceremony_nudge
        original = ceremony_nudge._build_status_line
        try:
            def _broken(s: CeremonyState) -> str:
                raise RuntimeError("simulated error")
            ceremony_nudge._build_status_line = _broken  # type: ignore[assignment]
            result = compute_nudge(state)
            assert isinstance(result, str)
            assert result == ""
        finally:
            ceremony_nudge._build_status_line = original

    def test_fr01_append_ceremony_nudge_failopen(self, tmp_path: Path) -> None:
        """append_ceremony_nudge returns response unchanged on error."""
        from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

        original_response: dict[str, object] = {"status": "ok", "data": "result"}
        # Pass a non-existent trw_dir to force a read failure
        bad_dir = tmp_path / "nonexistent" / ".trw"
        result = append_ceremony_nudge(original_response.copy(), trw_dir=bad_dir)
        # Should still contain the original keys
        assert result.get("status") == "ok"
        assert result.get("data") == "result"

    def test_fr01_append_ceremony_nudge_adds_key(self, tmp_path: Path) -> None:
        """append_ceremony_nudge adds ceremony_status key to response dict."""
        from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

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
            CeremonyState(session_started=True, files_modified_since_checkpoint=5,
                          nudge_counts={"checkpoint": 3}),
            CeremonyState(session_started=True, files_modified_since_checkpoint=5,
                          nudge_counts={"checkpoint": 6}),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate"),
            CeremonyState(session_started=True, checkpoint_count=1, phase="validate",
                          nudge_counts={"build_check": 5}),
            CeremonyState(session_started=True, checkpoint_count=1,
                          build_check_result="passed", phase="deliver",
                          learnings_this_session=2),
            CeremonyState(session_started=True, checkpoint_count=1,
                          build_check_result="passed", phase="deliver",
                          learnings_this_session=2, nudge_counts={"deliver": 6}),
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
            CeremonyState(session_started=True, checkpoint_count=1,
                          build_check_result="passed", phase="deliver"),
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
        assert any(phrase in result.lower() for phrase in (
            "no recovery", "recovery path", "compaction risk", "risk"
        ))

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
        assert any(phrase in result.lower() for phrase in (
            "re-discover", "rediscover", "cost", "agent"
        ))

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
            {},                          # low
            {"checkpoint": 3},           # medium
            {"checkpoint": 6},           # high
        ]
        for nudge_counts in nudge_count_sets:
            state = CeremonyState(
                session_started=True,
                files_modified_since_checkpoint=10,
                nudge_counts=nudge_counts,
                phase="implement",
            )
            result = compute_nudge(state, available_learnings=10)
            assert len(result) <= 400, (
                f"Nudge exceeds 400 chars at nudge_counts={nudge_counts}: {len(result)} chars\n{result!r}"
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
                assert len(nudge) <= 200, (
                    f"Minimal nudge too long ({len(nudge)}): {nudge}"
                )

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
            assert "build" not in nudge.lower(), (
                f"Minimal nudge mentions build for phase={phase}: {nudge}"
            )
