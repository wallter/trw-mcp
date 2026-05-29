"""Pure callable for updating the local SHA-256 code index."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from trw_mcp.code_index.update import update_code_index
from trw_mcp.tools.telemetry import log_tool_call


class CodeIndexUpdateToolResult(BaseModel):
    """Privacy-preserving response for ``trw_code_index_update``."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: Literal["ok", "failed"]
    manifest_path: str
    stats: dict[str, int]
    error: str = ""


def trw_code_index_update(
    repo_root: str,
    force: bool = False,
    paths: Iterable[str] | None = None,
) -> dict[str, object]:
    """Update the local code-index manifest and return stats only.

    The response intentionally omits file rows and file bodies; local
    integration can register this pure callable as an MCP tool later.
    """

    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        result = update_code_index(
            repo_root,
            force=force,
            paths=paths,
            max_file_bytes=config.code_index_max_file_bytes,
            exclude_dirs=frozenset(config.code_index_exclude_dirs),
            include_extensions=frozenset(config.code_index_include_extensions),
        )
        payload = CodeIndexUpdateToolResult(
            status="ok",
            manifest_path=str(result.manifest_path),
            stats={
                "total_files": result.stats.total_files,
                "added": result.stats.added,
                "unchanged": result.stats.unchanged,
                "modified": result.stats.modified,
                "deleted": result.stats.deleted,
                "skipped": result.stats.skipped,
            },
        )
    except (OSError, ValueError) as exc:
        payload = CodeIndexUpdateToolResult(status="failed", manifest_path="", stats={}, error=str(exc))
    return payload.model_dump()


def register_code_index_tools(server: FastMCP) -> None:
    """Register code-index MCP tools."""

    @server.tool(name="trw_code_index_update", output_schema=None)
    @log_tool_call
    def trw_code_index_update_tool(
        repo_root: str,
        force: bool = False,
        paths: list[str] | None = None,
    ) -> dict[str, object]:
        """Update the local SHA-256 code-index manifest.

        Use when an agent needs a fresh local code-index manifest before code
        search or symbol analysis without returning file bodies.

        Args:
            repo_root: Repository root to index.
            force: Reclassify all discovered files as freshly added.
            paths: Optional repo-relative file or directory limits.

        Returns:
            {"status": "ok", "manifest_path": str, "stats": {...}}
        """

        return trw_code_index_update(repo_root=repo_root, force=force, paths=paths)


__all__ = [
    "CodeIndexUpdateToolResult",
    "register_code_index_tools",
    "trw_code_index_update",
]
