"""MCP tool — ``trw_profile_explain`` (PRD-HPO-PROF-001 FR-4 / FR-11).

Renders the per-field layer attribution for the resolved session profile so
an operator can answer "*why* did this gate fire?" — the NIST 24-hour
reconstruction surface (PRD §7.7). Kept in its own file so ``server/_tools.py``
imports stay lean and the profile package keeps its facade boundary.

The tool resolves the current session's profile (defaults → org → domain →
task-type → session → client) and returns the explain payload: for every
surface field, ``{field, value, origin_layer, override_chain[]}`` plus
``layers_applied``, ``surface_snapshot_id``, and ``session_override_hash``.
Fail-open: any resolution error returns a structured ``error`` payload, never
raises.
"""

from __future__ import annotations

import structlog
from fastmcp import Context, FastMCP

logger = structlog.get_logger(__name__)


def register_trw_profile_explain_tools(server: FastMCP) -> None:
    """Register ``trw_profile_explain`` on the MCP server."""

    @server.tool(output_schema=None)
    def trw_profile_explain(
        domain: str = "",
        task_type: str = "",
        prd_path: str = "",
        task_name: str = "",
        ctx: Context | None = None,
    ) -> dict[str, object]:
        """Explain the resolved profile's per-field layer attribution.

        Use when:
        - A surprising ceremony/review/build-check gate fires and you need to
          see WHICH layer contributed the offending value.
        - Auditing the policy in force for the session (NIST 24h reconstruction).

        Resolves the full 6-layer chain (defaults → org → domain → task-type →
        session → client) and reports, for every surface field, its effective
        value, the origin layer, and the full override chain.

        Input (all optional — inferred when omitted):
        - domain: override the inferred domain layer (e.g. ``frontend``).
        - task_type: override the inferred task-type layer (e.g. ``bugfix``).
        - prd_path: PRD/file path used to infer the domain when not explicit.
        - task_name: task name used to infer the task-type when not explicit.

        Output: dict with ``fields`` (list of {field, value, origin_layer,
        override_chain}), ``layers_applied``, ``surface_snapshot_id``,
        ``session_override_hash``, and ``resolved_profile``. On error: a
        ``{error: str}`` payload (fail-open, never raises).
        """
        try:
            from trw_mcp.models.config import get_config
            from trw_mcp.profile import build_explanation, resolve_session_profile
            from trw_mcp.state._call_context import build_call_context
            from trw_mcp.state._paths import find_active_run, resolve_trw_dir

            config = get_config()
            trw_dir = resolve_trw_dir()
            run_dir = find_active_run(context=build_call_context(ctx))
            resolved = resolve_session_profile(
                config,
                run_dir=run_dir,
                domain=domain or None,
                task_type=task_type or None,
                prd_path=prd_path or None,
                task_name=task_name or None,
                trw_dir=trw_dir,
            )
            return build_explanation(resolved)
        except Exception as exc:  # justified: fail-open, tool must never crash
            logger.warning("profile_explain_tool_failed", error=str(exc))
            return {"error": str(exc), "fields": [], "layers_applied": []}


__all__ = ["register_trw_profile_explain_tools"]
