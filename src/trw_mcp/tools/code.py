"""``trw_code``: symbol lookup and before-edit hints in one tool (PRD-CORE-300-FR12).

Two modes over two engines:

* ``symbol`` reads the local code index that ``trw-mcp code index`` builds
  (``code_search.code_symbol``). A query never builds or rewrites the index
  (PRD-CORE-300-FR15).
* ``hint`` runs ``compute_before_edit_hint`` once per listed file: prior
  learnings for the file, plus the trw-distill sidecar half when a sidecar
  exists. Without trw-distill the learnings half still returns and
  ``distill_status`` says why there is no sidecar hint. ``hint`` is the
  default mode: it needs no query, matches the pre-edit workflow every
  install's hooks already call, and covers nearly all of this tool's live
  usage (retired ``search`` accounted for the rest).

``mode="search"`` was retired in 8.0 (full-text lexical search over the local
index): agents get more current results from ``rg``/``grep`` for text search
or the ``trw-distill`` CLI for codebase intelligence, and it saw negligible use
next to those. The mode now returns an actionable error rather than results. Retired
modes live in ``_code_modes.RETIRED_MODES``, which ``scripts/lint-instruction-surfaces.py``
also reads so no shipped instruction can advertise one.

The tool is registered in every install. Nothing here imports the proprietary
package; the sidecar is read through its envelope contract.

Under the reviewer role (``TRW_SURFACE_ROLE=reviewer``) hint mode writes
nothing: ``compute_before_edit_hint`` skips its exposure rows and delivery
telemetry, and this module skips the tool-call telemetry and the
transition-nudge selector, which records what it has shown.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.tools._before_edit_hint_core import compute_before_edit_hint
from trw_mcp.tools._code_modes import LIVE_MODES, RETIRED_MODES

logger = structlog.get_logger(__name__)

#: Most files one hint call answers. Each file costs a learnings recall and a
#: sidecar lookup (two git subprocesses), so an unbounded list is a slow call.
MAX_HINT_FILES: int = 25

#: Longest caller path a hint entry repeats back as its label.
_MAX_SHOWN_PATH: int = 200


def _refuse(error: str) -> dict[str, Any]:
    return {"status": "failed", "error": error}


_NO_MATCH_HINT: str = (
    "no indexed symbol matches. Symbol mode indexes module-level classes, functions and assignments only; a "
    "method is inside its class (look up the class), and text needs `rg`/`grep`. The index is built by "
    "`trw-mcp code index` and is not rebuilt by a query."
)


def _symbol(query: str, repo_root: str | None, top_k: int, path: str | None) -> dict[str, Any]:
    if not query.strip():
        return _refuse("mode='symbol' needs query: the symbol name")
    from trw_mcp.tools.code_search import code_symbol

    root = repo_root or str(_project_root())
    response: dict[str, Any] = dict(code_symbol(repo_root=root, query=query, top_k=top_k, path=path))
    if response.get("status") == "ok" and not response.get("results"):
        # An empty answer carries its reason: an omitted or empty `results` reads like a broken index.
        response["results"] = []
        response["message"] = _NO_MATCH_HINT
    return response


def _project_root() -> Path:
    from trw_mcp.state._paths import resolve_project_root

    return Path(os.path.realpath(resolve_project_root()))


def _confined_repo_root(repo_root: str | None) -> tuple[str | None, str | None]:
    """``(resolved, problem)``: *repo_root* resolved once, or why it may not be used (E2E-INC-125).

    It is confined to the project like ``files`` are. The caller keeps using the RESOLVED form returned here, so a
    symlink swapped after this check cannot move the root the work runs against. The refusal names the project root
    and never repeats the value the caller sent.
    """
    if repo_root is None:
        return None, None
    root = _project_root()
    try:
        candidate = Path(os.path.realpath(repo_root))
    except (ValueError, OSError):  # a NUL byte or an unusable path cannot be inside the project
        return None, f"repo_root is not a usable path; it must be inside the project root {root}"
    if not candidate.is_relative_to(root):
        return None, f"repo_root must be inside the project root {root}; omit it to use the project root"
    return str(candidate), None


def _shown_path(file_path: str) -> str:
    """A caller's path as a result may label its entry: bounded, so a huge path is not echoed back in full."""
    return file_path if len(file_path) <= _MAX_SHOWN_PATH else file_path[: _MAX_SHOWN_PATH - 3] + "..."


