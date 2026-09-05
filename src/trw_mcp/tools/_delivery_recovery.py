"""Crash recovery, stale takeover, and bounded retention — PRD-CORE-208 FR04/NFR04.

Belongs to the ``tools/_delivery_operations.py`` facade. Pure state-machine logic
over a :class:`JournalStore` connection; it never registers an MCP tool (the
public ``trw_delivery_recover`` wiring is a separate wave). Two invariants are
load-bearing and must never soften:

- **Never blindly replay a ``NON_REPLAYABLE`` started step.** On crash recovery it
  becomes ``indeterminate`` and blocks automatic retry (FR04 / US-002). This is
  what prevents a duplicated trust increment, external send, or destructive purge
  after a killed delivery.
- **Stale takeover is authorized, not time-triggered.** Capability hash (constant
  time), exact request binding, expected revision, >=15-minute stale lease, a
  dead/missing owner, and a bounded reason are ALL required before a new lease
  commits (FR04 / US-003).
"""

from __future__ import annotations

import os
import sqlite3

import structlog

from trw_mcp.tools._delivery_effect_registry import ReplayClass, is_auto_replayable_after_started
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
    StepRecord,
    StepState,
    Tombstone,
)
from trw_mcp.tools._delivery_request import (
    DeliveryLimits,
    DeliveryRequestError,
    verify_capability,
)

logger = structlog.get_logger(__name__)


