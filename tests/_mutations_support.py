"""Shared support for split mutation/build extension tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip(
    "trw_mcp.tools.mutations",
    reason="PRD-CORE-098: mutations module removed — these tests are obsolete",
)

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for use in mock return values."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _setup_build_tool_mocks(
    mock_get_config: MagicMock,
    tmp_path: Path,
    **overrides: object,
) -> tuple[Path, Path]:
    """Configure mock get_config to return a TRWConfig with build_check_enabled."""
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    (trw_dir / "context").mkdir()
    config_kwargs: dict[str, object] = {
        "build_check_enabled": True,
        "build_check_timeout_secs": 300,
        "build_check_pytest_args": "",
        "build_check_mypy_args": "--strict",
        "dep_audit_enabled": False,
    }
    config_kwargs.update(overrides)
    mock_get_config.return_value = TRWConfig(**config_kwargs)  # type: ignore[arg-type]
    return trw_dir, tmp_path


def _get_tool_fn(server: object) -> object:
    """Extract trw_build_check tool function from a FastMCP server."""
    tools = get_tools_sync(server)  # type: ignore[arg-type]
    if "trw_build_check" in tools:
        return tools["trw_build_check"].fn
    raise AssertionError("trw_build_check tool not found on server")
