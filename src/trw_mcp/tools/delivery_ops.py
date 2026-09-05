"""Public delivery-operation MCP tools — PRD-CORE-208 FR05 / FR04.

Registers two tools that expose the durable delivery journal without letting a
harmless query gain recovery authority (§6.5):

- ``trw_delivery_status`` — mechanically read-only projection (FR05). Annotated
  ``readOnlyHint``/``idempotentHint``/``openWorldHint=false``, opens the store
  via SQLite ``mode=ro``, and never claims/refreshes a lease, sweeps retention,
  invokes delivery, or creates the database.
- ``trw_delivery_recover`` — capability-guarded mutation (FR04 / PRD-FIX-127
  FR01+FR06). Splits stale takeover, ``resume``, and crash reconciliation from
  status so recovery authority is explicit. Every action requires the caller-held
  recovery capability + expected revision + reason before ownership changes. The
  always-refusing rollback action was deleted by PRD-FIX-127 FR06 (no descriptor
  registers a compensator, so its entire behaviour was to refuse).

Belongs to the ``server/_tools.py`` registration site. The coordinator resolves
its project scope from the same default installation identity ``run_trw_deliver``
uses, so a status/recover call reads the operation a timed-out deliver claimed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import structlog
from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from trw_mcp.tools._operation_owner_adapter import status_envelope
from trw_mcp.tools.telemetry import log_tool_call

if TYPE_CHECKING:
    from trw_mcp.tools._delivery_operations import DeliveryCoordinator

logger = structlog.get_logger(__name__)

_TAKEOVER = "takeover_pending"
_RECONCILE_APPLIED = "reconcile_applied"
_RECONCILE_NOT_APPLIED = "reconcile_not_applied"
_REQUEST_CANCEL = "request_cancel"
_RESUME = "resume"
#: PRD-FIX-127 FR06 deleted the rollback action from this tuple; requesting it
#: now returns ``unsupported_action``, which is the truthful answer.
_SUPPORTED_ACTIONS = (
    _TAKEOVER,
    _RESUME,
    _RECONCILE_APPLIED,
    _RECONCILE_NOT_APPLIED,
    _REQUEST_CANCEL,
)


def _coordinator() -> DeliveryCoordinator:
    """Build a DeliveryCoordinator over the active project (never creates the DB)."""
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.tools._delivery_operations import DeliveryCoordinator

    return DeliveryCoordinator(resolve_trw_dir())


def register_delivery_tools(server: FastMCP) -> None:
    """Register trw_delivery_status (read-only) + trw_delivery_recover (mutating)."""

    @server.tool(
        output_schema=None,
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    @log_tool_call
    def trw_delivery_status(
        ctx: Context | None = None,
        delivery_id: str = "",
        verbose: bool = False,
    ) -> dict[str, object]:
        """Read a delivery operation's crash-safe status, read-only.

        Use when trw_deliver timed out or the process restarted, to check
        whether it ran. Needs the delivery_id trw_deliver returned.
        """
        # PRD-CORE-208 FR05: this projection never exposes the recovery
        # capability, its hash, the full request digest, or absolute paths —
        # only project_status()'s already-redacted shape reaches the caller.
        projection: dict[str, object]
        try:
            projection = _coordinator().project_status(delivery_id, verbose=verbose)
        except Exception:  # justified: read-only tool must never raise into the client
            logger.debug("delivery_status_failed", exc_info=True)
            # Annotated above rather than inferred: without it the fallback
            # literal narrows to dict[str, str] and the envelope assignment
            # below becomes a type error on the failure path only.
            projection = {"result": "error", "reason_code": "status_unavailable"}
        projection["envelope"] = status_envelope(projection, request_id=delivery_id).model_dump(mode="json")
        return projection

    @server.tool(
        output_schema=None,
        annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False, openWorldHint=False),
    )
    @log_tool_call
    def trw_delivery_recover(
        ctx: Context | None = None,
        delivery_id: str = "",
        action: str = _TAKEOVER,
        capability_token: str = "",
        expected_revision: int = 0,
        reason: str = "",
        new_owner: str = "",
        new_pid: int = 0,
        effect_id: str = "",
        evidence_ref: str = "",
    ) -> dict[str, object]:
        """Recover a stale/crashed delivery. Use when a lease is stale or
        its process crashed — not for routine checks (trw_delivery_status).
        Requires the delivery_id AND capability_token trw_deliver returned
        (the recovery secret) plus exact expected_revision.

        Args: action in {takeover_pending, resume, reconcile_applied,
        reconcile_not_applied, request_cancel}. Use resume to finish a
        crashed delivery under the SAME delivery_id: it classifies the
        crashed steps, refuses with reconciliation_required while any is
        indeterminate, and otherwise grants this process a fresh lease so
        a re-invoked trw_deliver runs only the steps that never started.
        """
        if action not in _SUPPORTED_ACTIONS:
            return {"result": "unsupported_action", "action": action, "supported": list(_SUPPORTED_ACTIONS)}
        # One source of truth for both caps (DeliveryLimits), pre-checked here so
        # an oversize input names ITS OWN reason. The local 500/1024 copies this
        # replaces were a DRY hazard, and the evidence_ref one was never read at
        # all: an oversize evidence_ref fell through to the journal, raised, and
        # came back as the generic "recover_unavailable" — an input error
        # reported as an infrastructure failure.
        from trw_mcp.tools._delivery_models import DELIVERY_JOURNAL_OWNER
        from trw_mcp.tools._delivery_recovery import enforce_reason_bounds
        from trw_mcp.tools._delivery_request import DeliveryRequestError

        try:
            enforce_reason_bounds(reason, evidence_ref)
        except DeliveryRequestError as err:
            return {"result": "invalid_request", "reason_code": err.code}
        try:
            coord = _coordinator()
            if action == _TAKEOVER:
                result = coord.takeover(
                    operation_id=delivery_id,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                    new_owner=new_owner,
                    new_pid=new_pid,
                )
            elif action in {_RECONCILE_APPLIED, _RECONCILE_NOT_APPLIED}:
                result = coord.reconcile_effect(
                    operation_id=delivery_id,
                    effect_id=effect_id,
                    applied=action == _RECONCILE_APPLIED,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                    evidence_ref=evidence_ref,
                )
            elif action == _RESUME:
                # PRD-FIX-127 FR01/FR02: the grant is bound to the delivery
                # journal owner and to THIS server process, so it cannot be
                # consumed by an unrelated caller or banked across a restart.
                result = coord.resume(
                    operation_id=delivery_id,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                    new_owner=new_owner or DELIVERY_JOURNAL_OWNER,
                    new_pid=new_pid,
                )
            else:
                result = coord.request_cancel(
                    operation_id=delivery_id,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                )
        except Exception:  # justified: fail-closed — a recover failure changes nothing
            logger.debug("delivery_recover_failed", action=action, exc_info=True)
            return {"result": "error", "reason_code": "recover_unavailable"}
        return cast("dict[str, object]", result.model_dump(mode="json"))
