"""Durable, crash-safe delivery operation coordinator — PRD-CORE-208 (facade).

The single public import point for the delivery-operation journal. The
implementation is split into focused siblings to stay under the 350 effective-LOC
gate; this module owns claim (FR01), the operation/step state machine (FR02), the
mechanically read-only status projection (FR05), and deferred queue attach/enqueue
(FR06), and re-exports the request-identity, model, recovery, and effect-registry
symbols so callers keep one import point:

- ``_delivery_request.py`` — UUIDv7/identity/digest/capability primitives.
- ``_delivery_models.py`` — closed state enums + frozen records/results.
- ``_delivery_journal_store.py`` — SQLite authority (WAL/FULL/foreign-keys/0600).
- ``_delivery_recovery.py`` — crash recovery, stale takeover, bounded retention.
- ``_delivery_effect_registry.py`` — the executable §6.6 effect census.

Authority boundary (§6.1): this store is the ONLY authority for operation
identity, leases, steps, queue links, recovery events, and tombstones. It never
enrolls the memory/trust/telemetry stores in its transactions, so no cross-store
atomicity is claimed — each effect target's replay behavior is declared by the
registry and proven by its own wrapper.
"""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.tools._delivery_claim import commit_claim
from trw_mcp.tools._delivery_effect_registry import (
    EffectDescriptor,
    ReplayClass,
    get_descriptor,
)
from trw_mcp.tools._delivery_journal_store import JournalStore
from trw_mcp.tools._delivery_models import (
    TERMINAL_OPERATION_STATES,
    ClaimResult,
    ClaimStatus,
    OperationRecord,
    OperationState,
    QueueLink,
    QueueState,
    RecoverResult,
    RecoverStatus,
    StepDisposition,
    StepRecord,
    StepState,
)
from trw_mcp.tools._delivery_reconcile_actions import DeliveryRecoveryActionsMixin
from trw_mcp.tools._delivery_recovery import (
    apply_crash_recovery_locked,
    authorize_takeover_locked,
    process_alive,
    run_maintenance,
)
from trw_mcp.tools._delivery_request import (
    DeliveryRequestError,
    build_canonical_request,
    compute_project_scope,
    validate_capability_strength,
    validate_delivery_id,
)
from trw_mcp.tools._delivery_status import build_status_projection

__all__ = [
    "ClaimResult",
    "ClaimStatus",
    "DeliveryCoordinator",
    "DeliveryRequestError",
    "OperationState",
    "RecoverResult",
    "RecoverStatus",
    "StepState",
]

logger = structlog.get_logger(__name__)


