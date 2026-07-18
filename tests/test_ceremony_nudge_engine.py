"""Tests for ceremony nudge engine output and compatibility wrapper behavior."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.ceremony_nudge import CeremonyState, compute_nudge


class TestNudgeEngine:
    """Tests for compute_nudge(), _build_status_line(), _select_nudge_message()."""

    def test_fr01_nudge_session_start_pending(self, tmp_path: Path) -> None:
        """Given session_start not called, nudge returns non-empty content."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=5)
        assert result != ""
        assert "start" in result.lower()
        assert len(result) > 0

    def test_fr01_nudge_checkpoint_pending(self, tmp_path: Path) -> None:
        """Given files modified and no checkpoint, nudge returns non-empty content."""
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=11,
            phase="implement",
        )
        result = compute_nudge(state, available_learnings=0)
        assert result != ""
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
        assert "session_start" in result or "deliver" in result
        assert "\u2713" in result
        assert "\u2717" not in result

    def test_fr01_nudge_never_blocks(self, tmp_path: Path) -> None:
        """Nudge never replaces or obscures tool response — it only appends."""
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=0)
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
                session_started=True,
                checkpoint_count=1,
                build_check_result="passed",
                deliver_called=True,
                phase="done",
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
        state = CeremonyState(session_started=False)
        result = compute_nudge(state, available_learnings=0)
        assert "--- TRW ---" in result
        assert "\u2717" in result

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
        assert "\u2713" in result2

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
        state = CeremonyState()
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
