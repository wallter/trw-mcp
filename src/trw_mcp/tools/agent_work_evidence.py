"""AgentWorkEvidence v1 export and validation: the ``trw_dispatch`` evidence modes.

``trw_dispatch(action="evidence")`` and ``trw_dispatch(action="validate_evidence")``
call these (PRD-CORE-300-FR09). They were two tools of their own before S7.
"""

from __future__ import annotations

import json
from typing import cast

import structlog
from fastmcp import Context

from trw_mcp.exceptions import StateError
from trw_mcp.models.agent_work_evidence import AgentWorkEvidence, validate_agent_work_evidence
from trw_mcp.state._call_context import build_call_context as _build_call_context
from trw_mcp.state._paths import resolve_run_path
from trw_mcp.state.agent_work_evidence import assemble_agent_work_evidence

logger = structlog.get_logger(__name__)


def export_evidence(
    ctx: Context | None, run_path: str | None, *, include_events: bool, include_schema: bool
) -> dict[str, object]:
    """One schema-valid, privacy-safe work record for a run (the active run by default)."""
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


def validate_evidence(document: str) -> dict[str, object]:
    """``valid`` plus errors, each with a field path, type and message, for a JSON document.

    The candidate arrives as JSON text: one ``target`` string carries every mode's
    argument, which keeps ``trw_dispatch`` under the per-tool signature ceiling.
    Text that is not a JSON object is reported in the same error shape.
    """
    try:
        data = json.loads(document)
    except json.JSONDecodeError as exc:
        return {"valid": False, "errors": [{"loc": [], "type": "json_invalid", "message": str(exc)}]}
    if not isinstance(data, dict):
        return {"valid": False, "errors": [{"loc": [], "type": "dict_type", "message": "expected a JSON object"}]}
    return cast("dict[str, object]", validate_agent_work_evidence(data).model_dump(mode="json"))
