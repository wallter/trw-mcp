"""PRD-FIX-082: auto_close_stale_runs throttle persists across process restarts.

Pre-fix: _auto_close_last_ts was a module global, lost on process exit.
User-project stdio MCP installs spawn a fresh process per CLI invocation,
so the throttle never persisted past the single call. Every fresh process
paid the ~3-5s scan cost.

Post-fix: throttle timestamp persisted to .trw/runtime/auto_close_last_ts.json
atomically; loaded on the first call after process boot.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture
def trw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a minimal .trw layout and point analytics resolvers at it.

    The conftest._isolate_trw_dir autouse fixture patches
    ``trw_mcp.state._paths.resolve_project_root`` but ``analytics.report``
    binds it at import time, so we patch the consumer module's binding
    directly to ensure auto_close_stale_runs uses our tmp dir.
    """
    trw = tmp_path / ".trw"
    (trw / "runs").mkdir(parents=True)
    (trw / "runtime").mkdir(parents=True)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    def _fake_project_root() -> Path:
        return tmp_path

    def _fake_trw_dir() -> Path:
        return trw

    monkeypatch.setattr(
        "trw_mcp.state.analytics.report.resolve_project_root",
        _fake_project_root,
    )
    monkeypatch.setattr(
        "trw_mcp.state.analytics.report.resolve_trw_dir",
        _fake_trw_dir,
    )
    return trw


def _seed_persisted_throttle(trw: Path, age_minutes: float) -> Path:
    """Write a persisted throttle file with last_ts age_minutes minutes ago."""
    state_path = trw / "runtime" / "auto_close_last_ts.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    last_ts = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    state_path.write_text(json.dumps({"last_ts": last_ts.isoformat(), "version": 1}))
    return state_path


def test_persisted_recent_timestamp_throttles_first_call(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A persisted timestamp <1 hour old throttles the first call after boot."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    _seed_persisted_throttle(trw_dir, age_minutes=30)
    # Reset in-memory state to simulate a fresh process boot.
    _reset_auto_close_throttle()

    result = auto_close_stale_runs(ttl_hours=48)

    assert result.get("throttled") is True
    assert result.get("count") == 0
    next_eligible = float(str(result.get("next_eligible_in_seconds", "0") or "0"))
    # 30 min ago means ~30 min remaining in the 1-hour window.
    assert 1500 < next_eligible < 2000  # ~30 min ± wiggle


def test_persisted_stale_timestamp_does_not_throttle(
    trw_dir: Path,
) -> None:
    """A persisted timestamp >=1 hour old is treated as no-prior-call."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    _seed_persisted_throttle(trw_dir, age_minutes=120)  # 2 hours ago
    _reset_auto_close_throttle()

    result = auto_close_stale_runs(ttl_hours=48)

    # Sweep ran (not throttled). With no actual runs to close, count is 0
    # but throttled is absent or False.
    assert not result.get("throttled")


def test_first_real_call_persists_timestamp(trw_dir: Path) -> None:
    """A non-throttled call writes auto_close_last_ts.json atomically."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    state_path = trw_dir / "runtime" / "auto_close_last_ts.json"
    if state_path.exists():
        state_path.unlink()
    _reset_auto_close_throttle()

    auto_close_stale_runs(ttl_hours=48)

    assert state_path.exists(), "throttle state file should be written after sweep"
    data = json.loads(state_path.read_text())
    assert data.get("version") == 1
    assert isinstance(data.get("last_ts"), str)
    # The persisted timestamp should be very recent.
    last_dt = datetime.fromisoformat(str(data["last_ts"]).replace("Z", "+00:00"))
    age_seconds = (datetime.now(timezone.utc) - last_dt).total_seconds()
    assert 0 <= age_seconds < 5


def test_force_true_bypasses_persisted_throttle(trw_dir: Path) -> None:
    """force=True runs the sweep even when persisted state would throttle."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    _seed_persisted_throttle(trw_dir, age_minutes=10)  # well within window
    _reset_auto_close_throttle()

    result = auto_close_stale_runs(ttl_hours=48, force=True)

    assert not result.get("throttled")


def test_malformed_state_file_falls_back_safely(
    trw_dir: Path,
) -> None:
    """A malformed throttle file is treated as no-prior-call (fail-safe)."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    state_path = trw_dir / "runtime" / "auto_close_last_ts.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("not json at all {{{{")
    _reset_auto_close_throttle()

    result = auto_close_stale_runs(ttl_hours=48)

    # Should run the sweep (not throttled) and overwrite the malformed file.
    assert not result.get("throttled")
    # File rewritten with valid JSON.
    data = json.loads(state_path.read_text())
    assert data.get("version") == 1


def test_atomic_write_no_partial_files(trw_dir: Path) -> None:
    """The temp-then-rename pattern leaves no .auto_close_* siblings."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    _reset_auto_close_throttle()
    auto_close_stale_runs(ttl_hours=48)

    # No leftover temp files.
    siblings = list((trw_dir / "runtime").iterdir())
    temp_files = [p for p in siblings if p.name.startswith(".auto_close_")]
    assert temp_files == [], f"temp files leaked: {temp_files}"


def test_reset_with_trw_dir_removes_persisted_file(trw_dir: Path) -> None:
    """Test hook: _reset_auto_close_throttle(trw_dir) deletes the file."""
    from trw_mcp.state.analytics._stale_runs import (
        _reset_auto_close_throttle,
        auto_close_stale_runs,
    )

    _reset_auto_close_throttle()
    auto_close_stale_runs(ttl_hours=48)
    state_path = trw_dir / "runtime" / "auto_close_last_ts.json"
    assert state_path.exists()

    _reset_auto_close_throttle(trw_dir)
    assert not state_path.exists()
