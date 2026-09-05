"""The ``resume`` recovery action — PRD-FIX-127 FR01.

Belongs to the ``tools/_delivery_operations.py`` facade. This is the first (and
only) production caller of :func:`apply_crash_recovery_locked`; before this PRD
the classifier that turns a crashed ``started`` ``non_replayable`` step into
``indeterminate`` was reachable from tests alone, so PRD-CORE-208 FR04's clause
"a pending operation with no started step can then resume" had no code at all.

The whole action runs in ONE ``BEGIN IMMEDIATE`` transaction so classification,
the indeterminacy check, and the lease grant share a single locked snapshot
(NFR01). It widens no authority: the same constant-time capability check, the
same exact expected revision, the same bounded reason, the same stale-lease floor
and the same dead-owner probe that ``takeover_pending`` already requires (NFR02).

Ordering note against FR01's prose: the staleness/liveness guards are evaluated
BEFORE the classifier runs, not after. The classifier clears the lease it would
otherwise read, so running it first would make a still-fresh lease and a live
owner pass trivially and would mutate rows on a refusal path that NFR02 requires
to change nothing. Every acceptance outcome is unchanged by the reordering.

It performs zero product effects (NFR03): it writes only step rows, the operation
row, and one audit row.
"""

from __future__ import annotations

import os
import sqlite3

import structlog

from trw_mcp.tools._delivery_journal_store import JournalStore
from trw_mcp.tools._delivery_models import (
    DELIVERY_JOURNAL_OWNER,
    TERMINAL_OPERATION_STATES,
    OperationRecord,
    OperationState,
    RecoverResult,
    RecoverStatus,
    RecoveryAction,
    RecoveryEvent,
    StepState,
)
from trw_mcp.tools._delivery_reconcile_actions import DeliveryRecoveryActionsMixin
from trw_mcp.tools._delivery_recovery import fail_recovery, lease_takeover_refusal

logger = structlog.get_logger(__name__)

#: One source of truth for the capability / revision / reason gate every
#: recovery mutation shares — resume must not get a softer one.
_authorize_recovery = DeliveryRecoveryActionsMixin._authorize_recovery

#: Reason code returned when the classifier left at least one step indeterminate.
#: The operator must settle those through ``reconcile_applied`` /
#: ``reconcile_not_applied`` before a resume can be granted.
RECONCILIATION_REQUIRED = "reconciliation_required"


