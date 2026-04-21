"""PRD-CORE-144 FR01: session_id resolver + surface-event propagation."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state._session_id import (
    _get_process_session_id,
    _reset_process_session_id,
    resolve_effective_session_id,
)
from trw_mcp.state.surface_tracking import log_surface_event


@pytest.fixture(autouse=True)
def _reset_process_id() -> None:
    """Reset cached process UUID before each test."""
    _reset_process_session_id(None)


class TestResolveEffectiveSessionId:
    def test_falls_back_to_process_uuid_when_no_run_or_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRW_SESSION_ID", raising=False)
        with patch("trw_mcp.state._paths.find_active_run", return_value=None):
            sid = resolve_effective_session_id(tmp_path)
        assert sid
        assert re.fullmatch(r"[0-9a-f]{32}", sid), "expected a UUIDv4 hex"

    def test_process_uuid_stable_across_calls(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRW_SESSION_ID", raising=False)
        with patch("trw_mcp.state._paths.find_active_run", return_value=None):
            sid1 = resolve_effective_session_id(tmp_path)
            sid2 = resolve_effective_session_id(tmp_path)
        assert sid1 == sid2

    def test_env_var_takes_precedence_over_process_uuid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_SESSION_ID", "operator-forced-id")
        with patch("trw_mcp.state._paths.find_active_run", return_value=None):
            sid = resolve_effective_session_id(tmp_path)
        assert sid == "operator-forced-id"

    def test_active_run_wins_over_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRW_SESSION_ID", "env-id")
        fake_run = tmp_path / "task1" / "run-abc123" / "meta"
        fake_run.mkdir(parents=True)
        with patch(
            "trw_mcp.state._paths.find_active_run",
            return_value=fake_run.parent,
        ):
            sid = resolve_effective_session_id(tmp_path)
        assert sid == "run-abc123"

    def test_lookup_exception_falls_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("TRW_SESSION_ID", raising=False)
        with patch(
            "trw_mcp.state._paths.find_active_run",
            side_effect=RuntimeError("boom"),
        ):
            sid = resolve_effective_session_id(tmp_path)
        # must not raise; must produce some stable id
        assert sid == _get_process_session_id()


class TestSurfaceEventSessionIdPropagation:
    def _read_events(self, trw_dir: Path) -> list[dict]:
        p = trw_dir / "logs" / "surface_tracking.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def test_recall_impl_surface_events_have_session_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Import the function under test lazily.
        from trw_mcp.tools import _recall_impl

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        # Patch find_active_run to return a fake run so we get a deterministic id.
        fake_run = trw_dir / "runs" / "task" / "run-XYZ" / "meta"
        fake_run.mkdir(parents=True)
        monkeypatch.setattr(
            "trw_mcp.state._paths.find_active_run",
            lambda: fake_run.parent,
        )
        monkeypatch.setattr(_recall_impl, "_detect_surface_phase", lambda: "implement")

        ranked = [{"id": "L-1"}, {"id": "L-2"}]
        _recall_impl._log_recall_surface_events(trw_dir, ranked, recall_context=None)

        events = self._read_events(trw_dir)
        assert len(events) == 2
        assert all(e["session_id"] == "run-XYZ" for e in events)
        assert all(e["surface_type"] == "recall" for e in events)

    def test_legacy_empty_session_id_events_still_readable(
        self, tmp_path: Path
    ) -> None:
        """Pre-fix data with session_id="" must not crash readers."""
        from trw_mcp.state.surface_tracking import read_surface_events

        trw_dir = tmp_path / ".trw"
        # Emit an event with the legacy empty session_id via direct call.
        log_surface_event(trw_dir, learning_id="L-legacy", surface_type="nudge", session_id="")
        events = read_surface_events(trw_dir)
        assert len(events) == 1
        assert events[0]["session_id"] == ""  # preserved, not crashing
