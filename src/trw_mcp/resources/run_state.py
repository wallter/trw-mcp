"""Run state resource — exposes current active run state via MCP."""

from __future__ import annotations

from pathlib import Path

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

        # Concurrent runs can delete/rotate run.yaml between the glob above and
        # the stat/read below. A bare ``p.stat()`` inside the key or the final
        # ``read_text()`` would raise OSError and crash the MCP resource (it is
        # surfaced to the client, not caught upstream). Guard the stat in the
        # key (treat a vanished file as oldest) and fail open on read errors.
        # PRD-FIX: also cap the read so a runaway run.yaml can't blow the
        # resource response budget.
        _MAX_RUN_STATE_BYTES = 1_000_000  # 1 MB — run.yaml is normally < 10 KB

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except (OSError, PermissionError):
                return -1.0

        newest = max(candidates, key=_safe_mtime)
        try:
            size = newest.stat().st_size
            if size > _MAX_RUN_STATE_BYTES:
                logger.warning(
                    "run_state_oversized",
                    run_state_path=str(newest),
                    size_bytes=size,
                    cap_bytes=_MAX_RUN_STATE_BYTES,
                )
                with newest.open("r", encoding="utf-8") as handle:
                    return handle.read(_MAX_RUN_STATE_BYTES)
            return newest.read_text(encoding="utf-8")
        except (OSError, PermissionError):
            # The chosen run.yaml was deleted/became unreadable in the deletion
            # race. Fail open rather than crash the resource.
            logger.info("run_state_read_race", run_state_path=str(newest), exc_info=True)
            return "No active run found"