class DeliveryCoordinator(DeliveryRecoveryActionsMixin):
    """High-level API over one project's delivery operation store."""

    def __init__(
        self,
        trw_dir: Path,
        *,
        config: TRWConfig | None = None,
        installation_identity: str | None = None,
    ) -> None:
        self._trw_dir = trw_dir
        self._config = config if config is not None else get_config()
        identity = installation_identity or _resolve_installation_identity(trw_dir)
        self.project_scope = compute_project_scope(identity)
        self._db_path = trw_dir / "delivery" / "operations.sqlite3"
        self.store = JournalStore(self._db_path, busy_timeout_ms=self._config.delivery_busy_timeout_ms)

    # --- helpers ---

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    @property
    def _stale_lease_ms(self) -> int:
        return self._config.delivery_stale_lease_minutes * 60 * 1000

    def _lease_valid(self, op: OperationRecord, now_ms: int) -> bool:
        return bool(op.lease_expiry_utc_ms) and now_ms < op.lease_expiry_utc_ms and process_alive(op.lease_pid)

    # --- FR01: claim ---

    def claim(
        self,
        *,
        delivery_id: str,
        capability_token: str,
        run_identity: str | None = None,
        skip_reflect: bool = False,
        skip_index_sync: bool = False,
        allow_unverified: bool = False,
        acceptable_failure_digest: str = "",
        owner: str = "",
        pid: int = 0,
    ) -> ClaimResult:
        """Bind ``delivery_id`` to one canonical request digest before any effect.

        A byte-equivalent repeat follows the existing operation; a changed bound
        field returns ``delivery_request_conflict`` with ``effect_calls == 0``.
        Retention maintenance and the hard-cap check run before a new claim, never
        inside status (NFR04).
        """
        conn = self.store.connect()
        try:
            now = self._now_ms()
            effective_now = max(now, self.store.get_high_water(conn))
            try:
                validate_delivery_id(delivery_id, effective_now)
                validate_capability_strength(capability_token)
                request = build_canonical_request(
                    project_scope=self.project_scope,
                    run_identity=run_identity,
                    skip_reflect=skip_reflect,
                    skip_index_sync=skip_index_sync,
                    allow_unverified=allow_unverified,
                    acceptable_failure_digest=acceptable_failure_digest,
                )
            except DeliveryRequestError as exc:
                return ClaimResult(status=ClaimStatus.REJECTED, reason_code=exc.code, effect_calls=0)

            run_maintenance(self.store, conn, effective_now)
            return commit_claim(
                self.store,
                conn,
                delivery_id=delivery_id,
                capability_token=capability_token,
                request_digest=request.digest(),
                run_identity=request.run_identity,
                project_scope=self.project_scope,
                owner=owner,
                pid=pid,
                effective_now=effective_now,
                stale_lease_ms=self._stale_lease_ms,
            )
        finally:
            conn.close()

    # --- FR02: step state machine ---

    def begin_step(self, operation_id: str, effect_id: str, *, owner: str = "", pid: int = 0) -> StepRecord:
        """Durably transition a step to ``started`` and heartbeat the lease.

        Committed BEFORE the effect runs so a crash after the effect can never be
        read as ``not_started`` (FR02 invariant §6.3.5).
        """
        descriptor = get_descriptor(effect_id)
        conn = self.store.connect()
        try:
            now = self._now_ms()
            with self.store.immediate(conn):
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    raise DeliveryRequestError("unknown_operation", "operation not found")
                if op.state in TERMINAL_OPERATION_STATES:
                    raise DeliveryRequestError("illegal_transition", "operation already terminal")
                if op.state is OperationState.CANCEL_REQUESTED:
                    raise DeliveryRequestError("cancel_requested", "operation cancellation prevents new effects")
                prior = self.store.get_step(conn, operation_id, effect_id)
                step = StepRecord(
                    effect_id=effect_id,
                    state=StepState.STARTED,
                    replay_class=descriptor.replay_class,
                    attempt=(prior.attempt if prior else 0) + 1,
                    updated_utc_ms=now,
                )
                self.store.upsert_step(conn, operation_id, step)
                self.store.replace_operation(
                    conn,
                    op.model_copy(
                        update={
                            "state": OperationState.RUNNING if op.state is OperationState.PENDING else op.state,
                            "revision": op.revision + 1,
                            "updated_utc_ms": now,
                            "lease_owner": owner or op.lease_owner,
                            "lease_pid": pid or op.lease_pid,
                            "lease_expiry_utc_ms": now + self._stale_lease_ms,
                        }
                    ),
                )
            return step
        finally:
            conn.close()

    def finalize_step(
        self,
        operation_id: str,
        effect_id: str,
        *,
        state: StepState,
        proof_digest: str = "",
        proof_ref: str = "",
        disposition: StepDisposition = StepDisposition.NONE,
        finding_code: str = "",
    ) -> StepRecord:
        """Capture proof and commit the terminal step transition AFTER the effect."""
        if state is StepState.STARTED or state is StepState.NOT_STARTED:
            raise DeliveryRequestError("illegal_transition", "finalize requires a terminal step state")
        conn = self.store.connect()
        try:
            now = self._now_ms()
            with self.store.immediate(conn):
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    raise DeliveryRequestError("unknown_operation", "operation not found")
                prior = self.store.get_step(conn, operation_id, effect_id)
                step = StepRecord(
                    effect_id=effect_id,
                    state=state,
                    disposition=disposition,
                    replay_class=get_descriptor(effect_id).replay_class,
                    attempt=prior.attempt if prior else 1,
                    proof_ref=proof_ref,
                    proof_digest=proof_digest,
                    finding_code=finding_code,
                    updated_utc_ms=now,
                )
                self.store.upsert_step(conn, operation_id, step)
                remaining_started = any(
                    candidate.effect_id != effect_id and candidate.state is StepState.STARTED
                    for candidate in self.store.get_steps(conn, operation_id)
                )
                cancelled = op.state is OperationState.CANCEL_REQUESTED and not remaining_started
                self.store.replace_operation(
                    conn,
                    op.model_copy(
                        update={
                            "state": OperationState.CANCELLED if cancelled else op.state,
                            "revision": op.revision + 1,
                            "updated_utc_ms": now,
                            "terminal_utc_ms": now if cancelled else op.terminal_utc_ms,
                        }
                    ),
                )
            return step
        finally:
            conn.close()

    def mark_operation_state(self, operation_id: str, state: OperationState) -> OperationRecord:
        """Commit an operation-level state transition with a revision bump (FR02)."""
        conn = self.store.connect()
        try:
            now = self._now_ms()
            with self.store.immediate(conn):
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    raise DeliveryRequestError("unknown_operation", "operation not found")
                terminal = state in TERMINAL_OPERATION_STATES
                updated = op.model_copy(
                    update={
                        "state": state,
                        "revision": op.revision + 1,
                        "updated_utc_ms": now,
                        "terminal_utc_ms": now if terminal else op.terminal_utc_ms,
                    }
                )
                self.store.replace_operation(conn, updated)
            return updated
        finally:
            conn.close()

    # --- FR06: deferred singleflight ---

    def enqueue_deferred(self, operation_id: str, deferred_digest: str) -> QueueLink:
        """Attach to a running batch with an equal digest, else durably FIFO-queue.

        Raises ``deferred_queue_full`` (as ``DeliveryRequestError``) at the bounded
        depth; existing work is never dropped (FR06/NFR04).
        """
        conn = self.store.connect()
        try:
            now = self._now_ms()
            with self.store.immediate(conn):
                active = [
                    link
                    for link in self.store.get_queue(conn)
                    if link.state in {QueueState.RUNNING, QueueState.ATTACHED}
                ]
                match = next((link for link in active if link.deferred_digest == deferred_digest), None)
                state = QueueState.ATTACHED if match is not None else QueueState.QUEUED
                if state is QueueState.QUEUED:
                    depth = self.store.count_queue(conn, (QueueState.QUEUED,))
                    if depth >= self._config.delivery_queue_depth_max:
                        raise DeliveryRequestError("deferred_queue_full", "deferred FIFO queue is full")
                link = QueueLink(
                    operation_id=operation_id, deferred_digest=deferred_digest, state=state, enqueued_utc_ms=now
                )
                self.store.insert_queue_link(conn, link)
                if match is not None:
                    attached_op = self.store.get_operation(conn, operation_id)
                    if attached_op is not None:
                        self.store.replace_operation(
                            conn,
                            attached_op.model_copy(
                                update={"attached_to_operation_id": match.operation_id, "updated_utc_ms": now}
                            ),
                        )
            return link
        finally:
            conn.close()

    # --- FR04: crash recovery / takeover ---

    def recover_after_crash(self, operation_id: str) -> RecoverResult:
        """Reconcile a restarted operation whose lease is stale/absent (FR04)."""
        conn = self.store.connect()
        try:
            with self.store.immediate(conn):
                now = self._now_ms()
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
                if self._lease_valid(op, now):
                    return RecoverResult(
                        status=RecoverStatus.LIVE_OWNER,
                        reason_code="lease_still_live",
                        operation_id=operation_id,
                        revision=op.revision,
                        state=op.state,
                    )
                return apply_crash_recovery_locked(self.store, conn, op, now)
        finally:
            conn.close()

    def takeover(
        self,
        *,
        operation_id: str,
        capability_token: str,
        expected_revision: int,
        reason: str,
        new_owner: str,
        new_pid: int,
        owner_alive: bool | None = None,
    ) -> RecoverResult:
        """Capability + revision + liveness-checked stale takeover (FR04/US-003)."""
        conn = self.store.connect()
        try:
            with self.store.immediate(conn):
                now = self._now_ms()
                op = self.store.get_operation(conn, operation_id)
                if op is None:
                    return RecoverResult(status=RecoverStatus.NOT_FOUND, reason_code="unknown_operation")
                return authorize_takeover_locked(
                    self.store,
                    conn,
                    operation=op,
                    capability_token=capability_token,
                    expected_revision=expected_revision,
                    reason=reason,
                    new_owner=new_owner,
                    new_pid=new_pid,
                    now_ms=now,
                    stale_lease_ms=self._stale_lease_ms,
                    owner_alive=owner_alive,
                )
        finally:
            conn.close()

    # --- NFR04: explicit maintenance path ---

    def run_maintenance(self) -> dict[str, int]:
        """Explicit retention/compaction path (never inside status). Returns counts."""
        conn = self.store.connect()
        try:
            return run_maintenance(self.store, conn, max(self._now_ms(), self.store.get_high_water(conn)))
        finally:
            conn.close()

    # --- FR05: mechanically read-only status projection ---

    def project_status(self, delivery_id: str, *, verbose: bool = False) -> dict[str, object]:
        """Read-only operation projection via SQLite ``mode=ro`` (FR05).

        Delegates to :func:`build_status_projection`, which opens the store
        read-only and NEVER creates it, refreshes a lease, sweeps retention, or
        appends an audit event, and never exposes the capability hash/salt, full
        request digest, absolute paths, or raw traces.

        ``verbose=False`` compacts the ``ok`` response step census (see
        :func:`build_status_projection`); ``verbose=True`` returns the full
        46-entry projection for audits.
        """
        return build_status_projection(self.store, delivery_id, now_ms=self._now_ms(), verbose=verbose)


def _resolve_installation_identity(trw_dir: Path) -> str:
    """Stable installation identity from the project root name (never abs path)."""
    project_root = trw_dir.parent
    return project_root.name or "trw-project"


# Re-exports for downstream single-import-point convenience.
__all__ += ["DeliveryCoordinator", "EffectDescriptor", "ReplayClass", "get_descriptor"]
