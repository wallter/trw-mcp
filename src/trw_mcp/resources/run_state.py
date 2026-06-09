"""Run state resource — exposes current active run state via MCP."""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

logger = structlog.get_logger(__name__)

from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root


def register_run_state_resources(server: FastMCP) -> None:
    """Register run state resource on the MCP server.

    Args:
        server: FastMCP server instance to register resources on.
    """

    @server.resource("trw://run/state")
    def get_run_state() -> str:
        """Current run state (run.yaml) — phase, status, confidence, variables.

        Returns the contents of the most recently modified run.yaml
        if an active run is found. Empty string if no active run.
        """
        config = get_config()
        project_root = resolve_project_root()
        # Canonical run location: project_root / config.runs_root / {task} / {run_id} / meta / run.yaml
        # The glob pattern "**" covers both the nested (task/run_id) and flat (run_id-only) layouts.
        runs_root = project_root / config.runs_root

        if not runs_root.exists():
            return "No active run found (runs directory does not exist)"

        # Find most recently modified run.yaml across all task/run directories
        candidates = list(runs_root.glob("**/meta/run.yaml"))

        if not candidates:
            return "No active run found"

        return max(candidates, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8")