def process_alive(pid: int) -> bool:
    """True iff ``pid`` names a live process. PID 0/absent counts as dead."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists but owned by another user
        return True
    except OSError:  # pragma: no cover - defensive
        return False
    return True


def classify_restart(step: StepRecord) -> str:
    """Map a ``started`` step to a restart decision by replay class (§6.4).

    Returns ``"indeterminate"`` (never auto-replay), ``"prove_then_replay"``
    (postcondition-provable), or ``"replay_safe"`` for the transactional /
    keyed-idempotent / diagnostic / coordination classes.
    """
    if not is_auto_replayable_after_started(step.effect_id):
        return "indeterminate"
    if step.replay_class is ReplayClass.POSTCONDITION_PROVABLE:
        return "prove_then_replay"
    return "replay_safe"


def apply_crash_recovery_locked(
    store: JournalStore,
    conn: sqlite3.Connection,
    operation: OperationRecord,
    now_ms: int,
) -> RecoverResult:
    """Reconcile a restarted operation whose ``started`` steps lost their lease.

    Every ``NON_REPLAYABLE`` started step becomes ``indeterminate`` and is
    recorded; no product effect is auto-invoked here. Replay-safe classes are
    reported (their re-execution is the caller's registered wrapper job, still
    keyed/idempotent). The operation is left ``indeterminate`` if any step is
    indeterminate, else its prior state is preserved.
    The caller must hold ``store.immediate(conn)`` so operation and step reads
    share the same locked snapshot as every write.
    """
    steps = store.get_steps(conn, operation.operation_id)
    indeterminate: list[str] = []
    replay_safe: list[str] = []
    for step in steps:
        if step.state is not StepState.STARTED:
            continue
        decision = classify_restart(step)
        if decision == "indeterminate":
            store.upsert_step(
                conn,
                operation.operation_id,
                step.model_copy(
                    update={
                        "state": StepState.INDETERMINATE,
                        "finding_code": "crash_nonreplayable",
                        "updated_utc_ms": now_ms,
                    }
                ),
            )
            indeterminate.append(step.effect_id)
        else:
            replay_safe.append(step.effect_id)
    new_state = OperationState.INDETERMINATE if indeterminate else operation.state
    updated = operation.model_copy(
        update={
            "state": new_state,
            "revision": operation.revision + 1,
            "updated_utc_ms": now_ms,
            "lease_owner": "",
            "lease_pid": 0,
            "lease_expiry_utc_ms": 0,
        }
    )
    store.replace_operation(conn, updated)
    store.insert_recovery_event(
        conn,
        RecoveryEvent(
            operation_id=operation.operation_id,
            action=RecoveryAction.RECONCILE_NOT_APPLIED,
            reason="crash_recovery_evaluated",
            decided_utc_ms=now_ms,
        ),
    )
    return RecoverResult(
        status=RecoverStatus.OK,
        reason_code="crash_recovered",
        operation_id=operation.operation_id,
        revision=updated.revision,
        state=updated.state,
        replayed_effect_ids=tuple(sorted(replay_safe)),
        indeterminate_effect_ids=tuple(sorted(indeterminate)),
    )


def lease_takeover_refusal(
    operation: OperationRecord,
    *,
    now_ms: int,
    stale_lease_ms: int,
    owner_alive: bool | None = None,
    self_owned_pid: int = 0,
) -> RecoverResult | None:
    """The staleness + liveness guard every lease-granting action shares (FR04).

    ``None`` means the lease may be taken. One implementation, two callers
    (:func:`authorize_takeover_locked` and the PRD-FIX-127 ``resume`` action):
    NFR02 requires resume to reuse takeover's authority, and a verbatim copy would
    let the two drift apart the first time one is tightened.

    ``self_owned_pid`` names a process that is not a FOREIGN live owner — you
    cannot steal a lease from yourself. ``resume`` passes its own pid so the
    ordinary "the gate blocked, fix the evidence, retry under the same delivery
    id" flow works in one server process; ``takeover_pending`` passes nothing and
    keeps its behaviour exactly. This never softens the staleness floor, which is
    checked FIRST: a lease a live batch is heartbeating is still fresh, so a
    same-pid caller is refused there before liveness is ever consulted.
    """
    if operation.lease_expiry_utc_ms and now_ms < operation.lease_expiry_utc_ms:
        return fail_recovery(RecoverStatus.NOT_STALE, "lease_still_fresh", operation)
    lease_age = now_ms - operation.lease_expiry_utc_ms if operation.lease_expiry_utc_ms else now_ms
    if lease_age < stale_lease_ms:
        return fail_recovery(RecoverStatus.NOT_STALE, "lease_not_stale_enough", operation)
    if owner_alive is None:
        alive = (
            bool(operation.lease_pid) and operation.lease_pid != self_owned_pid and process_alive(operation.lease_pid)
        )
    else:
        alive = owner_alive
    if alive:
        return fail_recovery(RecoverStatus.LIVE_OWNER, "owner_process_alive", operation)
    return None


def authorize_takeover_locked(
    store: JournalStore,
    conn: sqlite3.Connection,
    *,
    operation: OperationRecord,
    capability_token: str,
    expected_revision: int,
    reason: str,
    new_owner: str,
    new_pid: int,
    now_ms: int,
    stale_lease_ms: int,
    owner_alive: bool | None = None,
) -> RecoverResult:
    """Validate and commit an FR04 stale-pending takeover (US-003).

    All checks must pass before a new lease commits; any failure returns a stable
    status and leaves the lease owner unchanged (zero effect). ``owner_alive`` may
    be injected for deterministic tests; otherwise liveness is probed from the
    stored PID.
    The caller must hold ``store.immediate(conn)`` across the operation read,
    these checks, and the writes below.
    """
    if not reason or len(reason) > DeliveryLimits.MAX_REASON_CHARS:
        return fail_recovery(RecoverStatus.REJECTED, "invalid_reason", operation)
    if not verify_capability(capability_token, operation.capability_salt, operation.capability_hash):
        return fail_recovery(RecoverStatus.UNAUTHORIZED, "capability_mismatch", operation)
    if expected_revision != operation.revision:
        return fail_recovery(RecoverStatus.STALE_REVISION, "revision_mismatch", operation)
    refusal = lease_takeover_refusal(operation, now_ms=now_ms, stale_lease_ms=stale_lease_ms, owner_alive=owner_alive)
    if refusal is not None:
        return refusal

    updated = operation.model_copy(
        update={
            "revision": operation.revision + 1,
            "updated_utc_ms": now_ms,
            "lease_owner": new_owner,
            "lease_pid": new_pid,
            "lease_expiry_utc_ms": now_ms + stale_lease_ms,
        }
    )
    store.replace_operation(conn, updated)
    store.insert_recovery_event(
        conn,
        RecoveryEvent(
            operation_id=operation.operation_id,
            action=RecoveryAction.TAKEOVER_PENDING,
            reason=reason,
            decided_utc_ms=now_ms,
        ),
    )
    return RecoverResult(
        status=RecoverStatus.OK,
        reason_code="takeover_granted",
        operation_id=updated.operation_id,
        revision=updated.revision,
        lease_owner=new_owner,
        state=updated.state,
    )


def fail_recovery(status: RecoverStatus, code: str, operation: OperationRecord) -> RecoverResult:
    """Refusal projection that leaves the lease owner and every step unchanged."""
    return RecoverResult(
        status=status,
        reason_code=code,
        operation_id=operation.operation_id,
        revision=operation.revision,
        lease_owner=operation.lease_owner,
        state=operation.state,
    )


def terminal_queue_state(op_state: OperationState) -> QueueState:
    """Retirement state for a terminal operation's deferred queue link (FR06).

    A cancelled operation retires its link as ``CANCELLED``; a succeeded/failed
    operation retires it as ``DONE``. Either way the link leaves the ``QUEUED``
    state so it no longer counts against the bounded FIFO depth.
    """
    return QueueState.CANCELLED if op_state is OperationState.CANCELLED else QueueState.DONE


def reap_orphaned_queue_links(
    store: JournalStore,
    conn: sqlite3.Connection,
    now_ms: int,
    stale_lease_ms: int,
) -> int:
    """Retire ``QUEUED`` links that no live process will ever drain (FR06 self-heal).

    A ``QUEUED`` link is reaped iff its owning operation is missing or already
    terminal (a completed op left its link behind), OR the link is older than the
    stale-lease window AND its operation holds no live lease. A link whose
    operation holds a live lease, or that is still within the stale window under a
    non-terminal operation, is NEVER reaped — live deferred work is never dropped
    (FR06/NFR04). Must run inside the caller's IMMEDIATE transaction; returns the
    number of links retired so the reap is observable, never silent.
    """
    terminal_reaped = 0
    stale_reaped = 0
    for link in store.get_queue(conn):
        if link.state is not QueueState.QUEUED:
            continue
        op = store.get_operation(conn, link.operation_id)
        if op is not None and op.state not in TERMINAL_OPERATION_STATES:
            live_lease = (
                bool(op.lease_expiry_utc_ms) and now_ms < op.lease_expiry_utc_ms and process_alive(op.lease_pid)
            )
            if live_lease or now_ms - link.enqueued_utc_ms < stale_lease_ms:
                continue
            store.update_queue_state(conn, link.operation_id, QueueState.CANCELLED)
            stale_reaped += 1
        else:
            retire = terminal_queue_state(op.state) if op is not None else QueueState.CANCELLED
            store.update_queue_state(conn, link.operation_id, retire)
            terminal_reaped += 1
    reaped = terminal_reaped + stale_reaped
    if reaped:
        logger.info(
            "delivery_queue_links_reaped",
            terminal_reaped=terminal_reaped,
            stale_reaped=stale_reaped,
        )
    return reaped


def run_maintenance(
    store: JournalStore,
    conn: sqlite3.Connection,
    now_ms: int,
    stale_lease_ms: int = 0,
) -> dict[str, int]:
    """Compact/expire per the fixed v1 lifecycle table (NFR04).

    Runs inside its own IMMEDIATE transaction: terminal full records tombstone at
    30 days, unresolved full records at 90 days, and tombstones/IDs delete at the
    180-day horizon. Compaction preserves request/terminal digests so a deleted
    row can never silently reopen an old identifier. A stale-``QUEUED`` link sweep
    runs in the same transaction so orphaned deferred links never accumulate
    against the FIFO depth. Returns counts for evidence.
    """
    compacted = 0
    expired = 0
    tombstoned_unresolved = 0
    with store.immediate(conn):
        for op in store.iter_operations(conn):
            horizon = _acceptance_expiry(op)
            if op.state in {OperationState.SUCCEEDED, OperationState.FAILED, OperationState.CANCELLED}:
                if op.terminal_utc_ms and now_ms - op.terminal_utc_ms >= DeliveryLimits.TERMINAL_FULL_RETENTION_MS:
                    _compact(store, conn, op, op.state.value, horizon)
                    compacted += 1
            elif now_ms - op.created_utc_ms >= DeliveryLimits.UNRESOLVED_FULL_RETENTION_MS:
                _compact(store, conn, op, "expired_indeterminate", horizon)
                tombstoned_unresolved += 1
        for tombstone in store.iter_tombstones(conn):
            if now_ms >= tombstone.expiry_utc_ms:
                store.delete_tombstone(conn, tombstone.operation_id)
                expired += 1
        reaped_queue_links = reap_orphaned_queue_links(store, conn, now_ms, stale_lease_ms)
    return {
        "compacted_terminal": compacted,
        "tombstoned_unresolved": tombstoned_unresolved,
        "expired_tombstones": expired,
        "reaped_queue_links": reaped_queue_links,
    }


def _acceptance_expiry(op: OperationRecord) -> int:
    """Tombstone expiry = the identifier's own 180-day acceptance horizon."""
    return op.created_utc_ms + DeliveryLimits.TOMBSTONE_TTL_MS


