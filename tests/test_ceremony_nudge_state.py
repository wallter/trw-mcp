"""Tests for ceremony nudge state tracking and schema extensions."""

from __future__ import annotations

import json
from pathlib import Path

from tests._ceremony_nudge_support import _state_file, _trw_dir
from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    compute_nudge,
    increment_files_modified,
    increment_learnings,
    increment_nudge_count,
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

    result = read_ceremony_state(trw)
    assert result.session_started is True
    assert result.checkpoint_count == 5
    assert result.phase == "validate"
    assert result.nudge_counts.get("build_check") == 3


def test_fr04_state_reset_on_init(tmp_path: Path) -> None:
    """reset_ceremony_state returns state to all defaults."""
    trw = _trw_dir(tmp_path)
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
    from datetime import datetime

    trw = _trw_dir(tmp_path)
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

    temp_files = list((trw / "context").glob("*.tmp"))
    assert len(temp_files) == 0

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
        assert result == "build_check"
