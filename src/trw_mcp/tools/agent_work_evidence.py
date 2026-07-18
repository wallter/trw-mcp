"""MCP tools for AgentWorkEvidence v1 export and validation."""

from __future__ import annotations

from typing import cast

import structlog
from fastmcp import Context, FastMCP

from trw_mcp.exceptions import StateError
from trw_mcp.models.agent_work_evidence import AgentWorkEvidence, validate_agent_work_evidence
from trw_mcp.state._call_context import build_call_context as _build_call_context
from trw_mcp.state._paths import resolve_run_path
from trw_mcp.state.agent_work_evidence import assemble_agent_work_evidence
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def register_agent_work_evidence_tools(server: FastMCP) -> None:
    """Register AgentWorkEvidence export and validation tools."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_agent_work_evidence(
        ctx: Context | None = None,
        run_path: str | None = None,
        include_events: bool = False,
        include_schema: bool = False,
    ) -> dict[str, object]:
        """Export canonical privacy-safe AgentWorkEvidence for a TRW run.

        Use when a judge, eval harness, reviewer, or knowledge-graph importer
        needs one schema-valid work record instead of scraping run internals.

        Args:
            ctx: Optional FastMCP context used to resolve the active run pin.
            run_path: Optional explicit run directory.
            include_events: Include safe event references without payload bodies.
            include_schema: Include the JSON Schema for AgentWorkEvidence v1.

        Returns:
            {"evidence": {...}, "schema"?: {...}} or {"status": "failed", "error": str}
        """
        try:
            resolved_path = resolve_run_path(run_path, context=_build_call_context(ctx))
            evidence = assemble_agent_work_evidence(resolved_path, include_events=include_events)
        except (OSError, StateError, ValueError) as exc:
            return {"error": str(exc), "status": "failed"}
        logger.info("trw_agent_work_evidence_generated", run_id=evidence.identity.run_id)
        result: dict[str, object] = {"evidence": cast("dict[str, object]", evidence.model_dump(mode="json"))}
        if include_schema:
            result["schema"] = cast("dict[str, object]", AgentWorkEvidence.model_json_schema())
        return result

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_validate_agent_work_evidence(data: dict[str, object]) -> dict[str, object]:
        """Validate an AgentWorkEvidence candidate and return structured errors.

        Use when an external producer or fixture needs schema validation before
        evidence is accepted by a judge or graph-ingestion pipeline.

        Args:
            data: Candidate AgentWorkEvidence JSON object.

        Returns:
            {"valid": bool, "errors": list[...]}
        """
        result = validate_agent_work_evidence(data)
        return cast("dict[str, object]", result.model_dump(mode="json"))
