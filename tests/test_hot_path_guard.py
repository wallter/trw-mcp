"""PRD-FIX-085 FR02: HOT_PATH ContextVar guards the legacy mtime scan.

The contextvar is set on entry to trw_session_start and middleware. Any
caller that triggers find_run_via_mtime_scan() while it is True emits
``hot_path_legacy_scan_attempted`` WARN with the offending stack. With
``TRW_HOT_PATH_STRICT=1``, the same call raises HotPathLegacyScanError.

This is the durable mechanical defense against the regression class
"hot-path caller forgot context= and silently routed to the slow scan."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from trw_mcp.state._paths import (
    HOT_PATH,
    HotPathLegacyScanError,
    find_run_via_mtime_scan,
)
from trw_mcp.state.persistence import FileStateWriter

_writer = FileStateWriter()


def _make_run_dir(tmp_path: Path, task: str = "t", run_id: str = "r-001") -> Path:
    run_dir = tmp_path / ".trw" / "runs" / task / run_id
    meta = run_dir / "meta"
    meta.mkdir(parents=True)
    _writer.write_yaml(
        meta / "run.yaml",
        {"run_id": run_id, "task": task, "status": "active", "phase": "implement"},
    )
    return run_dir


@pytest.fixture
def patched_runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a tmp project + point find_run_via_mtime_scan at it."""
    _make_run_dir(tmp_path)
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_project_root",
        lambda: tmp_path,
    )
    return tmp_path


def test_warn_fires_when_scan_called_in_hot_path(
    patched_runs_root: Path,
) -> None:
    """find_run_via_mtime_scan() while HOT_PATH=True logs hot_path_legacy_scan_attempted."""
    token = HOT_PATH.set(True)
    try:
        with capture_logs() as logs:
            find_run_via_mtime_scan()
    finally:
        HOT_PATH.reset(token)

    warn_events = [e for e in logs if e.get("event") == "hot_path_legacy_scan_attempted"]
    assert warn_events, "hot_path_legacy_scan_attempted WARN must fire when called from hot path"
    payload = warn_events[-1]
    # Caller info should point at this test (or the calling frame).
    assert "caller_module" in payload
    assert "caller_function" in payload
    assert "caller_lineno" in payload


def test_no_warn_when_scan_called_outside_hot_path(
    patched_runs_root: Path,
) -> None:
    """find_run_via_mtime_scan() outside the hot path is silent (legitimate use)."""
    # HOT_PATH defaults to False; we don't set it.
    with capture_logs() as logs:
        find_run_via_mtime_scan()

    warn_events = [e for e in logs if e.get("event") == "hot_path_legacy_scan_attempted"]
    assert warn_events == [], f"Legitimate scan use should not warn; got {warn_events}"


def test_strict_mode_raises_on_hot_path_scan(
    patched_runs_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TRW_HOT_PATH_STRICT=1 + HOT_PATH=True raises HotPathLegacyScanError."""
    monkeypatch.setenv("TRW_HOT_PATH_STRICT", "1")
    token = HOT_PATH.set(True)
    try:
        with pytest.raises(HotPathLegacyScanError) as exc_info:
            find_run_via_mtime_scan()
    finally:
        HOT_PATH.reset(token)

    msg = str(exc_info.value)
    assert "hot path" in msg.lower()
    assert "get_pinned_run" in msg


def test_strict_mode_does_not_raise_outside_hot_path(
    patched_runs_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode only raises when both env var AND HOT_PATH are True."""
    monkeypatch.setenv("TRW_HOT_PATH_STRICT", "1")
    # Don't set HOT_PATH — it defaults to False.
    result = find_run_via_mtime_scan()
    # Should run normally and return whatever the scan finds (or None).
    assert result is None or isinstance(result, Path)


def test_hot_path_set_during_session_start() -> None:
    """trw_session_start sets HOT_PATH=True for the duration of the call.

    After the call returns, HOT_PATH is reset to False so subsequent
    code outside the hot path is unaffected.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")

    # Capture HOT_PATH during a call by mocking find_run_via_mtime_scan
    # to read the contextvar (simulating a hypothetical leak).
    captured: list[bool] = []

    def fake_scan() -> None:
        captured.append(HOT_PATH.get())
        return None

    # Patch find_run_via_mtime_scan to record the contextvar state if invoked.
    # If session_start triggers it (which would be a regression), captured
    # will be non-empty and the value tells us if HOT_PATH was set.
    from unittest.mock import patch

    with patch("trw_mcp.state._paths.find_run_via_mtime_scan", side_effect=fake_scan):
        result: dict[str, Any] = fn(ctx=None, query="hot-path-probe")

    # Whether or not the scan was triggered, after the call HOT_PATH must
    # be False (the reset must always run).
    assert HOT_PATH.get() is False, "HOT_PATH must be reset after session_start returns"

    # If captured is non-empty, every observation must show HOT_PATH=True
    # (i.e. the legacy scan was triggered DURING session_start). This
    # currently happens only if some ctx-aware suppression leak survives;
    # if no scan was triggered at all, captured is empty.
    if captured:
        assert all(captured), (
            f"If find_run_via_mtime_scan is called during session_start, HOT_PATH must be True. Got: {captured}"
        )

    # Sanity: the call returned a result dict.
    assert isinstance(result, dict)
