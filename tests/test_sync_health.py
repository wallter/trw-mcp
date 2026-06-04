"""Tests for step_sync_health — PRD-FIX-COMPOUNDING-1.

Covers the sync-push health surface that converts the silent
sync-state.json failure counter into an operator-visible advisory on
trw_session_start. All paths are fail-open (never raise).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._ceremony_helpers import step_sync_health


def _write_state(trw_dir: Path, state: dict[str, object]) -> None:
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "sync-state.json").write_text(json.dumps(state, indent=2))


def _iso_ago(hours: float) -> str:
    return (datetime.now(tz=timezone.utc) - timedelta(hours=hours)).isoformat()


def _config(tmp_path: Path, **overrides: object) -> TRWConfig:
    return TRWConfig(trw_dir=str(tmp_path / ".trw"), **overrides)  # type: ignore[arg-type]


def test_degraded_on_failure_threshold(tmp_path: Path) -> None:
    """consecutive_failures >= threshold => degraded with non-empty advisory."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 11, "last_push_at": _iso_ago(0.1)})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is True
    assert result["consecutive_failures"] == 11
    assert isinstance(result["advisory"], str)
    assert result["advisory"] != ""
    # Advisory must report the failure count for actionability (NFR03).
    assert "11" in result["advisory"]


def test_degraded_on_staleness(tmp_path: Path) -> None:
    """last_push_at older than stale_hours => degraded even if failures low."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 2, "last_push_at": _iso_ago(48)})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is True
    assert result["advisory"] != ""


def test_healthy_state(tmp_path: Path) -> None:
    """Low failures + recent push => not degraded, empty advisory."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.5)})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is False
    assert result["advisory"] == ""
    assert result["consecutive_failures"] == 0


def test_fail_open_on_missing_file(tmp_path: Path) -> None:
    """No sync-state.json (fresh install) => safe default, no false positive."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is False
    assert result["advisory"] == ""
    assert result["consecutive_failures"] == 0
    assert result["last_push_at"] is None


def test_fail_open_on_corrupted_json(tmp_path: Path) -> None:
    """Corrupted sync-state.json => safe default without raising."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "sync-state.json").write_text("{ this is not valid json ")
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is False
    assert result["advisory"] == ""


def test_config_threshold_override(tmp_path: Path) -> None:
    """sync_health_failure_threshold=1 makes a single failure degrade."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 1, "last_push_at": _iso_ago(0.1)})
    config = _config(tmp_path, sync_health_failure_threshold=1)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is True
    assert result["advisory"] != ""


def test_threshold_boundary_at(tmp_path: Path) -> None:
    """consecutive_failures == threshold (10) => degraded."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 10, "last_push_at": _iso_ago(0.1)})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is True


def test_threshold_boundary_below(tmp_path: Path) -> None:
    """consecutive_failures == 9 (below threshold) + recent push => healthy."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 9, "last_push_at": _iso_ago(0.1)})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is False
    assert result["advisory"] == ""


def test_missing_last_push_at_is_degraded(tmp_path: Path) -> None:
    """Failures present but last_push_at absent => treated as 'never' => degraded."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 3})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    assert result["degraded"] is True
    assert result["last_push_at"] is None
    assert "never" in result["advisory"].lower()


def test_advisory_includes_remediation(tmp_path: Path) -> None:
    """Advisory directs operator to the config file (NFR03)."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 50, "last_push_at": _iso_ago(72)})
    config = _config(tmp_path)

    result = step_sync_health(trw_dir, config)

    advisory = str(result["advisory"])
    assert "config.yaml" in advisory


def test_latency_under_budget(tmp_path: Path) -> None:
    """NFR01: step adds <= 5ms p95. Single file read is O(1)."""
    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 0, "last_push_at": _iso_ago(0.1)})
    config = _config(tmp_path)

    durations: list[float] = []
    for _ in range(50):
        start = time.monotonic()
        step_sync_health(trw_dir, config)
        durations.append((time.monotonic() - start) * 1000.0)
    durations.sort()
    p95 = durations[int(len(durations) * 0.95)]
    assert p95 <= 5.0, f"p95 {p95:.2f}ms exceeds 5ms budget"


def test_config_fields_defaults() -> None:
    """FR04: default threshold=10, stale_hours=6.0."""
    config = TRWConfig()
    assert config.sync_health_failure_threshold == 10
    assert config.sync_health_stale_hours == 6.0


def test_config_env_override(monkeypatch: object) -> None:
    """FR04: env override TRW_SYNC_HEALTH_FAILURE_THRESHOLD."""
    import os

    os.environ["TRW_SYNC_HEALTH_FAILURE_THRESHOLD"] = "1"
    os.environ["TRW_SYNC_HEALTH_STALE_HOURS"] = "2.5"
    try:
        config = TRWConfig()
        assert config.sync_health_failure_threshold == 1
        assert config.sync_health_stale_hours == 2.5
    finally:
        del os.environ["TRW_SYNC_HEALTH_FAILURE_THRESHOLD"]
        del os.environ["TRW_SYNC_HEALTH_STALE_HOURS"]


def test_sync_health_in_session_start_typed_dict() -> None:
    """FR03: SessionStartResultDict declares sync_health."""
    from trw_mcp.models.typed_dicts import SessionStartResultDict

    assert "sync_health" in SessionStartResultDict.__annotations__


def test_step_embed_health_still_present() -> None:
    """Regression: step_embed_health not accidentally removed."""
    from trw_mcp.tools._ceremony_helpers import step_embed_health

    result = step_embed_health()
    assert "advisory" in result


def test_session_start_contains_sync_health(tmp_path: Path) -> None:
    """FR02 integration: real trw_session_start response includes sync_health."""
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    # verbose=True: sync_health is a diagnostic sub-block that compact-by-default
    # (PRD-IMPROVE-MCP-04) folds into the one-line health_summary.
    result = fn(ctx=None, query="*", verbose=True)

    assert "sync_health" in result, f"sync_health missing; got keys: {sorted(result.keys())}"
    sync_health = result["sync_health"]
    assert isinstance(sync_health, dict)
    assert "degraded" in sync_health
    assert "advisory" in sync_health
    assert "consecutive_failures" in sync_health


def test_session_start_sync_health_degraded_warning(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """FR02 integration: a degraded sync-state surfaces a non-empty advisory.

    Patches the trw_dir resolver used by trw_session_start so the tool reads a
    sync-state.json with a failure count well above threshold.
    """
    import pytest

    from tests.conftest import extract_tool_fn, make_test_server

    assert isinstance(monkeypatch, pytest.MonkeyPatch)

    trw_dir = tmp_path / ".trw"
    _write_state(trw_dir, {"consecutive_failures": 9067, "last_push_at": _iso_ago(1008)})

    # step_sync_health resolves trw_dir via resolve_trw_dir() in ceremony.py.
    import trw_mcp.tools.ceremony as ceremony_mod

    monkeypatch.setattr(ceremony_mod, "resolve_trw_dir", lambda: trw_dir)

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    # verbose=True: full sync_health diagnostic block (compact mode folds it into
    # health_summary — PRD-IMPROVE-MCP-04).
    result = fn(ctx=None, query="*", verbose=True)

    sync_health = result["sync_health"]
    assert sync_health["degraded"] is True
    assert sync_health["advisory"] != ""
    assert "9067" in str(sync_health["advisory"])
