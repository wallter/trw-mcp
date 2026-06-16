"""Best-effort telemetry emission for SAFE-001 promotion decisions."""

from __future__ import annotations

import structlog

from trw_mcp.telemetry.event_base import MetaTuneEvent

logger = structlog.get_logger(__name__)


def emit_promotion_gate_decision(
    *,
    proposal_id: str,
    decision: str,
    reason: str,
    vote_count: int,
    surface_classification: str,
) -> None:
    """Emit the promotion-gate decision without making telemetry authoritative."""
    try:
        from trw_mcp.telemetry.unified_events import emit as _emit_unified

        event = MetaTuneEvent(
            session_id=proposal_id,
            payload={
                "action": "promotion_gate_evaluate",
                "proposal_id": proposal_id,
                "decision": decision,
                "reason": reason,
                "promotion_gate_vote_count": vote_count,
                "surface_classification_result": surface_classification,
            },
        )
        # PRD-HPO-SAFE-001 telemetry dispatch — emit via unified writer.
        # fallback_dir=None leaves emission best-effort when no run is pinned;
        # the audit log is the authoritative record either way.
        _emit_unified(event, run_dir=None, fallback_dir=None)
    except Exception:  # justified: telemetry_best_effort, gate must not raise
        logger.warning(
            "promotion_gate_telemetry_failed",
            component="meta_tune.promotion_gate",
            op="_record",
            outcome="degraded",
        )


__all__ = ["emit_promotion_gate_decision"]
