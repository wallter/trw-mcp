"""Post-run analytics report tool — PRD-CORE-030.

Exposes trw_run_report as an MCP tool that generates structured
analytics from any run directory.
"""

from __future__ import annotations

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.state._paths import resolve_run_path, resolve_trw_dir
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.report import assemble_report

logger = structlog.get_logger()

_reader = FileStateReader()


def register_report_tools(server: FastMCP) -> None:
    """Register post-run analytics tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_run_report(run_path: str | None = None) -> dict[str, object]:
        """Generate a structured analytics report for a completed or active run.

        Reads run.yaml (required), events.jsonl, checkpoints.jsonl, and
        build-status.yaml to produce aggregated metrics including phase
        timing, event counts, learning yield, and build status.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
        """
        try:
            resolved_path = resolve_run_path(run_path)
        except StateError as exc:
            return {"error": str(exc), "status": "failed"}

        try:
            trw_dir = resolve_trw_dir()
        except Exception:
            trw_dir = resolved_path.parent.parent.parent / ".trw"

        try:
            report = assemble_report(resolved_path, _reader, trw_dir)
            logger.info("trw_run_report_generated", run_id=report.run_id)
            result: dict[str, object] = report.model_dump()
            return result
        except StateError as exc:
            return {"error": str(exc), "status": "failed"}
