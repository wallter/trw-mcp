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
