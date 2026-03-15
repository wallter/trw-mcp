"""Run state resource — exposes current active run state via MCP."""

from __future__ import annotations

from fastmcp import FastMCP

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
        project_root = resolve_project_root()
        docs_dir = project_root / "docs"

        if not docs_dir.exists():
            return "No active run found (docs/ directory does not exist)"

        # Find most recently modified run.yaml across all task/run directories
        candidates = list(docs_dir.glob("*/runs/*/meta/run.yaml"))

        if not candidates:
            return "No active run found"

        return max(candidates, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8")
