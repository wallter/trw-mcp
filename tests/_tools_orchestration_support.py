"""Shared support for split orchestration tool tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import TRWConfig

FRAMEWORK_VERSION = TRWConfig().framework_version


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    return tmp_path


def _make_orch_tools() -> dict[str, Any]:
    """Create a FastMCP server with orchestration tools and return the tools dict."""
    return get_tools_sync(make_test_server("orchestration"))


@pytest.fixture
def orch_tools() -> dict[str, Any]:
    """Provide orchestration tools dict for tests that only need orch tools."""
    return _make_orch_tools()
