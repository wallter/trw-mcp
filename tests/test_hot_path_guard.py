"""PRD-FIX-085 FR02: HOT_PATH ContextVar guards the legacy mtime scan.

The contextvar is set on entry to trw_session_start and middleware. Any
caller that reaches ``resolve_run_path``'s mtime fallback while it is True
emits ``hot_path_legacy_scan_attempted`` WARN with the offending stack. With
``TRW_HOT_PATH_STRICT=1``, the same call raises HotPathLegacyScanError.

PRD-FIX-132 deleted the sibling explicit opt-in scan helper these tests used
to drive -- it had no production caller. The guard itself is unchanged and
still live, so the tests now drive it through the fallback that remains: a
``resolve_run_path()`` call with neither ``run_path`` nor ``context``.

This is the durable mechanical defense against the regression class
"hot-path caller forgot context= and silently routed to the slow scan."
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.state._paths import (
    HOT_PATH,
    HotPathLegacyScanError,
    resolve_run_path,
    unpin_active_run,
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
    """Build a tmp project + point run resolution at it, with no pin set.

    The pin must be cleared: with one, ``resolve_run_path`` short-circuits
    before the guarded fallback and the guard would never be exercised.
    """
    _make_run_dir(tmp_path)
    monkeypatch.setattr(
        "trw_mcp.state._paths.resolve_project_root",
        lambda: tmp_path,
    )
    unpin_active_run()
    return tmp_path


def test_warn_fires_when_scan_called_in_hot_path(
    patched_runs_root: Path,
    captured_structlog: list[dict],
) -> None:
    """The mtime fallback while HOT_PATH=True logs hot_path_legacy_scan_attempted.

    Uses the shared ``captured_structlog`` fixture (not raw capture_logs):
    sibling startup-path tests leak a CRITICAL filtering wrapper via the
    ``trw_mcp.server._app`` import side effect, which drops WARN events before
    capture_logs processors run. The fixture resets structlog to a
    capture-friendly default and restores it on teardown.
    """
    token = HOT_PATH.set(True)
    try:
        resolve_run_path()
    finally:
        HOT_PATH.reset(token)

    warn_events = [e for e in captured_structlog if e.get("event") == "hot_path_legacy_scan_attempted"]
    assert warn_events, "hot_path_legacy_scan_attempted WARN must fire when called from hot path"
    payload = warn_events[-1]
    # Caller info should point at this test (or the calling frame).
    assert "caller_module" in payload
    assert "caller_function" in payload
    assert "caller_lineno" in payload


def test_no_warn_when_scan_called_outside_hot_path(
    patched_runs_root: Path,
    captured_structlog: list[dict],
) -> None:
    """The mtime fallback outside the hot path is silent (legitimate use)."""
    # HOT_PATH defaults to False; we don't set it.
    resolve_run_path()

    warn_events = [e for e in captured_structlog if e.get("event") == "hot_path_legacy_scan_attempted"]
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
            resolve_run_path()
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
    result = resolve_run_path()
    # Should run normally and return whatever the fallback finds.
    assert isinstance(result, Path)


def test_hot_path_set_during_session_start() -> None:
    """trw_session_start sets HOT_PATH=True for the duration of the call.

    After the call returns, HOT_PATH is reset to False so subsequent
    code outside the hot path is unaffected.
    """
    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")

    # Capture HOT_PATH during a call by mocking the mtime scan helper
    # to read the contextvar (simulating a hypothetical leak).
    captured: list[bool] = []

    def fake_scan(_base: Path) -> None:
        captured.append(HOT_PATH.get())
        return None

    # Patch _find_latest_run_dir to record the contextvar state if invoked.
    # If session_start triggers it (which would be a regression), captured
    # will be non-empty and the value tells us if HOT_PATH was set.
    from unittest.mock import patch

    with patch("trw_mcp.state._paths._find_latest_run_dir", side_effect=fake_scan):
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
            f"If the mtime scan is reached during session_start, HOT_PATH must be True. Got: {captured}"
        )

    # Sanity: the call returned a result dict.
    assert isinstance(result, dict)


def test_hot_path_reset_even_when_step_raises() -> None:
    """trw-mcp-2: HOT_PATH must reset even if an unguarded step raises.

    The reset lives in a try/finally so an unhandled raise between
    HOT_PATH.set(True) and the return cannot leak HOT_PATH=True into the
    surrounding context. Patch an early, individually-unguarded step
    (run_boot_audit) to raise and confirm the exception propagates while
    HOT_PATH is restored to its pre-call value.
    """
    from unittest.mock import patch

    from tests.conftest import extract_tool_fn, make_test_server

    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")

    assert HOT_PATH.get() is False

    with patch(
        "trw_mcp.telemetry.boot_audit.run_boot_audit",
        side_effect=RuntimeError("boom"),
    ):
        with pytest.raises(RuntimeError, match="boom"):
            fn(ctx=None, query="hot-path-raise-probe")

    assert HOT_PATH.get() is False, "HOT_PATH must be reset even when a session_start step raises"
