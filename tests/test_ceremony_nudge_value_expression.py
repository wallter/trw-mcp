"""Tests for ceremony nudge value expression rules."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    NudgeContext,
    ToolName,
    _context_reactive_message,
    _select_nudge_message,
    compute_nudge,
)


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
        assert "7" in result
        assert any(word in result.lower() for word in ("compaction", "compact", "lose", "lost", "erases"))

    def test_fr02_checkpoint_nudge_includes_elapsed_time(self, tmp_path: Path) -> None:
        """FR02: Checkpoint nudge includes elapsed time when last_checkpoint_ts is set."""
        import re
        from datetime import datetime, timedelta, timezone

        past_ts = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
        state = CeremonyState(
            session_started=True,
            files_modified_since_checkpoint=3,
            last_checkpoint_ts=past_ts,
            checkpoint_count=1,
            phase="implement",
        )
        result = _select_nudge_message("checkpoint", state, available_learnings=0)
        assert "min ago" in result

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
        assert "4" in result
        assert any(word in result.lower() for word in ("future", "persist", "session"))
        assert any(word in result.lower() for word in ("lost", "lose", "discard", "skip", "skipping"))

    def test_fr02_session_start_nudge_content(self, tmp_path: Path) -> None:
        """Session start nudge includes available learnings count."""
        state = CeremonyState(session_started=False)
        result = _select_nudge_message("session_start", state, available_learnings=8)
        assert "8" in result
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
        assert any(word in result.lower() for word in ("not run", "build check"))
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
