"""Pure callable for updating the local SHA-256 code index."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from trw_mcp.code_index.bounds import IndexBoundExceeded
from trw_mcp.code_index.store import RuntimeUnsupported
from trw_mcp.code_index.update import update_code_index


class CodeIndexUpdateToolResult(BaseModel):
    """Privacy-preserving response for :func:`build_code_index`."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    status: Literal["ok", "failed"]
    manifest_path: str
    stats: dict[str, int]
    chunk_stats: dict[str, int] = {}
    error: str = ""
    error_code: Literal["", "index_bound_exceeded", "unsupported_runtime"] = ""
    #: The ``code_index_bounds`` key a failed build crossed (PRD-CORE-300-FR15).
    bound: str = ""


def build_code_index(
    repo_root: str,
    force: bool = False,
    paths: Iterable[str] | None = None,
) -> dict[str, object]:
    """Build the code-index manifest and chunk store; return stats only.

    This is the only writer of the index: search and symbol queries read the
    published store and never build it (PRD-CORE-300-FR15). The response omits
    file rows and file bodies. Called from ``trw-mcp code index`` (the CLI
    command PRD-CORE-300 slice S4 replaced the former MCP tool with).
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
            bounds=config.code_index_bounds,
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
            chunk_stats=result.chunk_stats.model_dump(),
        )
    except IndexBoundExceeded as exc:
        payload = CodeIndexUpdateToolResult(
            status="failed",
            manifest_path="",
            stats={},
            error=str(exc),
            error_code="index_bound_exceeded",
            bound=exc.bound,
        )
    except RuntimeUnsupported as exc:
        payload = CodeIndexUpdateToolResult(
            status="failed", manifest_path="", stats={}, error=str(exc), error_code="unsupported_runtime"
        )
    except (OSError, ValueError) as exc:
        payload = CodeIndexUpdateToolResult(status="failed", manifest_path="", stats={}, error=str(exc))
    return payload.model_dump()


__all__ = [
    "CodeIndexUpdateToolResult",
    "build_code_index",
]
