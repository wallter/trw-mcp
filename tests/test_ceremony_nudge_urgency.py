"""Tests for ceremony nudge urgency progression."""

from __future__ import annotations

from pathlib import Path

from tests._ceremony_nudge_support import _trw_dir
from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    _compute_urgency,
    _select_nudge_message,
    compute_nudge,
    mark_checkpoint,
    read_ceremony_state,
    reset_nudge_count,
    write_ceremony_state,
)


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
        assert "5" in result
        assert "compaction" in result.lower() or "compact" in result.lower()
        assert "permanently" not in result.lower()

    def test_fr03_urgency_medium_3_nudges(self, tmp_path: Path) -> None:
        """After 3 nudges, checkpoint message adds concrete risk language."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={"checkpoint": 3},
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        assert any(phrase in result.lower() for phrase in ("no recovery", "recovery path", "compaction risk", "risk"))

    def test_fr03_urgency_high_5_nudges(self, tmp_path: Path) -> None:
        """After 5 nudges, checkpoint message adds consequence + effort framing."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={"checkpoint": 5},
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
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
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=5,
            nudge_counts={"checkpoint": 7},
        )
        write_ceremony_state(trw, state)
        assert read_ceremony_state(trw).nudge_counts.get("checkpoint") == 7

        reset_nudge_count(trw, "checkpoint")
        result = read_ceremony_state(trw)
        assert result.nudge_counts.get("checkpoint", 0) == 0
        mark_checkpoint(trw)
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
            {},
            {"checkpoint": 3},
            {"checkpoint": 6},
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
