"""``trw_code``: code search, symbol lookup and before-edit hints in one tool (PRD-CORE-300-FR12).

Three modes over two engines:

* ``search`` / ``symbol`` read the local code index that ``trw-mcp code index``
  builds (``code_search.code_search`` / ``code_search.code_symbol``). A query
  never builds or rewrites the index (PRD-CORE-300-FR15).
* ``hint`` runs ``compute_before_edit_hint`` once per listed file: prior
  learnings for the file, plus the trw-distill sidecar half when a sidecar
  exists. Without trw-distill the learnings half still returns and
  ``distill_status`` says why there is no sidecar hint.

The tool is registered in every install. Nothing here imports the proprietary
package; the sidecar is read through its envelope contract.

Under the reviewer role (``TRW_SURFACE_ROLE=reviewer``) hint mode writes
nothing: ``compute_before_edit_hint`` skips its exposure rows and delivery
telemetry, and this module skips the tool-call telemetry and the
transition-nudge selector, which records what it has shown.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint

logger = structlog.get_logger(__name__)

#: Most files one hint call answers. Each file costs a learnings recall and a
#: sidecar lookup (two git subprocesses), so an unbounded list is a slow call.
MAX_HINT_FILES: int = 25

_MODES: tuple[str, ...] = ("search", "symbol", "hint")


def _refuse(error: str) -> dict[str, Any]:
    return {"status": "failed", "error": error}


def _search(mode: str, query: str, repo_root: str | None, top_k: int, path: str | None) -> dict[str, Any]:
    if not query.strip():
        return _refuse(f"mode={mode!r} needs query: the search text or the symbol name")
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.tools.code_search import code_search, code_symbol

    root = repo_root or str(resolve_project_root())
    run = code_search if mode == "search" else code_symbol
    return run(repo_root=root, query=query, top_k=top_k, path=path)


def _one_hint(file_path: str, repo_root: str | None, client_tier: str | None, reviewer: bool) -> dict[str, Any]:
    result = compute_before_edit_hint(file_path=file_path, repo_root=repo_root)
    hint: dict[str, Any] = result.model_dump()
    if not reviewer:
        with suppress(Exception):  # justified: fail-open telemetry, never break the tool
            from trw_mcp.channels._distill_telemetry import emit_tool_call

            sidecar_sha = result.distill_sidecar_sha or ""
            emit_tool_call(
                tool_name="trw_code",
                file_path=file_path,
                tier=result.tier,
                record_ids=[f"hotspot:{file_path}@{sidecar_sha[:8]}"] if sidecar_sha else [],
            )
        # PRD-CORE-294 FR04(b): the top-learning transition nudge, reusing the
        # learnings already collected. The selector records what it has shown.
        with suppress(Exception):  # justified: fail-open, a nudge must never break the hint
            from trw_mcp.tools._ceremony_status_context import maybe_attach_edit_hint_transition_nudge

            maybe_attach_edit_hint_transition_nudge(hint, None, learnings=result.learnings)
    if client_tier is not None:
        with suppress(Exception):  # justified: fail-open enrichment never breaks the hint
            from trw_mcp.channels._tool_return_tiers import enrich_response

            return enrich_response(hint, client_tier=client_tier)
    return hint


def _hint(files: str | list[str] | None, repo_root: str | None, ctx: Context | None) -> dict[str, Any]:
    paths = [files] if isinstance(files, str) else list(files or [])
    paths = [path for path in paths if path.strip()]
    if not paths:
        return _refuse("mode='hint' needs files: one path or a list of paths")
    if len(paths) > MAX_HINT_FILES:
        return _refuse(f"mode='hint' takes at most {MAX_HINT_FILES} files; got {len(paths)}")

    from trw_mcp.state._surface_role import reviewer_role_active

    reviewer = reviewer_role_active()
    client_tier: str | None = None
    with suppress(Exception):  # justified: fail-open, an unresolved client only skips enrichment
        from trw_mcp.tools._client_detection import resolve_client_profile, resolve_tier_for_client

        client_tier = resolve_tier_for_client(resolve_client_profile(ctx=ctx))
    hints: list[dict[str, Any]] = []
    for path in paths:
        try:
            hints.append(_one_hint(path, repo_root, client_tier, reviewer))
        except Exception as exc:  # justified: one file's failure must not cost the other files their hints
            logger.warning("trw_code_hint_file_failed", file_path=path, error=str(exc))
            hints.append({"file_path": path, "status": "failed", "error": str(exc)})
    return {"status": "ok", "hints": hints, "count": len(hints)}


def register_code_tools(server: FastMCP) -> None:
    """Register ``trw_code``."""

    @server.tool(name="trw_code", output_schema=None)
    def trw_code(
        mode: str = "search",
        query: str = "",
        files: str | list[str] | None = None,
        repo_root: str | None = None,
        top_k: int = 10,
        path: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Search indexed code, find a symbol's definition, or get before-edit hints.

        Use when you need code context without grepping the tree or reading
        whole files, or before editing a file.

        Output: search/symbol give status and results (path, line_range,
        symbol, snippet); hint gives hints, one per file, each with learnings
        and a distill_status.

        Args:
            mode: "search" (query is full text), "symbol" (query is a name;
                exact matches first) or "hint".
            files: hint mode: one path or a list.
            repo_root: defaults to the project root.
            path: search/symbol: limit results to this path prefix.
        """
        if mode in ("search", "symbol"):
            return _search(mode, query, repo_root, top_k, path)
        if mode == "hint":
            return _hint(files, repo_root, ctx)
        return _refuse(f"unknown mode {mode!r}; use one of {', '.join(_MODES)}")


__all__ = ["MAX_HINT_FILES", "register_code_tools"]
