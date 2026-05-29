"""Ceremony scoring and nudge-wiring orchestration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401


class TestCeremonyScoring:
    """Tests for compute_ceremony_score() — direct and tool_invocation event formats."""

    def _score(self, events: list[dict[str, object]]) -> dict[str, object]:
        from trw_mcp.state.analytics.report import compute_ceremony_score

        return compute_ceremony_score(events)

    def test_direct_session_start_detected(self) -> None:
        result = self._score([{"event": "session_start"}])
        assert result["session_start"] is True
        assert result["score"] == 25
        assert result["review"] is False

    def test_direct_deliver_via_reflection_complete(self) -> None:
        result = self._score([{"event": "reflection_complete"}])
        assert result["deliver"] is True
        assert result["score"] == 25

    def test_direct_checkpoint_counted(self) -> None:
        result = self._score(
            [
                {"event": "checkpoint"},
                {"event": "checkpoint"},
            ]
        )
        assert result["checkpoint_count"] == 2
        assert result["score"] == 20

    def test_direct_learn_counted(self) -> None:
        result = self._score([{"event": "learn_recorded"}])
        assert result["learn_count"] == 1
        assert result["score"] == 10

    def test_direct_build_check_complete(self) -> None:
        result = self._score([{"event": "build_check_complete", "tests_passed": "true"}])
        assert result["build_check"] is True
        assert result["build_passed"] is True
        assert result["score"] == 10

    def test_tool_invocation_session_start_detected(self) -> None:
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_session_start"},
            ]
        )
        assert result["session_start"] is True
        assert result["score"] == 25

    def test_tool_invocation_deliver_via_trw_deliver(self) -> None:
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_deliver"},
            ]
        )
        assert result["deliver"] is True
        assert result["score"] == 25

    def test_tool_invocation_deliver_via_trw_reflect(self) -> None:
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_reflect"},
            ]
        )
        assert result["deliver"] is True

    def test_tool_invocation_checkpoint_counted(self) -> None:
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
                {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
                {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
            ]
        )
        assert result["checkpoint_count"] == 3
        assert result["score"] == 20

    def test_tool_invocation_learn_counted(self) -> None:
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_learn"},
                {"event": "tool_invocation", "tool_name": "trw_learn"},
            ]
        )
        assert result["learn_count"] == 2
        assert result["score"] == 10

    def test_tool_invocation_build_check(self) -> None:
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_build_check"},
            ]
        )
        assert result["build_check"] is True
        assert result["score"] == 10

    def test_trw_deliver_complete_event_detected(self) -> None:
        result = self._score([{"event": "trw_deliver_complete"}])
        assert result["deliver"] is True
        assert result["score"] == 25

    def test_mixed_formats_full_score(self) -> None:
        """Real-world mix: tool_invocation events produce full 100-point score."""
        events: list[dict[str, object]] = [
            {"event": "tool_invocation", "tool_name": "trw_session_start"},
            {"event": "tool_invocation", "tool_name": "trw_learn"},
            {"event": "tool_invocation", "tool_name": "trw_learn"},
            {"event": "tool_invocation", "tool_name": "trw_checkpoint"},
            {"event": "tool_invocation", "tool_name": "trw_build_check"},
            {"event": "tool_invocation", "tool_name": "trw_deliver"},
            {"event": "tool_invocation", "tool_name": "trw_review"},
        ]
        result = self._score(events)
        assert result["session_start"] is True
        assert result["deliver"] is True
        assert result["checkpoint_count"] == 1
        assert result["learn_count"] == 2
        assert result["build_check"] is True
        assert result["review"] is True
        assert result["score"] == 100

    def test_unrelated_tool_invocation_ignored(self) -> None:
        """tool_invocation with unrelated tool_name does not affect score."""
        result = self._score(
            [
                {"event": "tool_invocation", "tool_name": "trw_status"},
            ]
        )
        assert result["score"] == 0

    def test_empty_events_zero_score(self) -> None:
        result = self._score([])
        assert result["score"] == 0
        assert result["session_start"] is False
        assert result["deliver"] is False
        assert result["checkpoint_count"] == 0
        assert result["learn_count"] == 0
        assert result["build_check"] is False
        assert result["review"] is False


class TestCeremonyNudgeWiring:
    """Verify ceremony_status is injected into orchestration tool responses."""

    def test_trw_init_includes_ceremony_status(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_init response must contain 'ceremony_status' key after nudge injection."""
        result = orch_tools["trw_init"].fn(task_name="nudge-init-task")
        assert "ceremony_status" in result, "trw_init did not inject ceremony_status — nudge wiring is broken"
        assert isinstance(result["ceremony_status"], str)

    def test_trw_status_includes_ceremony_status(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_status response must contain 'ceremony_status' key after nudge injection."""
        init_result = orch_tools["trw_init"].fn(task_name="nudge-status-task")
        status_result = orch_tools["trw_status"].fn(run_path=init_result["run_path"])
        assert "ceremony_status" in status_result, "trw_status did not inject ceremony_status — nudge wiring is broken"
        assert isinstance(status_result["ceremony_status"], str)

    def test_trw_checkpoint_includes_ceremony_status(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_checkpoint response must contain 'ceremony_status' key after nudge injection."""
        init_result = orch_tools["trw_init"].fn(task_name="nudge-cp-task")
        cp_result = orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="nudge checkpoint test",
        )
        assert "ceremony_status" in cp_result, "trw_checkpoint did not inject ceremony_status — nudge wiring is broken"
        assert isinstance(cp_result["ceremony_status"], str)

    def test_trw_checkpoint_calls_mark_checkpoint(
        self,
        tmp_path: Path,
        orch_tools: dict[str, Any],
    ) -> None:
        """trw_checkpoint must call mark_checkpoint so ceremony state is updated."""
        from trw_mcp.state.ceremony_nudge import read_ceremony_state

        init_result = orch_tools["trw_init"].fn(task_name="mark-cp-task")
        trw_dir = tmp_path / ".trw"

        state_before = read_ceremony_state(trw_dir)
        count_before = state_before.checkpoint_count

        orch_tools["trw_checkpoint"].fn(
            run_path=init_result["run_path"],
            message="mark checkpoint test",
        )

        state_after = read_ceremony_state(trw_dir)
        assert state_after.checkpoint_count == count_before + 1, (
            "mark_checkpoint was not called — checkpoint_count did not increment"
        )
