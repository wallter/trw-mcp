from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.tools.orchestration import register_orchestration_tools


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests in this split suite."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _make_orch_tools() -> dict[str, Any]:
    """Return a dict of orchestration tools keyed by name."""
    srv = FastMCP("test")
    register_orchestration_tools(srv)
    return get_tools_sync(srv)


@pytest.fixture
def orch_tools() -> dict[str, Any]:
    """Orchestration tools dict for test use."""
    return _make_orch_tools()
