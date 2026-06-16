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


def test_deliver_blocks_unpinned_session_without_build_check(tmp_path: Path) -> None:
    """No active run still requires local ceremony build evidence after session_start."""
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

    assert result["success"] is False
    assert "build_gate_warning" in result
    assert "unpinned session" in str(result["build_gate_warning"])
    assert "build_gate_block" in result


def test_deliver_allows_unpinned_session_with_build_check(tmp_path: Path) -> None:
    """No active run delivery is allowed when local ceremony state has a passing build check."""
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

    assert "build_gate_warning" not in result
    assert result["checkpoint"]["reason"] == "no_active_run"


def test_deliver_override_surfaces_truthfulness_gate_bypass(tmp_path: Path) -> None:
    """allow_unverified bypass of the build gate must be UNMISSABLE (A-P1-02).

    Pre-fix the bypass was only quietly stored in ``build_gate_override`` — an
    operator watching the log/event stream could not tell a legitimate
    acceptable-failure deliver from a one-word reason. The fix adds a prominent
    result key + a build_gate_override_used WARNING.
    """
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

    # Override allowed (no hard block) but surfaced prominently in the result...
    assert "build_gate_block" not in result
    assert result.get("truthfulness_gate_bypassed") == reason
    # ...and logged at WARNING for the operator log stream.
    overrides = [e for e in logs if e.get("event") == "build_gate_override_used"]
    assert overrides, f"expected a build_gate_override_used warning; got {logs}"
    assert overrides[0]["log_level"] == "warning"


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


def test_no_active_run_started_session_without_build_still_blocks(tmp_path: Path) -> None:
    """The fail-closed posture IS active for a STARTED session with no passing build

    — the gate's actual job. This is the second layer of the PATH-3 defense.
    """
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


def test_compaction_gate_blocks_trw_deliver_before_session_start() -> None:
    """Upstream layer: CeremonyMiddleware blocks trw_deliver with

    ``session_start_required`` when a post-compaction recovery marker is pending,
    so a deliver after a dropped session cannot reach the no-run path at all.
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
    with patch.object(cm, "_is_compaction_gate_required_for_session", return_value=True):
        result = asyncio.run(mw.on_call_tool(_MwCtx(), _call_next))  # type: ignore[arg-type]

    structured = getattr(result, "structured_content", {})
    assert structured.get("error") == "session_start_required"
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


def test_deliver_accepts_run_path_inside_project_root(tmp_path: Path) -> None:
    """A run_path under the project root passes the containment check (the
    block above is specific to traversal, not a blanket rejection)."""
    project = tmp_path / "project"
    trw_dir = project / ".trw"
    _write_ceremony_state(trw_dir, "passed")
    run_dir = project / ".trw" / "runs" / "task" / "run-1" / "meta"
    run_dir.mkdir(parents=True)
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
