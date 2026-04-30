"""Integration tests for ceremony state mutation wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


@pytest.mark.integration
class TestCeremonyStateMutationWiring:
    """Verify ceremony state is mutated correctly by session_start and deliver."""

    def test_session_start_calls_mark_session_started(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_session_start must set session_started=True in ceremony state."""
        from trw_mcp.state.ceremony_nudge import read_ceremony_state

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        state_before = read_ceremony_state(trw_dir)
        assert state_before.session_started is False

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True

        state_after = read_ceremony_state(trw_dir)
        assert state_after.session_started is True, (
            "step_mark_session_started was not called — session_started flag is still False"
        )

    def test_deliver_calls_mark_deliver(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """trw_deliver must set deliver_called=True in ceremony state."""
        from trw_mcp.state.ceremony_nudge import read_ceremony_state

        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        state_before = read_ceremony_state(trw_dir)
        assert state_before.deliver_called is False

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["success"] is True

        state_after = read_ceremony_state(trw_dir)
        assert state_after.deliver_called is True, "mark_deliver was not called — deliver_called flag is still False"
