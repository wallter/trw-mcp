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


# ---------------------------------------------------------------------------
# PRD-CORE-141 audit follow-up — FR03 ctx flows through learning tools
# ---------------------------------------------------------------------------


def test_trw_recall_ctx_aware_no_scan_hijack(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit P1-01: trw_recall(ctx=fresh) must not scan-hijack another session.

    Seeds an on-disk active run owned by "other session" and asserts that a
    fresh ctx-scoped recall does NOT surface that run via build_recall_context's
    find_active_run() probe (used to load PRD knowledge IDs).
    """
    from tests.conftest import extract_tool_fn, make_test_server

    # Seed an "other session" active run with a knowledge_requirements.yaml
    # that would be picked up by a scan-hijack.
    other_run = _seed_active_run(
        isolated_project, "other-task", "20260101T000000Z-other0001"
    )
    kr_path = other_run / "meta" / "knowledge_requirements.yaml"
    kr_path.write_text(
        "learning_ids:\n  - L-SHOULD-NOT-LEAK\n",
        encoding="utf-8",
    )

    # Capture find_active_run calls. _recall_impl uses a local import, so we
    # patch the source module (trw_mcp.state._paths.find_active_run) and also
    # watch log events for ctx-aware suppression.
    captured_contexts: list[object | None] = []
    from trw_mcp.state import _paths as paths_mod

    original_find = paths_mod.find_active_run

    def _spy_find(*, context: object | None = None) -> Path | None:
        captured_contexts.append(context)
        return original_find(context=context)

    monkeypatch.setattr(paths_mod, "find_active_run", _spy_find)

    server = make_test_server("learning")
    trw_recall = extract_tool_fn(server, "trw_recall")

    fresh_ctx = _fresh_ctx("fresh-recall-session")
    result = trw_recall(ctx=fresh_ctx, query="anything")

    # The spy should have been called with a TRWCallContext for the fresh
    # session, NOT with context=None (which would trigger scan-hijack).
    assert captured_contexts, "build_recall_context must probe find_active_run"
    any_ctx_aware = any(c is not None for c in captured_contexts)
    assert any_ctx_aware, (
        "FR03: trw_recall must thread a TRWCallContext into find_active_run "
        f"(captured: {captured_contexts!r})"
    )

    # Sanity — no crash; result is dict-shaped.
    assert isinstance(result, dict)


def test_trw_learn_ctx_aware_telemetry_not_scan_hijacked(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit P1-01: trw_learn(ctx=fresh) telemetry routes via ctx, not scan."""
    from tests.conftest import extract_tool_fn, make_test_server

    # Seed other-session active run — the telemetry scan would pick this up.
    _seed_active_run(
        isolated_project, "other-task", "20260101T000000Z-other0001"
    )

    captured: list[object | None] = []

    # Patch _get_cached_run_dir to record the call_ctx it receives.
    from trw_mcp.tools import telemetry as tel_mod

    original = tel_mod._get_cached_run_dir

    def _spy(call_ctx: object | None = None) -> Path | None:
        captured.append(call_ctx)
        return original(call_ctx=call_ctx)

    monkeypatch.setattr(tel_mod, "_get_cached_run_dir", _spy)

    server = make_test_server("learning")
    trw_learn = extract_tool_fn(server, "trw_learn")

    fresh_ctx = _fresh_ctx("fresh-learn-session")
    try:
        trw_learn(
            ctx=fresh_ctx,
            summary="ctx parity",
            detail="covers FR03 telemetry routing for trw_learn",
        )
    except Exception:
        # trw_learn may raise on backend unavailability in isolated fixture;
        # we only care about the telemetry decorator's ctx propagation.
        pass

    assert captured, "telemetry decorator must invoke _get_cached_run_dir"
    assert any(c is not None for c in captured), (
        "FR03: log_tool_call decorator must pass a TRWCallContext when the "
        f"wrapped handler was given ctx (captured: {captured!r})"
    )


def test_log_tool_call_decorator_uses_ctx_when_available(
    isolated_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Audit P1-01: log_tool_call extracts ctx from kwargs and builds call_ctx."""
    from trw_mcp.tools import telemetry as tel_mod
    from trw_mcp.tools.telemetry import log_tool_call

    captured: list[object | None] = []

    def _spy(call_ctx: object | None = None) -> Path | None:
        captured.append(call_ctx)
        return None

    monkeypatch.setattr(tel_mod, "_get_cached_run_dir", _spy)

    @log_tool_call
    def _fake_tool(ctx: object | None = None, payload: str = "") -> str:
        return f"ok:{payload}"

    ctx = _fresh_ctx("decorator-test-session")
    _fake_tool(ctx=ctx, payload="x")

    assert captured, "_get_cached_run_dir should have been invoked"
    assert any(c is not None for c in captured), (
        "Decorator must build a TRWCallContext from the wrapped handler's "
        f"ctx kwarg (captured: {captured!r})"
    )


# ---------------------------------------------------------------------------
# FR12 — TRW_SESSION_ID env inheritance across subprocesses
# ---------------------------------------------------------------------------


def test_trw_session_id_subprocess_inheritance(
    isolated_project: Path,
) -> None:
    """FR12: a subprocess inherits TRW_SESSION_ID and resolves to the same pin-key."""
    import os
    import subprocess
    import sys

    script = (
        "from trw_mcp.state._paths import resolve_pin_key\n"
        "print(resolve_pin_key(ctx=None))\n"
    )
    env = {**os.environ, "TRW_SESSION_ID": "parent-pin-001"}
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "parent-pin-001" in result.stdout, (
        f"subprocess must resolve to inherited TRW_SESSION_ID; "
        f"got stdout={result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# P2-03 — Grace window boundary semantics (>=)
# ---------------------------------------------------------------------------


def test_sweep_preserves_run_at_exact_grace_boundary(
    isolated_project: Path,
) -> None:
    """Audit P2-03: run whose last_activity == grace_cutoff is PRESERVED."""
    import os
    import time
    from dataclasses import asdict, is_dataclass

    from trw_mcp.state._run_gc import sweep_stale_runs

    run_dir = _seed_active_run(
        isolated_project, "boundary-task", "20260101T000000Z-boundary01"
    )
    events_path = run_dir / "meta" / "events.jsonl"
    events_path.write_text("", encoding="utf-8")

    from trw_mcp.models.config import get_config

    staleness_hours = 24
    grace_hours = 48
    # Use a deterministic _now to avoid rounding races — set events.jsonl mtime
    # EXACTLY to grace_cutoff = now - (staleness + grace) * 3600.
    now = time.time()
    target_mtime = now - ((staleness_hours + grace_hours) * 3600)
    os.utime(events_path, (target_mtime, target_mtime))

    runs_root = isolated_project / get_config().runs_root
    report = sweep_stale_runs(
        runs_root=runs_root,
        staleness_hours=staleness_hours,
        grace_hours=grace_hours,
        pinned_paths=[],
        dry_run=True,
        _now=now,
    )
    if is_dataclass(report):
        report_d = asdict(report)
    elif hasattr(report, "model_dump"):
        report_d = report.model_dump()
    else:
        report_d = dict(report)

    abandoned = report_d.get("abandoned_run_ids") or []
    assert "20260101T000000Z-boundary01" not in abandoned, (
        f"Run at exact grace boundary must be preserved (>= semantics). "
        f"report={report_d!r}"
    )