class DeliveryResumeMixin:
    """FR01 ``resume``: classify, refuse on indeterminacy, grant a fresh lease."""

    store: JournalStore

    @staticmethod
    def _now_ms() -> int:
        raise NotImplementedError

    @property
    def _stale_lease_ms(self) -> int:
        raise NotImplementedError

    def recover_after_crash(self, operation_id: str, conn: sqlite3.Connection | None = None) -> RecoverResult:
        """Provided by :class:`DeliveryCoordinator`; classifies under our lock."""
        raise NotImplementedError

    def resume(
        self,
        *,
        operation_id: str,
        capability_token: str,
        expected_revision: int,
        reason: str,
        new_owner: str = DELIVERY_JOURNAL_OWNER,
        new_pid: int = 0,
        owner_alive: bool | None = None,
    ) -> RecoverResult:
        """Grant a resume lease on a crashed, fully-determinate operation (FR01)."""
        owner = new_owner or DELIVERY_JOURNAL_OWNER
        pid = new_pid if new_pid > 0 else os.getpid()
        conn = self.store.connect()
        try:
            now = self._now_ms()
            stale_lease_ms = self._stale_lease_ms
            with self.store.immediate(conn):
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
                refusal = _authorize_recovery(op, capability_token, expected_revision, reason, "")
                if refusal is not None:
                    return refusal
                refusal = _state_refusal(op)
                if refusal is not None:
                    return refusal
                refusal = lease_takeover_refusal(
                    op,
                    now_ms=now,
                    stale_lease_ms=stale_lease_ms,
                    owner_alive=owner_alive,
                    self_owned_pid=pid,
                )
                if refusal is not None:
                    return refusal

                classified = self.recover_after_crash(operation_id, conn)
                unsettled = classified.indeterminate_effect_ids + self._still_started(conn, operation_id)
                if unsettled:
                    logger.info(
                        "delivery_resume_reconciliation_required",
                        operation_id=operation_id,
                        unsettled=sorted(set(unsettled)),
                    )
                    return classified.model_copy(
                        update={
                            "status": RecoverStatus.REJECTED,
                            "reason_code": RECONCILIATION_REQUIRED,
                            "indeterminate_effect_ids": tuple(sorted(set(unsettled))),
                        }
                    )
                return self._grant_locked(conn, operation_id, owner, pid, reason, now, stale_lease_ms)
        finally:
            conn.close()

    def _still_started(self, conn: sqlite3.Connection, operation_id: str) -> tuple[str, ...]:
        """Effect ids the classifier left ``started`` — unsettled, not replay-safe.

        ``apply_crash_recovery_locked`` reports the non-``NON_REPLAYABLE`` classes
        as "replay safe" on the strength of their registered idempotency keys. Those
        keys are NOT wired at the call sites (PRD-FIX-127 §2 Contributing Factors,
        OQ-004): ``_log_deliver_event`` appends ``S20`` with no effect id at all. So
        a ``started`` step of ANY class is an effect whose outcome is unknown, and
        re-running it would genuinely duplicate. Resume therefore refuses until the
        operator settles it, which is what makes FR02's zero-duplicated-effects
        claim true rather than aspirational. Lifting this is gated on OQ-004.
        """
        return tuple(
            sorted(
                step.effect_id for step in self.store.get_steps(conn, operation_id) if step.state is StepState.STARTED
            )
        )

    def _grant_locked(
        self,
        conn: sqlite3.Connection,
        operation_id: str,
        owner: str,
        pid: int,
        reason: str,
        now: int,
        stale_lease_ms: int,
    ) -> RecoverResult:
        """Commit the fresh lease + the single ``resume`` audit row (FR01 steps 5-6).

        Re-reads the operation because the classifier bumped its revision and
        cleared the lease it just evaluated.
        """
        op = self.store.get_operation(conn, operation_id)
        if op is None:  # pragma: no cover - the row was read under the same lock
            return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
        updated = op.model_copy(
            update={
                "revision": op.revision + 1,
                "updated_utc_ms": now,
                "lease_owner": owner,
                "lease_pid": pid,
                "lease_expiry_utc_ms": now + stale_lease_ms,
            }
        )
        self.store.replace_operation(conn, updated)
        self.store.insert_recovery_event(
            conn,
            RecoveryEvent(
                operation_id=operation_id,
                action=RecoveryAction.RESUME,
                reason=reason,
                decided_utc_ms=now,
            ),
        )
        logger.info("delivery_resume_granted", operation_id=operation_id, lease_owner=owner, lease_pid=pid)
        return RecoverResult(
            status=RecoverStatus.OK,
            reason_code="resume_granted",
            operation_id=operation_id,
            revision=updated.revision,
            lease_owner=owner,
            state=updated.state,
        )


def _state_refusal(op: OperationRecord) -> RecoverResult | None:
    """Refuse a terminal or cancelling operation before any write (FR01 step 2)."""
    if op.state in TERMINAL_OPERATION_STATES:
        return fail_recovery(RecoverStatus.REJECTED, "operation_terminal", op)
    if op.state is OperationState.CANCEL_REQUESTED:
        return fail_recovery(RecoverStatus.REJECTED, "cancel_requested", op)
    return None


def latest_resume_grant(store: JournalStore, conn: sqlite3.Connection, operation_id: str) -> RecoveryEvent | None:
    """Return the operation's most recent recovery event iff it is a ``resume``.

    FR02 uses the audit row as the resume-grant marker rather than a new column:
    the store hard-fails on a schema-version mismatch with no migration path, so
    a column bump would strand every existing local store.
    """
    events = store.get_recovery_events(conn, operation_id)
    if not events:
        return None
    latest = events[-1]
    return latest if latest.action is RecoveryAction.RESUME else None
