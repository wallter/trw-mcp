"""MEAS-001 FR-12 run-isolation tests for persisted projections."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.models.config import _reset_config
from trw_mcp.state._paths import pin_active_run, unpin_active_run
from trw_mcp.telemetry.tool_call_timing import clear_pricing_cache


def _get_production_tool_fn(tool_name: str) -> Any:
    import trw_mcp.server._tools  # noqa: F401
    from trw_mcp.server._app import mcp

    components = getattr(getattr(mcp, "_local_provider"), "_components", {})
    for key, component in components.items():
        if key.startswith(f"tool:{tool_name}@"):
            fn = getattr(component, "fn", None) or getattr(component, "func", None)
            if callable(fn):
                return fn
    pytest.fail(f"Production MCP tool {tool_name!r} not found.")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def meas_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Path, Path, Path]]:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    run_one = trw_dir / "runs" / "task-a" / "run-a"
    run_two = trw_dir / "runs" / "task-b" / "run-b"
    for session_id, snapshot_id, run_dir in (
        ("sess-a", "snap-a", run_one),
        ("sess-b", "snap-b", run_two),
    ):
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "run.yaml").write_text(
            "\n".join(
                (
                    f"run_id: {run_dir.name}",
                    "status: active",
                    "phase: implement",
                    f"task: {run_dir.parent.name}",
                    f"owner_session_id: {session_id}",
                    f"surface_snapshot_id: {snapshot_id}",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (meta_dir / "run_surface_snapshot.yaml").write_text(
            f"snapshot_id: {snapshot_id}\nartifacts: []\n",
            encoding="utf-8",
        )

    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.build._registration.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools._ceremony_helpers.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._find_active_run_compat",
        lambda _ctx: run_one if os.environ.get("TRW_SESSION_ID") == "sess-a" else run_two,
    )

    try:
        yield trw_dir, run_one, run_two
    finally:
        unpin_active_run(session_id="sess-a")
        unpin_active_run(session_id="sess-b")
        _reset_config(None)
        clear_pricing_cache()


def test_tool_call_events_run_id_isolation(
    meas_workspace: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each projection row must carry the run_id of the run that emitted it."""
    _, run_one, run_two = meas_workspace
    tool_fn = _get_production_tool_fn("trw_build_check")

    for session_id, run_dir in (("sess-a", run_one), ("sess-b", run_two)):
        monkeypatch.setenv("TRW_SESSION_ID", session_id)
        pin_active_run(run_dir, session_id=session_id)
        for _ in range(5):
            tool_fn(
                tests_passed=True,
                test_count=1,
                coverage_pct=100.0,
                mypy_clean=True,
                run_path=str(run_dir),
            )

    proj_one = _read_jsonl(run_one / "meta" / "tool_call_events.jsonl")
    proj_two = _read_jsonl(run_two / "meta" / "tool_call_events.jsonl")

    assert len(proj_one) == 5
    assert len(proj_two) == 5
    assert {row["run_id"] for row in proj_one} == {"run-a"}
    assert {row["run_id"] for row in proj_two} == {"run-b"}


def test_artifact_registry_run_id_isolation(
    meas_workspace: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trw_session_start must isolate artifact-registry rows by run_id."""
    _, run_one, run_two = meas_workspace
    tool_fn = _get_production_tool_fn("trw_session_start")
    monkeypatch.setattr("trw_mcp.telemetry.boot_audit.run_boot_audit", lambda **_: [])

    for session_id, run_dir in (("sess-a", run_one), ("sess-b", run_two)):
        monkeypatch.setenv("TRW_SESSION_ID", session_id)
        pin_active_run(run_dir, session_id=session_id)
        result = tool_fn()
        assert result["success"] is True

    rows_one = _read_jsonl(run_one / "meta" / "artifact_registry.jsonl")
    rows_two = _read_jsonl(run_two / "meta" / "artifact_registry.jsonl")

    assert rows_one
    assert rows_two
    assert {row["run_id"] for row in rows_one} == {"run-a"}
    assert {row["run_id"] for row in rows_two} == {"run-b"}
