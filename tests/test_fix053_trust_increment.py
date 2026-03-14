"""Tests for PRD-FIX-053-FR02: Relaxed Trust Increment Gate.

Verifies that _step_trust_increment fires for productive sessions
(3+ learnings AND 1+ checkpoints) even without a build_check event.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _write_events(run_dir: Path, events: list[dict]) -> None:
    """Write event dicts to the run's events.jsonl file."""
    events_path = run_dir / "meta" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory."""
    d = tmp_path / "runs" / "20260313T000000Z-abc123"
    d.mkdir(parents=True)
    (d / "meta").mkdir()
    return d


class TestTrustIncrementRelaxedGate:
    """FR02: Trust increment fires for productive sessions without build_check."""

    def test_productive_session_fires_trust(
        self, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 learnings + 1 checkpoint without build_check triggers trust increment."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        # 3 learn events + 1 checkpoint event, no build_check
        _write_events(run_dir, [
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_checkpoint"}},
        ])

        increment_calls: list = []

        def fake_increment(trw_dir: Path, agent_id: str) -> dict:
            increment_calls.append((trw_dir, agent_id))
            return {"incremented": True, "reason": "productive_session"}

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count", fake_increment
        )
        import trw_mcp.tools.ceremony as _cer
        import os
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: run_dir.parent.parent / ".trw")
        (run_dir.parent.parent / ".trw").mkdir(parents=True, exist_ok=True)

        result = _step_trust_increment(run_dir)

        assert result is not None
        assert result.get("skipped") is not True
        assert len(increment_calls) == 1

    def test_productive_session_reason_set(
        self, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When trust fires via productive_session, reason is productive_session."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        _write_events(run_dir, [
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_checkpoint"}},
        ])

        def fake_increment(trw_dir: Path, agent_id: str) -> dict:
            return {"incremented": True, "reason": "productive_session"}

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count", fake_increment
        )
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: run_dir.parent.parent / ".trw")
        (run_dir.parent.parent / ".trw").mkdir(parents=True, exist_ok=True)

        result = _step_trust_increment(run_dir)
        assert result is not None
        assert result.get("reason") == "productive_session"

    def test_insufficient_learnings_skips(
        self, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only 2 learnings + 1 checkpoint (not enough) → skipped."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        _write_events(run_dir, [
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_checkpoint"}},
        ])

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count",
            lambda *a, **kw: {"incremented": True},
        )
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: run_dir.parent.parent / ".trw")
        (run_dir.parent.parent / ".trw").mkdir(parents=True, exist_ok=True)

        result = _step_trust_increment(run_dir)
        assert result is not None
        assert result.get("skipped") is True

    def test_no_activity_skips_with_insufficient_reason(
        self, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """0 learnings, 0 checkpoints → skipped with insufficient_session_activity."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        _write_events(run_dir, [])

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count",
            lambda *a, **kw: {"incremented": True},
        )
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: run_dir.parent.parent / ".trw")
        (run_dir.parent.parent / ".trw").mkdir(parents=True, exist_ok=True)

        result = _step_trust_increment(run_dir)
        assert result is not None
        assert result.get("skipped") is True
        assert result.get("reason") == "insufficient_session_activity"

    def test_build_check_passed_still_fires(
        self, run_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Existing build_check path still fires trust increment."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        _write_events(run_dir, [
            {"event": "build_check_complete", "data": {"result": "pass"}},
        ])

        increment_calls: list = []

        def fake_increment(trw_dir: Path, agent_id: str) -> dict:
            increment_calls.append(True)
            return {"incremented": True, "reason": "build_check_passed"}

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count", fake_increment
        )
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: run_dir.parent.parent / ".trw")
        (run_dir.parent.parent / ".trw").mkdir(parents=True, exist_ok=True)

        result = _step_trust_increment(run_dir)
        assert result is not None
        assert result.get("skipped") is not True
        assert len(increment_calls) == 1

    def test_session_events_jsonl_counted(
        self, run_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Learnings in session-events.jsonl also count toward productive session."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        # Only 1 learning in events.jsonl, but 2 more in session-events.jsonl
        _write_events(run_dir, [
            {"event": "tool_invocation", "data": {"tool_name": "trw_learn"}},
            {"event": "tool_invocation", "data": {"tool_name": "trw_checkpoint"}},
        ])

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True)
        ctx_dir = trw_dir / "context"
        ctx_dir.mkdir()
        session_events_path = ctx_dir / "session-events.jsonl"
        with session_events_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({"event": "tool_invocation", "data": {"tool_name": "trw_learn"}}) + "\n")
            f.write(json.dumps({"event": "tool_invocation", "data": {"tool_name": "trw_learn"}}) + "\n")

        increment_calls: list = []

        def fake_increment(trw_dir_arg: Path, agent_id: str) -> dict:
            increment_calls.append(True)
            return {"incremented": True, "reason": "productive_session"}

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count", fake_increment
        )
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: trw_dir)

        result = _step_trust_increment(run_dir)
        assert result is not None
        # With 3 total learnings (1 run + 2 session) + 1 checkpoint → should fire
        assert result.get("skipped") is not True
        assert len(increment_calls) == 1

    def test_no_run_dir_skips_gracefully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """None run_dir returns a graceful skipped result."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        monkeypatch.setattr(
            "trw_mcp.state.trust.increment_session_count",
            lambda *a, **kw: {"incremented": True},
        )
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(
            _cer, "resolve_trw_dir",
            lambda: Path("/tmp/nonexistent/.trw"),
        )

        result = _step_trust_increment(None)
        # With no run dir, no events can be found - skips due to no activity
        assert result is not None
        assert result.get("skipped") is True

    @pytest.mark.parametrize("learn_count,checkpoint_count,should_fire", [
        # Exactly at threshold: 3 learnings + 1 checkpoint → fires
        (3, 1, True),
        # One below threshold: 2 learnings + 1 checkpoint → skips
        (2, 1, False),
        # No checkpoints: 5 learnings + 0 checkpoints → skips (need both)
        (5, 0, False),
        # Zero of everything → skips
        (0, 0, False),
        # Exactly 1 checkpoint + exactly 3 learnings → fires (boundary)
        (3, 1, True),
    ])
    def test_productive_session_boundary_conditions(
        self,
        run_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        learn_count: int,
        checkpoint_count: int,
        should_fire: bool,
    ) -> None:
        """Boundary conditions for productive session threshold (3 learns + 1 checkpoint)."""
        from trw_mcp.tools._deferred_delivery import _step_trust_increment

        events = (
            [{"event": "tool_invocation", "data": {"tool_name": "trw_learn"}}] * learn_count
            + [{"event": "tool_invocation", "data": {"tool_name": "trw_checkpoint"}}] * checkpoint_count
        )
        _write_events(run_dir, events)

        increment_calls: list[bool] = []

        def fake_increment(trw_dir_arg: Path, agent_id: str) -> dict:
            increment_calls.append(True)
            return {"incremented": True, "reason": "productive_session"}

        monkeypatch.setattr("trw_mcp.state.trust.increment_session_count", fake_increment)
        import trw_mcp.tools.ceremony as _cer
        monkeypatch.setattr(_cer, "resolve_trw_dir", lambda: run_dir.parent.parent / ".trw")
        (run_dir.parent.parent / ".trw").mkdir(parents=True, exist_ok=True)

        result = _step_trust_increment(run_dir)
        assert result is not None

        fired = len(increment_calls) > 0
        assert fired == should_fire, (
            f"learns={learn_count}, checkpoints={checkpoint_count}: "
            f"expected fired={should_fire}, got fired={fired}"
        )
