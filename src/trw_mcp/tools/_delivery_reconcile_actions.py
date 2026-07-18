"""Capability-guarded delivery reconciliation/cancellation actions (CORE-208 FR04)."""

from __future__ import annotations

import hashlib

from trw_mcp.tools._delivery_effect_registry import get_descriptor
from trw_mcp.tools._delivery_journal_store import JournalStore
from trw_mcp.tools._delivery_models import (
    TERMINAL_OPERATION_STATES,
    OperationRecord,
    OperationState,
    QueueState,
    RecoverResult,
    RecoverStatus,
    RecoveryAction,
    RecoveryEvent,
    StepState,
)
from trw_mcp.tools._delivery_recovery import enforce_reason_bounds
from trw_mcp.tools._delivery_request import DeliveryRequestError, verify_capability


class DeliveryRecoveryActionsMixin:
    """Focused FR04 mutation mixin used by DeliveryCoordinator."""

    store: JournalStore

    @staticmethod
    def _now_ms() -> int:
        raise NotImplementedError

    def reconcile_effect(
        self,
        *,
        operation_id: str,
        effect_id: str,
        applied: bool,
        capability_token: str,
        expected_revision: int,
        reason: str,
        evidence_ref: str,
    ) -> RecoverResult:
        """Record operator proof that an indeterminate effect applied or did not."""
        conn = self.store.connect()
        try:
            now = self._now_ms()
            with self.store.immediate(conn):
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
                rejected = self._authorize_recovery(op, capability_token, expected_revision, reason, evidence_ref)
                if rejected is not None:
                    return rejected
                try:
                    descriptor = get_descriptor(effect_id)
                except KeyError:
                    return RecoverResult(
                        status=RecoverStatus.REJECTED,
                        reason_code="unregistered_effect",
                        operation_id=operation_id,
                        revision=op.revision,
                        state=op.state,
                    )
                step = self.store.get_step(conn, operation_id, effect_id)
                if step is None or step.state not in {StepState.STARTED, StepState.INDETERMINATE}:
                    return RecoverResult(
                        status=RecoverStatus.REJECTED,
                        reason_code="effect_not_reconcilable",
                        operation_id=operation_id,
                        revision=op.revision,
                        state=op.state,
                    )
                action = RecoveryAction.RECONCILE_APPLIED if applied else RecoveryAction.RECONCILE_NOT_APPLIED
                new_step = step.model_copy(
                    update={
                        "state": StepState.SUCCEEDED if applied else StepState.FAILED,
                        "proof_ref": evidence_ref,
                        "proof_digest": hashlib.sha256(evidence_ref.encode("utf-8")).hexdigest(),
                        "finding_code": "confirmed_applied" if applied else "confirmed_not_applied",
                        "updated_utc_ms": now,
                    }
                )
                self.store.upsert_step(conn, operation_id, new_step)
                self.store.insert_recovery_event(
                    conn,
                    RecoveryEvent(
                        operation_id=operation_id,
                        action=action,
                        reason=reason,
                        evidence_ref=evidence_ref,
                        effect_id=descriptor.effect_id,
                        decided_utc_ms=now,
                    ),
                )
                updated = op.model_copy(
                    update={
                        "state": OperationState.RUNNING,
                        "revision": op.revision + 1,
                        "updated_utc_ms": now,
                    }
                )
                self.store.replace_operation(conn, updated)
                return RecoverResult(
                    status=RecoverStatus.OK,
                    reason_code="confirmed_applied" if applied else "confirmed_not_applied",
                    operation_id=operation_id,
                    revision=updated.revision,
                    state=updated.state,
                )
        finally:
            conn.close()

    def request_cancel(
        self,
        *,
        operation_id: str,
        capability_token: str,
        expected_revision: int,
        reason: str,
    ) -> RecoverResult:
        """Prevent new effects and cancel immediately when none is active."""
        conn = self.store.connect()
        try:
            now = self._now_ms()
            with self.store.immediate(conn):
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
                rejected = self._authorize_recovery(op, capability_token, expected_revision, reason, "")
                if rejected is not None:
                    return rejected
                if op.state in TERMINAL_OPERATION_STATES:
                    return RecoverResult(
                        status=RecoverStatus.REJECTED,
                        reason_code="operation_terminal",
                        operation_id=operation_id,
                        revision=op.revision,
                        state=op.state,
                    )
                has_started = any(step.state is StepState.STARTED for step in self.store.get_steps(conn, operation_id))
                state = OperationState.CANCEL_REQUESTED if has_started else OperationState.CANCELLED
                updated = op.model_copy(
                    update={
                        "state": state,
                        "revision": op.revision + 1,
                        "updated_utc_ms": now,
                        "terminal_utc_ms": now if state is OperationState.CANCELLED else 0,
                    }
                )
                self.store.replace_operation(conn, updated)
                self.store.update_queue_state(conn, operation_id, QueueState.CANCELLED)
                self.store.insert_recovery_event(
                    conn,
                    RecoveryEvent(
                        operation_id=operation_id,
                        action=RecoveryAction.REQUEST_CANCEL,
                        reason=reason,
                        decided_utc_ms=now,
                    ),
                )
                return RecoverResult(
                    status=RecoverStatus.OK,
                    reason_code=state.value,
                    operation_id=operation_id,
                    revision=updated.revision,
                    state=state,
                )
        finally:
            conn.close()

    def run_compensation(
        self,
        *,
        operation_id: str,
        effect_id: str,
        capability_token: str,
        expected_revision: int,
        reason: str,
        evidence_ref: str,
    ) -> RecoverResult:
        """Reject rollback unless a reviewed compensating effect is registered.

        The current census has no safely reversible effect with a captured
        before-image.  This implemented action therefore fails closed rather
        than fabricating rollback or deleting history.
        """
        conn = self.store.connect()
        try:
            op = self.store.get_operation(conn, operation_id)
            if op is None:
                return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
            rejected = self._authorize_recovery(op, capability_token, expected_revision, reason, evidence_ref)
            if rejected is not None:
                return rejected
            try:
                get_descriptor(effect_id)
            except KeyError:
                reason_code = "unregistered_effect"
            else:
                reason_code = "no_registered_compensation"
            return RecoverResult(
                status=RecoverStatus.REJECTED,
                reason_code=reason_code,
                operation_id=operation_id,
                revision=op.revision,
                state=op.state,
            )
        finally:
            conn.close()

    @staticmethod
    def _authorize_recovery(
        operation: OperationRecord,
        capability_token: str,
        expected_revision: int,
        reason: str,
        evidence_ref: str,
    ) -> RecoverResult | None:
        """Common constant-time capability/revision/bounds gate for mutations."""
        if not reason.strip():
            return RecoverResult(
                status=RecoverStatus.REJECTED,
                reason_code="empty_reason",
                operation_id=operation.operation_id,
                revision=operation.revision,
                state=operation.state,
            )
        try:
            enforce_reason_bounds(reason, evidence_ref)
        except DeliveryRequestError as exc:
            return RecoverResult(
                status=RecoverStatus.REJECTED,
                reason_code=exc.code,
                operation_id=operation.operation_id,
                revision=operation.revision,
                state=operation.state,
            )
        if not verify_capability(capability_token, operation.capability_salt, operation.capability_hash):
            return RecoverResult(
                status=RecoverStatus.UNAUTHORIZED,
                reason_code="capability_mismatch",
                operation_id=operation.operation_id,
                revision=operation.revision,
                state=operation.state,
            )
        if expected_revision != operation.revision:
            return RecoverResult(
                status=RecoverStatus.STALE_REVISION,
                reason_code="stale_revision",
                operation_id=operation.operation_id,
                revision=operation.revision,
                state=operation.state,
            )
        return None
