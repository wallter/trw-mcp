"""Tests for the feedback nudge engine (PRD-INFRA-132 FR07)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.models.config._main import TRWConfig
from trw_mcp.state._feedback_nudge import (
    FEEDBACK_NUDGE_TEXT,
    _state_path,
    maybe_emit_feedback_nudge,
    record_build_check_outcome,
    record_bug_learning,
    record_unhandled_exception,
)


def _config(proactive: bool) -> TRWConfig:
    """Return a TRWConfig with ``feedback.proactive`` toggled."""
    cfg = TRWConfig()
    # Pydantic v2 BaseSettings: mutate the sub-model in place.
    cfg.feedback.proactive = proactive
    return cfg


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Provide a ``.trw`` directory with the runtime/ folder ready."""
    d = tmp_path / ".trw"
    (d / "runtime").mkdir(parents=True)
    return d


def test_opt_in_gate_suppresses_when_proactive_false(trw_dir: Path) -> None:
    cfg = _config(proactive=False)
    for _ in range(5):
        record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) is None


def test_threshold_trigger_3_build_check_fails(trw_dir: Path) -> None:
    cfg = _config(proactive=True)
    for _ in range(3):
        record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    result = maybe_emit_feedback_nudge("sess-A", trw_dir, cfg)
    assert result == FEEDBACK_NUDGE_TEXT


def test_build_check_pass_resets_counter(trw_dir: Path) -> None:
    cfg = _config(proactive=True)
    record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    record_build_check_outcome("sess-A", passed=True, trw_dir=trw_dir)
    # 2 fails, reset, then 2 more fails -> still below threshold of 3.
    record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) is None


def test_per_session_throttle(trw_dir: Path) -> None:
    cfg = _config(proactive=True)
    for _ in range(3):
        record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    first = maybe_emit_feedback_nudge("sess-A", trw_dir, cfg)
    second = maybe_emit_feedback_nudge("sess-A", trw_dir, cfg)
    assert first == FEEDBACK_NUDGE_TEXT
    assert second is None


def test_cross_session_isolation(trw_dir: Path) -> None:
    cfg = _config(proactive=True)
    for _ in range(3):
        record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) == FEEDBACK_NUDGE_TEXT
    # Session B hasn't tripped anything.
    assert maybe_emit_feedback_nudge("sess-B", trw_dir, cfg) is None
    for _ in range(3):
        record_build_check_outcome("sess-B", passed=False, trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-B", trw_dir, cfg) == FEEDBACK_NUDGE_TEXT


def test_unhandled_exception_threshold(trw_dir: Path) -> None:
    cfg = _config(proactive=True)
    record_unhandled_exception("sess-A", trw_dir=trw_dir)
    # 1 exception (below threshold of 2) should not fire.
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) is None
    record_unhandled_exception("sess-A", trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) == FEEDBACK_NUDGE_TEXT


def test_bug_learning_threshold(trw_dir: Path) -> None:
    cfg = _config(proactive=True)
    # tags=["bug"] alone must NOT trigger.
    record_bug_learning("sess-A", tags=["bug"], trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) is None
    # tags=["trw-internal"] alone must NOT trigger.
    record_bug_learning("sess-A", tags=["trw-internal"], trw_dir=trw_dir)
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) is None
    # Both tags -> threshold (1) met.
    record_bug_learning(
        "sess-A", tags=["bug", "trw-internal"], trw_dir=trw_dir
    )
    assert maybe_emit_feedback_nudge("sess-A", trw_dir, cfg) == FEEDBACK_NUDGE_TEXT


def test_atomic_write_resilience(trw_dir: Path) -> None:
    """A stray ``.tmp`` file in the runtime dir must not block legitimate writes."""
    # Pre-create a garbage .tmp file resembling a partial write.
    garbage = trw_dir / "runtime" / ".feedback_nudge_state.garbage.tmp"
    garbage.write_text("not-json garbage", encoding="utf-8")
    # A legitimate write should still succeed and leave a parseable state.
    record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    path = _state_path(trw_dir)
    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["sess-A"]["build_check_fail_count"] == 1


def test_corrupt_state_file_recovers_to_empty(trw_dir: Path) -> None:
    """A corrupt on-disk state file is treated as empty, not propagated."""
    path = _state_path(trw_dir)
    path.write_text("{not valid json", encoding="utf-8")
    # Subsequent writes must still succeed.
    record_build_check_outcome("sess-A", passed=False, trw_dir=trw_dir)
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["sess-A"]["build_check_fail_count"] == 1
