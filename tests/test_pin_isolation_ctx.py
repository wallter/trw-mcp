"""PRD-CORE-141 Wave 3 — ctx-aware pin isolation + scan suppression.

Covers FR03 (ctx injection flows through tool handlers), FR05 (scan
fallback suppressed for ctx-aware callers), FR06 (trw_session_start
surfaces structured hint + null run on fresh ctx sessions), and FR15
(stdio / no-ctx callers still get the legacy scan fallback).

The new test file (rather than extending test_pin_isolation.py) keeps
the Wave 1/2 baseline file under 800 lines per the project's module
size gate and scopes Wave 3 regression coverage to one place.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from structlog.testing import capture_logs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_active_run(
    project_root: Path,
    task: str,
    run_id: str,
    status: str = "active",
) -> Path:
    """Create runs_root/{task}/{run_id}/meta/run.yaml with the given status.

    Uses the configured ``runs_root`` (``.trw/runs`` by default).
    """
    from trw_mcp.models.config import get_config

    runs_root = project_root / get_config().runs_root
    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text(
        f"""run_id: {run_id}
task: {task}
framework: v24.5_TRW
status: {status}
phase: implement
""",
        encoding="utf-8",
    )
    return run_dir


def _fresh_ctx(session_id: str) -> Any:
    """Construct a minimal ctx-shaped namespace that resolve_pin_key probes."""
    return SimpleNamespace(session_id=session_id)


@pytest.fixture
def isolated_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point TRW_PROJECT_ROOT and config at a scratch dir; clean pin store."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    # Ensure pin store lives under tmp_path
    from trw_mcp.models.config import _reset_config

    _reset_config()
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    # Create the runs_root and trw_dir so StateError("not found") doesn't fire.
    from trw_mcp.models.config import get_config

    config = get_config()
    (tmp_path / config.runs_root).mkdir(parents=True, exist_ok=True)
    (tmp_path / config.trw_dir).mkdir(parents=True, exist_ok=True)
    # Ensure docs/task/ exists for trw_init which expects task_root
    (tmp_path / "docs" / "task").mkdir(parents=True, exist_ok=True)
    return tmp_path


# ---------------------------------------------------------------------------
# FR05 — ctx-aware scan suppression
# ---------------------------------------------------------------------------


def test_ctx_aware_no_pin_returns_none_not_scan_match(
    isolated_project: Path,
) -> None:
    """FR05: ctx-aware find_active_run does NOT fall through to mtime scan."""
    from trw_mcp.state._paths import TRWCallContext, find_active_run

    # Seed three active runs from "other sessions" on disk — the legacy
    # scan would pick one of these.
    _seed_active_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    _seed_active_run(isolated_project, "task-b", "20260102T000000Z-bbbb2222")
    _seed_active_run(isolated_project, "task-c", "20260103T000000Z-cccc3333")

    fresh = TRWCallContext(
        session_id="fresh-session-no-pin",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )

    with capture_logs() as logs:
        result = find_active_run(context=fresh)

    assert result is None, (
        f"Ctx-aware caller with no pin must return None; got {result!r}"
    )

    events = [e for e in logs if e.get("event") == "run_resolution_no_pin_scan_suppressed"]
    assert events, (
        "FR05 requires a run_resolution_no_pin_scan_suppressed event on "
        f"the no-pin path; logs were {logs!r}"
    )
    assert any(e.get("pin_key") == "fresh-session-no-pin" for e in events)


def test_stdio_no_ctx_falls_back_to_scan(
    isolated_project: Path,
) -> None:
    """FR15: legacy callers (no context arg) retain the scan fallback."""
    from trw_mcp.state._paths import find_active_run

    _seed_active_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    latest = _seed_active_run(isolated_project, "task-b", "20260102T000000Z-bbbb2222")

    # No context, no session_id — mirror the stdio-per-instance path.
    result = find_active_run()
    assert result == latest, (
        "Legacy callers must still scan and return the latest active run "
        f"(expected {latest}, got {result})"
    )


def test_resolve_run_path_ctx_aware_raises_without_pin(
    isolated_project: Path,
) -> None:
    """FR05: resolve_run_path raises StateError on ctx-aware no-pin."""
    from trw_mcp.exceptions import StateError
    from trw_mcp.state._paths import TRWCallContext, resolve_run_path

    _seed_active_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")

    fresh = TRWCallContext(
        session_id="another-fresh-session",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )

    with pytest.raises(StateError) as exc_info:
        resolve_run_path(context=fresh)
    # The error should mention scan suppression; the pin_key context hint
    # is carried as a structured attribute.
    assert "scan fallback suppressed" in str(exc_info.value).lower() or exc_info.value.context.get("pin_key") == "another-fresh-session"


def test_concurrent_ctx_clients_isolated(
    isolated_project: Path,
) -> None:
    """Two distinct ctxs pin distinct runs; find_active_run stays isolated."""
    from trw_mcp.state._paths import (
        TRWCallContext,
        find_active_run,
        pin_active_run,
        unpin_active_run,
    )

    run_a = _seed_active_run(isolated_project, "task-a", "20260101T000000Z-aaaa1111")
    run_b = _seed_active_run(isolated_project, "task-b", "20260102T000000Z-bbbb2222")

    ctx1 = TRWCallContext(
        session_id="client-one",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )
    ctx2 = TRWCallContext(
        session_id="client-two",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )

    pin_active_run(run_a, context=ctx1)
    pin_active_run(run_b, context=ctx2)

    try:
        assert find_active_run(context=ctx1) == run_a.resolve()
        assert find_active_run(context=ctx2) == run_b.resolve()
    finally:
        unpin_active_run(context=ctx1)
        unpin_active_run(context=ctx2)


# ---------------------------------------------------------------------------
# FR06 — trw_session_start surfaces no-pin state with hint
# ---------------------------------------------------------------------------


def test_fresh_session_start_no_hijack_returns_null_run_with_hint(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR06: fresh ctx gets run=None + actionable hint, never a scan hijack."""
    from tests.conftest import extract_tool_fn, make_test_server

    # Seed a sprint-92-style active run on disk owned by another session.
    _seed_active_run(
        isolated_project, "sprint-92", "20260101T000000Z-aaaa1111"
    )

    # Silence heavy session_start substeps we don't care about.
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony.perform_session_recalls",
        lambda *_a, **_kw: ([], False, {}),
        raising=False,
    )
    monkeypatch.setattr(
        "trw_mcp.tools._ceremony_helpers.perform_session_recalls",
        lambda *_a, **_kw: ([], False, {}),
    )

    server = make_test_server("ceremony")
    session_start = extract_tool_fn(server, "trw_session_start")

    fresh_ctx = SimpleNamespace(session_id="fresh-gemini-scenario")
    result = session_start(ctx=fresh_ctx, query="")

    # Run field reflects the no-pin state (NOT the on-disk run).
    run_dict = result.get("run") or {}
    assert run_dict.get("active_run") is None, (
        "Fresh ctx session must NOT hijack another session's on-disk active "
        f"run via scan fallback; got {run_dict!r}"
    )
    assert run_dict.get("status") == "no_active_run"

    hint = result.get("hint", "")
    assert hint and "trw_init" in hint, (
        f"Expected FR06 hint pointing at trw_init; got {hint!r}"
    )
    assert "run_path" in hint


