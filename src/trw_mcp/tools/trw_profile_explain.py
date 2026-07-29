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
        """Show which config layer set each field of the resolved profile.

        Use when: a ceremony/review/build-check gate fires unexpectedly.

        Output: the resolved profile, and per field the layer that set it.

        Args:
            domain: override the inferred domain, e.g. "frontend".
            task_type: override the inferred task type, e.g. "bugfix".
            prd_path: infers domain when domain is unset.
            task_name: infers task_type when task_type is unset.
        """
        # Resolves the full 6-layer chain: defaults -> org -> domain ->
        # task-type -> session -> client.
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
