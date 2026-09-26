"""Delivery-operation status and recovery — PRD-CORE-208 FR05 / FR04, PRD-CORE-300-FR03.

Two entry points over the durable delivery journal, kept apart so a harmless
query never gains recovery authority (§6.5):

- :func:`delivery_status` backs ``trw_status(delivery=...)``. It is mechanically
  read-only: it opens the store via SQLite ``mode=ro`` and never claims or
  refreshes a lease, sweeps retention, invokes delivery, or creates the database.
- :func:`delivery_recover` backs ``trw-mcp delivery recover``, a state-changing
  CLI verb under the FR02 contract. Every action requires the caller-held
  recovery capability, the expected revision and a reason before ownership
  changes. The always-refusing rollback action was deleted by PRD-FIX-127 FR06
  (no descriptor registers a compensator, so its entire behaviour was to refuse).

The coordinator resolves its project scope from the same default installation
identity ``run_trw_deliver`` uses, so both read the operation a timed-out
deliver claimed.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.tools._operation_owner_adapter import status_envelope

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


def delivery_status(delivery_id: str, *, verbose: bool = False) -> dict[str, object]:
    """A delivery operation's crash-safe status, read-only, plus ``resume_pid``.

    ``resume_pid`` is this server process's id. A resume grant is consumed by
    ``trw_deliver`` only in the process that holds the lease, so a CLI resume
    must pass it as ``--new-pid``.
    """
    # PRD-CORE-208 FR05: this projection never exposes the recovery
    # capability, its hash, the full request digest, or absolute paths —
    # only project_status()'s already-redacted shape reaches the caller.
    projection: dict[str, object]
    try:
        projection = _coordinator().project_status(delivery_id, verbose=verbose)
    except Exception:  # justified: a read-only status must never raise into the client
        logger.debug("delivery_status_failed", exc_info=True)
        # Annotated above rather than inferred: without it the fallback
        # literal narrows to dict[str, str] and the envelope assignment
        # below becomes a type error on the failure path only.
        projection = {"result": "error", "reason_code": "status_unavailable"}
    projection["resume_pid"] = os.getpid()
    projection["envelope"] = status_envelope(projection, request_id=delivery_id).model_dump(mode="json")
    return projection


def delivery_recover(
    *,
    delivery_id: str,
    action: str = _TAKEOVER,
    capability_token: str = "",
    expected_revision: int = 0,
    reason: str = "",
    new_owner: str = "",
    new_pid: int = 0,
    effect_id: str = "",
    evidence_ref: str = "",
) -> dict[str, object]:
    """Recover a stale or crashed delivery; ``result``/``status`` report the outcome.

    ``resume`` finishes a crashed delivery under the SAME delivery_id (it may
    refuse with ``reconciliation_required``); a re-invoked ``trw_deliver`` then
    runs only the steps that never started. It needs ``new_pid``: the server pid
    that ``trw_status(delivery=...)`` reports as ``resume_pid``.
    """
    if action not in _SUPPORTED_ACTIONS:
        return {"result": "unsupported_action", "action": action, "supported": list(_SUPPORTED_ACTIONS)}
    if action == _RESUME and new_pid <= 0:
        # The grant would otherwise go to the caller's pid. From the CLI that is a
        # process about to exit, so no trw_deliver could ever consume the grant and
        # the lease would block the operation until it expired.
        return {"result": "invalid_request", "reason_code": "resume_needs_server_pid"}
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
            # journal owner and to the server process new_pid names, so it
            # cannot be consumed by an unrelated caller or banked across a restart.
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
