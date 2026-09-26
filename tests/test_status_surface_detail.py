"""PRD-CORE-300 S11b: trw_status(detail="surface") and ``trw-mcp profile explain``.

The two replace the profile-explain MCP tool and call one service
(``profile.explain_surface``), so they must report the same tool surface. Both
work with no pinned run.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    from trw_mcp.models.config import _reset_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._paths import _pinned_runs

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    (tmp_path / ".trw").mkdir()
    (tmp_path / ".trw" / "config.yaml").write_text("assess_enabled: true\n", encoding="utf-8")
    _reset_config()
    _pinned_runs.clear()
    pin_store_mod.invalidate_pin_store_cache()
    yield tmp_path
    _reset_config()


def _status_surface() -> dict[str, Any]:
    """Call the registered ``trw_status`` tool, not a helper behind it."""
    from tests.conftest import extract_tool_fn, make_test_server

    result = extract_tool_fn(make_test_server("orchestration"), "trw_status")(detail="surface")
    return dict(result)


def _cli_surface(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    from trw_mcp.tools._profile_cli import run_profile

    args = argparse.Namespace(
        profile_command="explain", domain="", task_type="", prd_path="", task_name="", as_json=True
    )
    run_profile(args)
    return json.loads(capsys.readouterr().out)


def test_status_and_cli_report_the_same_surface_with_no_run(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trw_mcp.server._surface_manifest_registry import resolve_tool_surface

    status = _status_surface()
    cli = _cli_surface(capsys)
    assert "error" not in status, status
    assert status["tool_surface"] == cli["tool_surface"]
    expected = sorted(set(resolve_tool_surface(comms_enabled=True, assess_enabled=True).tools))
    assert status["tool_surface"]["tools"] == expected
    assert "trw_assess" in expected
    assert status["tool_surface"]["off"] == {"dispatch": "dispatch_tools_exposed"}
    assert status["fields"], "the profile explanation rides along"


def test_reviewer_role_reports_the_reviewer_bound(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.models.surface_packs import REVIEWER_TOOLS
    from trw_mcp.state._surface_role import reset_surface_role_state

    monkeypatch.setenv("TRW_SURFACE_ROLE", "reviewer")
    reset_surface_role_state()
    try:
        assert _status_surface()["tool_surface"]["tools"] == sorted(REVIEWER_TOOLS)
    finally:
        monkeypatch.delenv("TRW_SURFACE_ROLE")
        reset_surface_role_state()


def test_trw_status_advertises_the_detail_parameter() -> None:
    import asyncio

    from trw_mcp.server import mcp

    tools = {tool.name: tool for tool in asyncio.run(mcp._list_tools())}
    assert "detail" in tools["trw_status"].parameters["properties"]
