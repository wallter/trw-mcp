"""SAFE-001 operator tools."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from trw_mcp.meta_tune.dispatch import promote_candidate
from trw_mcp.meta_tune.rollback import rollback_proposal
from trw_mcp.meta_tune.surface_registry import classify_path


def _consult(tool_name: str, args: dict[str, Any]) -> None:
    try:
        from trw_mcp.server._security_hook import consult_mcp_security
    except Exception:
        return
    consult_mcp_security(tool_name, args, "", None)


def register_meta_tune_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_surface_classify(path: str) -> dict[str, Any]:
        _consult("trw_surface_classify", {"path": path})
        classification = classify_path(Path(path))
        return {
            "path": path,
            "classification": "control" if classification.is_control else "advisory",
            "surfaces": [surface.value for surface in classification.surfaces],
            "rationale": classification.rationale,
        }

    @server.tool()
    def trw_meta_tune_propose(
        target_path: str,
        candidate_content: str,
        proposer_id: str,
        sandbox_command: list[str],
        reviewer_id: str | None = None,
        approval_ts: str | None = None,
        declared_metric_delta: float | None = None,
        promotion_session_id: str | None = None,
        state_dir: str | None = None,
    ) -> dict[str, Any]:
        _consult(
            "trw_meta_tune_propose",
            {
                "target_path": target_path,
                "proposer_id": proposer_id,
                "reviewer_id": reviewer_id,
                "promotion_session_id": promotion_session_id,
            },
        )
        from trw_mcp.models.config import get_config

        parsed_approval_ts = (
            datetime.fromisoformat(approval_ts.replace("Z", "+00:00"))
            if approval_ts
            else None
        )
        result = promote_candidate(
            target_path=Path(target_path),
            candidate_content=candidate_content,
            proposer_id=proposer_id,
            reviewer_id=reviewer_id,
            approval_ts=parsed_approval_ts,
            sandbox_command=sandbox_command,
            declared_metric_delta=declared_metric_delta,
            promotion_session_id=promotion_session_id,
            state_dir=Path(state_dir) if state_dir is not None else None,
            _config=get_config(),
        )
        return result.model_dump()

    @server.tool()
    def trw_meta_tune_rollback(
        proposal_id: str,
        state_dir: str | None = None,
        audit_log_path: str | None = None,
    ) -> dict[str, Any]:
        _consult(
            "trw_meta_tune_rollback",
            {
                "proposal_id": proposal_id,
                "state_dir": state_dir,
                "audit_log_path": audit_log_path,
            },
        )
        from trw_mcp.models.config import get_config

        config = get_config()
        if audit_log_path is not None:
            config = config.model_copy(
                update={
                    "meta_tune": config.meta_tune.model_copy(update={"enabled": True, "audit_log_path": audit_log_path})
                }
            )
        elif not config.meta_tune.enabled:
            config = config.model_copy(
                update={
                    "meta_tune": config.meta_tune.model_copy(update={"enabled": True})
                }
            )
        result = rollback_proposal(
            proposal_id,
            state_dir=Path(state_dir) if state_dir is not None else None,
            _config=config,
        )
        return result.model_dump()


__all__ = ["register_meta_tune_tools"]
