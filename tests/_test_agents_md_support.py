"""Shared helpers for split AGENTS.md tests."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastmcp import FastMCP

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.claude_md import TRW_MARKER_END, TRW_MARKER_START
from trw_mcp.tools.learning import register_learning_tools

_TRW_SECTION = f"\n{TRW_MARKER_START}\n## TRW Section\n- test\n{TRW_MARKER_END}\n"


def _extract_trw_section(content: str) -> str:
    """Extract the TRW marker-delimited section from file content."""
    start = content.index(TRW_MARKER_START)
    end = content.index(TRW_MARKER_END) + len(TRW_MARKER_END)
    return content[start:end]


@contextmanager
def _patched_learning_env(
    project_root: Path,
    *,
    agents_md_enabled: bool = True,
) -> Generator[dict[str, Any], None, None]:
    """Patch learning tool dependencies and yield a tool-name-to-tool map."""
    with (
        patch("trw_mcp.tools.learning.resolve_trw_dir", return_value=project_root / ".trw"),
        patch(
            "trw_mcp.tools.learning.get_config",
            return_value=TRWConfig(agents_md_enabled=agents_md_enabled),
        ),
        patch("trw_mcp.state.claude_md._static_sections.get_config", return_value=TRWConfig()),
        patch("trw_mcp.state.claude_md.resolve_project_root", return_value=project_root),
        patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=project_root / ".trw"),
    ):
        server = FastMCP("test")
        register_learning_tools(server)
        yield get_tools_sync(server)
