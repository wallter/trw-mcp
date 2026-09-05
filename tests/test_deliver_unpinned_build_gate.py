"""Unpinned-session build gate tests for trw_deliver."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.tools.ceremony import register_ceremony_tools


def _make_deliver_fn() -> Callable[..., dict[str, Any]]:
    server = FastMCP("test")
    register_ceremony_tools(server)
    return get_tools_sync(server)["trw_deliver"].fn


def _write_ceremony_state(trw_dir: Path, build_check_result: object) -> None:
    context = trw_dir / "context"
    context.mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "reflections").mkdir(parents=True)
    (context / "ceremony-state.json").write_text(
        json.dumps(
            {
                "session_started": True,
                "build_check_result": build_check_result,
                "deliver_called": False,
            }
        ),
        encoding="utf-8",
    )


def test_deliver_surfaces_unpinned_missing_build_as_advisory(tmp_path: Path) -> None:
    """Unknown/unpinned work keeps the configured advisory posture end to end."""
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, None)
    deliver_fn = _make_deliver_fn()

    with (
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
    ):
        result = deliver_fn()

    assert result["success"] is True
    assert "build_gate_warning" in result
    assert "unpinned session" in str(result["build_gate_warning"])
    assert "build_gate_block" not in result


def test_unpinned_deliver_writes_session_events_deliver_marker(tmp_path: Path) -> None:
    """An UNPINNED trw_deliver must leave a session-scoped completion marker.

    Regression: without a run dir, ``_log_deliver_event`` had no events.jsonl to
    write ``trw_deliver_complete`` to, so the Stop hook (which attributes a
    foreign run to the unpinned session) never saw the deliver succeed and nagged
    falsely. The marker now lands in ``.trw/context/session-events.jsonl`` on
    every deliver, carrying ``session_id`` + an ISO ``ts`` the hook can bound.
    """
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, "passed")
    deliver_fn = _make_deliver_fn()

    with (
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
    ):
        result = deliver_fn(skip_reflect=True)

    assert result["success"] is True
    session_events = trw_dir / "context" / "session-events.jsonl"
    assert session_events.exists(), "unpinned deliver must write session-events.jsonl"
    markers = [
        json.loads(line)
        for line in session_events.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == "trw_deliver_complete"
    ]
    assert markers, "session-events must record a trw_deliver_complete marker"
    marker = markers[-1]
    assert "session_id" in marker, "marker must carry session_id for attribution"
    assert marker.get("ts"), "marker must carry an ISO timestamp for recency bounding"


def test_deliver_does_not_inherit_unbound_global_build_check(tmp_path: Path) -> None:
    """A session-aware delivery cannot inherit legacy global build evidence."""
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, "passed")
    deliver_fn = _make_deliver_fn()

    with (
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
    ):
        result = deliver_fn(skip_reflect=True)

    assert "build_gate_warning" in result
    assert result["checkpoint"]["reason"] == "no_active_run"


def test_unpinned_advisory_does_not_accept_or_record_free_text_override(tmp_path: Path) -> None:
    """Advisory warnings need no override and must not bless free-text prose."""
    import structlog

    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, None)  # no passing build -> build_gate_warning fires
    deliver_fn = _make_deliver_fn()
    reason = "acceptable failure: known-flaky integration test, tracked in ISSUE-123"

    with (
        patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
        structlog.testing.capture_logs() as logs,
    ):
        result = deliver_fn(allow_unverified=True, unverified_reason=reason, skip_reflect=True)

    # Delivery proceeds because the warning is advisory, not because prose
    # bypassed a gate. The unused override arguments create no audit record.
    assert "build_gate_block" not in result
    assert result.get("truthfulness_gate_bypassed") is None
    assert result.get("acceptable_failure_record") is None
    overrides = [e for e in logs if e.get("event") == "build_gate_override_used"]
    assert not overrides


# ── codex cross-model review #3: PATH-3 no-run/no-ceremony-state, layered defense ──


def test_no_active_run_no_ceremony_state_is_by_design_advisory(tmp_path: Path) -> None:
    """codex cross-model review PATH-3 (DOCUMENTED): with NO run pin AND NO

    ceremony-state.json, the no-active-run build gate returns None (no block).
    This is by-design, not an oversight — a delivery with no run.yaml has
    task_type=unknown, which the deliver_gate_mode taxonomy intentionally treats
    as advisory (blocking it would over-block legitimate brand-new-project /
    quick-task delivery). Upstream, the compaction-gate middleware blocks a
    deliver after a dropped/compacted session (pinned separately below).
    """
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._delivery_build_gates import _check_no_active_run_build_gate

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)  # context dir exists but NO ceremony-state.json

    result = _check_no_active_run_build_gate(trw_dir, FileStateReader())
    assert result is None, "no ceremony state -> no gate (by-design; layered defense)"


def test_no_active_run_started_session_without_build_still_warns(tmp_path: Path) -> None:
    """Started unpinned sessions still surface their missing-build warning."""
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._delivery_build_gates import _check_no_active_run_build_gate

    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "context" / "ceremony-state.json").write_text(
        json.dumps({"session_started": True, "build_check_result": None}), encoding="utf-8"
    )

    result = _check_no_active_run_build_gate(trw_dir, FileStateReader())
    assert result is not None
    assert "unpinned session" in result


def test_new_session_cannot_inherit_previous_build_pass(tmp_path: Path) -> None:
    from trw_mcp.state._ceremony_progress_state import (
        mark_build_check,
        mark_deliver,
        mark_session_started,
        read_ceremony_state,
    )
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._delivery_build_gates import _check_no_active_run_build_gate

    trw_dir = tmp_path / ".trw"
    mark_session_started(trw_dir, session_id="session-one")
    mark_build_check(trw_dir, passed=True, session_id="session-one")
    mark_deliver(trw_dir)

    mark_session_started(trw_dir, session_id="session-two")

    state = read_ceremony_state(trw_dir)
    assert state.session_build_results == {"session-one": "passed", "session-two": "pending"}
    assert _check_no_active_run_build_gate(trw_dir, FileStateReader(), session_id="session-two") is not None


def test_same_session_start_preserves_compaction_progress(tmp_path: Path) -> None:
    from trw_mcp.state._ceremony_progress_state import mark_build_check, mark_session_started, read_ceremony_state

    trw_dir = tmp_path / ".trw"
    mark_session_started(trw_dir, session_id="session-one")
    mark_build_check(trw_dir, passed=True, session_id="session-one")

    mark_session_started(trw_dir, session_id="session-one")

    assert read_ceremony_state(trw_dir).session_build_results["session-one"] == "passed"


def test_recently_active_session_survives_session_result_pruning(tmp_path: Path) -> None:
    from trw_mcp.state._ceremony_progress_state import mark_build_check, mark_session_started, read_ceremony_state

    trw_dir = tmp_path / ".trw"
    mark_session_started(trw_dir, session_id="long-lived")
    for index in range(2048):
        mark_session_started(trw_dir, session_id=f"session-{index}")

    mark_build_check(trw_dir, passed=True, session_id="long-lived")
    mark_session_started(trw_dir, session_id="new-session")

    state = read_ceremony_state(trw_dir)
    assert state.session_build_results["long-lived"] == "passed"
    assert "session-0" not in state.session_build_results


def test_interleaved_sessions_cannot_share_build_pass(tmp_path: Path) -> None:
    from trw_mcp.state._ceremony_progress_state import mark_build_check, mark_session_started
    from trw_mcp.state.persistence import FileStateReader
    from trw_mcp.tools._delivery_build_gates import _check_no_active_run_build_gate

    trw_dir = tmp_path / ".trw"
    mark_session_started(trw_dir, session_id="session-a")
    mark_session_started(trw_dir, session_id="session-b")
    mark_build_check(trw_dir, passed=True, session_id="session-a")

    assert _check_no_active_run_build_gate(trw_dir, FileStateReader(), session_id="session-a") is None
    assert _check_no_active_run_build_gate(trw_dir, FileStateReader(), session_id="session-b") is not None


def test_compaction_gate_blocks_trw_deliver_before_session_start(tmp_path: Path) -> None:
    """Upstream layer: CeremonyMiddleware blocks trw_deliver with

    ``post_compaction_recovery_required`` when a post-compaction recovery marker is pending,
    so a deliver after a dropped session cannot reach the no-run path at all.

    This case forces the gate on a session with a ZERO block count, so it pins
    only the pre-bound half. The post-bound half — where the bounded escape used
    to execute ``trw_deliver`` outright — is pinned by
    ``test_trw_deliver_is_still_blocked_after_the_bound_is_exhausted`` below.
    """
    import asyncio

    from mcp.types import TextContent

    from trw_mcp.middleware import ceremony as cm

    cm.reset_state()

    class _Msg:
        name = "trw_deliver"

    class _ReqCtx:
        pass

    class _Ctx:
        session_id = "sess-1"
        request_context = _ReqCtx()

    class _MwCtx:
        message = _Msg()
        fastmcp_context = _Ctx()

    async def _call_next(_ctx: object) -> object:  # pragma: no cover - must NOT run
        raise AssertionError("trw_deliver should be blocked before execution")

    mw = cm.CeremonyMiddleware()
    # Force the compaction gate pending for this session.
    with (
        patch.object(cm, "_is_compaction_gate_required_for_session", return_value=True),
        # Review P1-1: the blocked payload reads the marker; keep it off the live .trw.
        patch("trw_mcp.state._paths.resolve_trw_dir", return_value=tmp_path),
    ):
        result = asyncio.run(mw.on_call_tool(_MwCtx(), _call_next))  # type: ignore[arg-type]

    structured = getattr(result, "structured_content", {})
    assert structured.get("error") == "post_compaction_recovery_required"
    assert structured.get("tool_attempted") == "trw_deliver"
    content = getattr(result, "content", [])
    assert content and isinstance(content[0], TextContent)


def test_deliver_rejects_run_path_outside_project_root(tmp_path: Path) -> None:
    """PRD-QUAL-042-FR02: an explicit run_path that resolves OUTSIDE the project
    root is a path-traversal attempt — deliver must block, not operate on it."""
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, "passed")
    # A directory entirely outside the project root.
    outside = tmp_path / "outside-target"
    outside.mkdir()
    deliver_fn = _make_deliver_fn()

    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
    ):
        result = deliver_fn(run_path=str(outside), skip_reflect=True)

    assert result["success"] is False, "traversal run_path must block delivery"
    assert result.get("run_path") is None
    assert "escapes project root" in str(result.get("delivery_blocked", ""))


def test_deliver_accepts_valid_run_path_inside_configured_runs_root(tmp_path: Path) -> None:
    """A canonical run with persistent identity passes path validation."""
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, "passed")
    run_dir = project / ".trw" / "runs" / "task" / "run-1" / "meta"
    run_dir.mkdir(parents=True)
    (run_dir / "run.yaml").write_text("run_id: run-1\n", encoding="utf-8")
    deliver_fn = _make_deliver_fn()

    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
    ):
        result = deliver_fn(run_path=str(run_dir.parent), skip_reflect=True)

    # Not blocked for traversal: run_path is retained (delivery may still warn
    # for other reasons, but the containment check did not fire).
    assert "escapes project root" not in str(result.get("delivery_blocked", ""))
    assert result.get("run_path") == str(run_dir.parent.resolve())


def test_deliver_rejects_inside_project_non_run_without_writing_checkpoint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, "passed")
    non_run = project / "src" / "package"
    non_run.mkdir(parents=True)
    deliver_fn = _make_deliver_fn()

    with (
        patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
        patch("trw_mcp.state._paths.resolve_project_root", return_value=project),
    ):
        result = deliver_fn(run_path=str(non_run), skip_reflect=True)

    assert result["success"] is False
    assert "not a valid TRW run directory" in str(result["delivery_blocked"])
    assert not (non_run / "meta").exists()


def test_trw_deliver_is_still_blocked_after_the_bound_is_exhausted(tmp_path: Path) -> None:
    """PRD-CORE-258-FR09: a terminal act never rides the bounded escape.

    The escape exists for a caller that CANNOT clear the gate. That is
    measurably not the caller who delivers: of eleven bundled agents exactly one
    names ``trw_session_start``, and it is the same and only one that names
    ``trw_deliver``. Before this, ``_delivery_build_gates.py``'s
    defence-in-depth comment — "so a deliver after a compacted session cannot
    reach here" — was false for every session past the bound.
    """
    import asyncio

    from mcp.types import TextContent

    from trw_mcp.middleware import ceremony as cm

    cm.reset_state()
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "context" / "pre_compact_state.json").write_text(
        json.dumps({"timestamp": "2026-09-04T21:49:47+00:00", "trigger": "mcp_tool"}), encoding="utf-8"
    )

    class _Ctx:
        session_id = "sess-exhausted"
        request_context = object()

    def _mw_ctx(tool_name: str) -> Any:
        message = type("_Msg", (), {"name": tool_name})()
        return type("_MwCtx", (), {"message": message, "fastmcp_context": _Ctx()})()

    executed: list[str] = []

    async def _call_next(ctx: Any) -> Any:
        executed.append(ctx.message.name)
        return type("_Result", (), {"content": [TextContent(type="text", text="ok")], "structured_content": None})()

    mw = cm.CeremonyMiddleware()
    with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
        # Spend the budget, then prove the two tools diverge in the SAME session.
        for _ in range(cm._COMPACTION_GATE_MAX_BLOCKS):
            asyncio.run(mw.on_call_tool(_mw_ctx("trw_recall"), _call_next))  # type: ignore[arg-type]
        degraded = asyncio.run(mw.on_call_tool(_mw_ctx("trw_recall"), _call_next))  # type: ignore[arg-type]
        blocked = asyncio.run(mw.on_call_tool(_mw_ctx("trw_deliver"), _call_next))  # type: ignore[arg-type]

    assert executed == ["trw_recall"], "trw_deliver must never reach call_next past the bound"
    assert degraded.structured_content is None, "a non-terminal tool still degrades to advisory"
    assert "trw_session_start" in degraded.content[0].text, "the nudge is never removed"

    assert blocked.structured_content is not None
    assert blocked.structured_content["error"] == "post_compaction_recovery_required"
    assert blocked.structured_content["tool_attempted"] == "trw_deliver"
    assert blocked.structured_content["blocked_count"] > cm._COMPACTION_GATE_MAX_BLOCKS
    # The obligation survives: the marker is still on disk for a caller that can
    # discharge it.
    assert (trw_dir / "context" / "pre_compact_state.json").exists()


def test_terminal_tools_holds_only_the_terminal_act() -> None:
    """OQ-04: ``trw_init`` is NOT terminal — it creates the run evidence lands in."""
    from trw_mcp.middleware import ceremony as cm

    assert cm.TERMINAL_TOOLS == frozenset({"trw_deliver"})
    assert "trw_init" not in cm.TERMINAL_TOOLS
