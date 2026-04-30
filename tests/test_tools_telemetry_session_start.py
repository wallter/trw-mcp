"""Tests for telemetry session_start event logging."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests._tools_telemetry_support import _make_ceremony_tools, _read_jsonl, reset_telemetry_cache, run_dir  # noqa: F401


class TestSessionStartEvent:
    """T-05 through T-07: FR01 session_start event logging."""

    def _setup_trw_project(self, tmp_path: Path) -> Path:
        """Create minimal .trw/ structure needed for trw_session_start."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "logs").mkdir(parents=True)
        return trw_dir

    def test_t05_session_start_event_written_to_run_events_jsonl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        run_dir: Path,
    ) -> None:
        """T-05: session_start event is written to run's events.jsonl when a run exists."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True

        session_events = [
            r
            for r in _read_jsonl(run_dir / "meta" / "events.jsonl")
            if r.get("event") == "session_start"
        ]
        assert len(session_events) == 1

        ev = session_events[0]
        assert "ts" in ev
        assert "learnings_recalled" in ev
        assert "run_detected" in ev
        assert ev["run_detected"] is True

    def test_t06_session_start_fallback_to_session_events_jsonl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-06: When no active run, session_start event falls back to session-events.jsonl."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True

        fallback_path = trw_dir / "context" / "session-events.jsonl"
        assert fallback_path.exists(), "session-events.jsonl fallback was not created"

        session_events = [r for r in _read_jsonl(fallback_path) if r.get("event") == "session_start"]
        assert len(session_events) == 1

        ev = session_events[0]
        assert ev["run_detected"] is False
        assert "learnings_recalled" in ev

    def test_t07_event_write_failure_does_not_cause_session_start_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-07: Event write failure in FR01 block does not affect trw_session_start result."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
            patch(
                "trw_mcp.tools.ceremony._events",
                MagicMock(log_event=MagicMock(side_effect=OSError("write failure"))),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert all("session_start" not in e for e in result.get("errors", []))

    def test_t07_second_resolve_trw_dir_failure_is_silenced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-07 variant: If resolve_trw_dir raises inside FR01 fallback block, no error surfaces."""
        trw_dir = self._setup_trw_project(tmp_path)
        tools = _make_ceremony_tools(monkeypatch, tmp_path)
        call_count: list[int] = [0]

        def resolve_trw_dir_side_effect() -> Path:
            call_count[0] += 1
            if call_count[0] == 1:
                return trw_dir
            raise OSError("no trw dir")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", side_effect=resolve_trw_dir_side_effect),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", return_value=[]),
        ):
            result = tools["trw_session_start"].fn()

        assert isinstance(result, dict)
        assert "success" in result
