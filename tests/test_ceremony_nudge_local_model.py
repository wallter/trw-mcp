"""Tests for ceremony nudge local-model scoping and contextual variants."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._ceremony_nudge_support import _trw_dir
from trw_mcp.state.ceremony_nudge import (
    CeremonyState,
    compute_nudge_contextual,
    compute_nudge_contextual_action,
    compute_nudge_learning_injection,
    compute_nudge_minimal,
    is_local_model,
)


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
