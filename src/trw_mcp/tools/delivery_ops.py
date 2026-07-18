"""Public delivery-operation MCP tools — PRD-CORE-208 FR05 / FR04.

Registers two tools that expose the durable delivery journal without letting a
harmless query gain recovery authority (§6.5):

- ``trw_delivery_status`` — mechanically read-only projection (FR05). Annotated
  ``readOnlyHint``/``idempotentHint``/``openWorldHint=false``, opens the store
  via SQLite ``mode=ro``, and never claims/refreshes a lease, sweeps retention,
  invokes delivery, or creates the database.
- ``trw_delivery_recover`` — capability-guarded mutation (FR04). Splits stale
  takeover and crash reconciliation from status so recovery authority is
  explicit. Every action requires the caller-held recovery capability + expected
  revision + reason before ownership changes.

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

_MAX_REASON_CHARS = 500
_MAX_EVIDENCE_REF_CHARS = 1024
_TAKEOVER = "takeover_pending"
_RECONCILE_APPLIED = "reconcile_applied"
_RECONCILE_NOT_APPLIED = "reconcile_not_applied"
_REQUEST_CANCEL = "request_cancel"
_RUN_COMPENSATION = "run_compensation"
_SUPPORTED_ACTIONS = (
    _TAKEOVER,
    _RECONCILE_APPLIED,
    _RECONCILE_NOT_APPLIED,
    _REQUEST_CANCEL,
    _RUN_COMPENSATION,
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
        """Read a delivery operation's crash-safe status without any mutation.

        Use when a ``trw_deliver`` response timed out or the process restarted and
        you need to know whether the delivery ran — without re-running it.

        Mechanically read-only (PRD-CORE-208 FR05): opens the operation store
        ``mode=ro``, and never invokes delivery, claims/refreshes a lease,
        reconciles, sweeps retention, or creates a missing database. Never exposes
        the recovery capability, its hash, the full request digest, or absolute
        paths.

        Input:
        - delivery_id: the caller UUIDv7 supplied to ``trw_deliver``.
        - verbose: when ``True``, return the full 46-entry step census (each with
          its static ``replay_class``) for FR05 audits. The default compact
          response enumerates only steps that have run and adds
          ``steps_total``/``steps_started``/``steps_succeeded`` counts — the
          durable journal truth is complete either way.

        Output: a projection dict with a stable ``result`` — ``ok`` (operation
        state/revision, critical/deferred summary, per-step replay class, queue
        disposition, recovery eligibility), ``not_found_store``, ``not_found_id``,
        ``invalid_id``, ``tombstone``, ``corrupt_store``,
        ``unsupported_schema``, or ``legacy_wal_migration_required``. Every
        response also carries an ``envelope`` — the PRD-CORE-215 FR02
        :class:`ToolResultEnvelope` projection of the same state — added
        alongside the legacy shape (the legacy keys remain authoritative for
        existing readers; the envelope is the typed common surface).
        """
        try:
            projection = _coordinator().project_status(delivery_id, verbose=verbose)
        except Exception:  # justified: read-only tool must never raise into the client
            logger.debug("delivery_status_failed", exc_info=True)
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
        """Authorized recovery of a stale/crashed delivery operation (FR04).

        Use when a delivery lease is stale or a delivery process crashed
        mid-operation and ownership must be reclaimed or started effects
        reconciled — never for a routine status check (use ``trw_delivery_status``).

        Separate from status so a harmless query never gains recovery authority
        (§6.5). A NON_REPLAYABLE started effect (trust increment, external send,
        destructive purge) is NEVER blindly replayed — crash reconciliation marks
        it ``indeterminate``.

        Input:
        - delivery_id: the caller UUIDv7 to recover.
        - action: ``takeover_pending``, ``reconcile_applied``,
          ``reconcile_not_applied``, ``request_cancel``, or ``run_compensation``.
        - capability_token / expected_revision / reason: required for every
          mutation (constant-time capability check and exact revision match).
        - new_owner / new_pid: the taking-over owner identity.
        - effect_id / evidence_ref: required for reconciliation/compensation.

        Output: a dict with the recovery ``status`` (e.g. ``ok``,
        ``unauthorized``, ``lease_not_stale``, ``stale_revision``, ``live_owner``,
        ``not_found``) plus reconciled/indeterminate effect ids.
        """
        if action not in _SUPPORTED_ACTIONS:
            return {"result": "unsupported_action", "action": action, "supported": list(_SUPPORTED_ACTIONS)}
        if len(reason) > _MAX_REASON_CHARS:
            return {"result": "invalid_reason", "reason_code": "oversize_reason"}
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
            elif action == _REQUEST_CANCEL:
                result = coord.request_cancel(
                    operation_id=delivery_id,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                )
            else:
                result = coord.run_compensation(
                    operation_id=delivery_id,
                    effect_id=effect_id,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                    evidence_ref=evidence_ref,
                )
        except Exception:  # justified: fail-closed — a recover failure changes nothing
            logger.debug("delivery_recover_failed", action=action, exc_info=True)
            return {"result": "error", "reason_code": "recover_unavailable"}
        return cast("dict[str, object]", result.model_dump(mode="json"))