# ---------------------------------------------------------------------------
# FR03 — ctx flows through tool handlers (trw_init + trw_status isolation)
# ---------------------------------------------------------------------------


def test_two_clients_trw_init_then_status_no_cross_read(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ctxs each create/query their own run via the tool layer."""
    from tests.conftest import extract_tool_fn, make_test_server

    # Ensure task_root dirs exist for trw_init
    (isolated_project / "docs" / "task").mkdir(parents=True, exist_ok=True)

    server = make_test_server("orchestration")
    trw_init = extract_tool_fn(server, "trw_init")
    trw_status = extract_tool_fn(server, "trw_status")

    ctx1 = SimpleNamespace(session_id="client-1-session")
    ctx2 = SimpleNamespace(session_id="client-2-session")

    init1 = trw_init(ctx=ctx1, task_name="alpha-task", objective="")
    init2 = trw_init(ctx=ctx2, task_name="beta-task", objective="")

    assert init1["run_path"] != init2["run_path"], (
        "Distinct ctxs should produce distinct runs"
    )
    # Each ctx resolves to its own run via trw_status.
    status1 = trw_status(ctx=ctx1)
    status2 = trw_status(ctx=ctx2)

    # The run_ids come from trw_init's result payload.
    assert status1["run_id"] == init1["run_id"]
    assert status2["run_id"] == init2["run_id"]
    assert status1["run_id"] != status2["run_id"]
