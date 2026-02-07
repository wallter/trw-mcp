"""Run state resource — exposes current active run state via MCP."""

from __future__ import annotations

import os
from pathlib import Path

from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader

_config = TRWConfig()
_reader = FileStateReader()


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
        env_root = os.environ.get("TRW_PROJECT_ROOT")
        project_root = Path(env_root).resolve() if env_root else Path.cwd().resolve()
        docs_dir = project_root / "docs"

        if not docs_dir.exists():
            return "No active run found (docs/ directory does not exist)"

        # Find most recent run.yaml
        latest_run_yaml: Path | None = None
        latest_time: float = 0.0

        for task_dir in docs_dir.iterdir():
            if not task_dir.is_dir():
                continue
            runs_dir = task_dir / "runs"
            if not runs_dir.exists():
                continue
            for run_dir in runs_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                run_yaml = run_dir / "meta" / "run.yaml"
                if run_yaml.exists():
                    mtime = run_yaml.stat().st_mtime
                    if mtime > latest_time:
                        latest_time = mtime
                        latest_run_yaml = run_yaml

        if latest_run_yaml is None:
            return "No active run found"

        return latest_run_yaml.read_text(encoding="utf-8")