def _path_problem(file_path: str, repo_root: str | None) -> tuple[str, str] | None:
    """``("outside", why)`` for a path that leaves the project, ``("not_found", why)`` for one that is absent, else None.

    The reason never repeats the path (the entry is already labelled with a bounded form of it).
    """
    root = Path(os.path.realpath(repo_root)) if repo_root else _project_root()
    candidate = Path(file_path)
    resolved = Path(os.path.realpath(candidate if candidate.is_absolute() else root / candidate))
    if not resolved.is_relative_to(root):
        return "outside", f"this path is outside the project root {root}; give a path inside the project"
    if not resolved.exists():
        return "not_found", (
            f"this path does not exist under {root}; this hint covers the path only (fine for a file you are "
            "about to create, a typo otherwise)"
        )
    return None


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
            problem = _path_problem(path, repo_root)
        except (ValueError, OSError) as exc:  # justified: a malformed path (a NUL byte) fails its own hint only
            problem = ("outside", f"this is not a usable path ({type(exc).__name__}); it cannot be inside the project")
        if problem is not None and problem[0] == "outside":
            hints.append({"file_path": _shown_path(path), "status": "failed", "error": problem[1]})
            continue
        try:
            hint = _one_hint(path, repo_root, client_tier, reviewer)
            if problem is not None:
                hint["path_status"], hint["path_note"] = problem
            hints.append(hint)
        except Exception as exc:  # justified: one file's failure must not cost the other files their hints
            logger.warning("trw_code_hint_file_failed", file_path=_shown_path(path), error_type=type(exc).__name__)
            hints.append(
                {
                    "file_path": _shown_path(path),
                    "status": "failed",
                    "error": f"the hint could not be computed ({type(exc).__name__})",
                }
            )
    failed = sum(1 for hint in hints if hint.get("status") == "failed")
    return {
        "status": "failed" if failed == len(hints) else "ok",
        "hints": hints,
        "count": len(hints),
        **({"failed_count": failed} if failed else {}),
    }


def register_code_tools(server: FastMCP) -> None:
    """Register ``trw_code``."""

    @server.tool(name="trw_code", output_schema=None)
    def trw_code(
        mode: str = "hint",
        query: str = "",
        files: str | list[str] | None = None,
        repo_root: str | None = None,
        top_k: int = 10,
        path: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when you need a symbol's definition or a file's before-edit context,
        without grepping or reading whole files.

        Output: symbol returns results (path, line_range, symbol, snippet); hint
        returns one hint per file. Symbol needs `trw-mcp code index` first (a query
        never builds it) and finds module-level classes and functions, not methods.

        Args:
            mode: "hint" (default) or "symbol" (exact matches first).
                "search" is retired -- use `rg`/`grep` or `trw-distill`.
            files: hint mode: one path or a list.
            repo_root: defaults to the project root.
            path: symbol mode: limit results to this path prefix.
        """
        if mode in RETIRED_MODES:
            return _refuse(RETIRED_MODES[mode])
        repo_root, root_problem = _confined_repo_root(repo_root)
        if root_problem is not None:
            return _refuse(root_problem)
        if mode == "symbol":
            return _symbol(query, repo_root, top_k, path)
        if mode == "hint":
            return _hint(files, repo_root, ctx)
        return _refuse(f"unknown mode; use one of {', '.join(LIVE_MODES)}")


__all__ = ["MAX_HINT_FILES", "register_code_tools"]
