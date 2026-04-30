"""Shared support for split build edge-path tests."""

from __future__ import annotations

import subprocess

import pytest

pytest.importorskip(
    "trw_mcp.tools.build._subprocess",
    reason="PRD-CORE-098: subprocess/audit modules removed — these tests are obsolete",
)

from tests.conftest import get_tools_sync


def _make_completed_process(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    """Build a CompletedProcess for mocked subprocess results."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _get_tool_fn(server: object) -> object:
    """Extract trw_build_check tool function from a FastMCP server."""
    tools = get_tools_sync(server)  # type: ignore[arg-type]
    if "trw_build_check" in tools:
        return tools["trw_build_check"].fn
    raise AssertionError("trw_build_check tool not found on server")