def _compact(
    store: JournalStore,
    conn: sqlite3.Connection,
    op: OperationRecord,
    terminal_state: str,
    expiry_utc_ms: int,
) -> None:
    findings = ";".join(
        f"{s.effect_id}:{s.state.value}" for s in store.get_steps(conn, op.operation_id) if s.finding_code
    )
    store.insert_tombstone(
        conn,
        Tombstone(
            operation_id=op.operation_id,
            project_scope=op.project_scope,
            request_digest=op.request_digest,
            terminal_state=terminal_state,
            findings=findings[: DeliveryLimits.MAX_EVIDENCE_REF_CHARS],
            created_utc_ms=op.created_utc_ms,
            expiry_utc_ms=expiry_utc_ms,
        ),
    )
    store.delete_operation(conn, op.operation_id)  # cascades steps/queue/recovery


def over_hard_caps(store: JournalStore, conn: sqlite3.Connection) -> bool:
    """True iff the store still exceeds the 64 MiB or 20k-row hard cap (NFR04)."""
    return (
        store.store_bytes() > DeliveryLimits.STORE_MAX_BYTES or store.count_rows(conn) >= DeliveryLimits.STORE_MAX_ROWS
    )


def enforce_reason_bounds(reason: str, evidence_ref: str) -> None:
    """Reject an oversize recovery reason / evidence reference before write (NFR04)."""
    if len(reason) > DeliveryLimits.MAX_REASON_CHARS:
        raise DeliveryRequestError("oversize_reason", "recovery reason exceeds 500 characters")
    if len(evidence_ref) > DeliveryLimits.MAX_EVIDENCE_REF_CHARS:
        raise DeliveryRequestError("oversize_evidence", "evidence reference exceeds 1024 characters")
