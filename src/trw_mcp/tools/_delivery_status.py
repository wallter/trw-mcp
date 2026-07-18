"""Mechanically read-only delivery status projection — PRD-CORE-208 FR05.

Belongs to the ``tools/_delivery_operations.py`` facade. Pure projection logic
over a read-only :class:`JournalStore` connection: it opens ``mode=ro``, never
creates the database, refreshes a lease, sweeps retention, or appends an audit
event, and never exposes the capability hash/salt, full request digest, absolute
paths, or raw exception traces (FR05 acceptance). The public ``trw_delivery_status``
MCP tool (readOnlyHint/idempotentHint annotations) is a later wave that simply
calls :func:`build_status_projection`.
"""

from __future__ import annotations

import sqlite3

from trw_mcp.tools._delivery_effect_registry import DELIVERY_EFFECT_REGISTRY
from trw_mcp.tools._delivery_journal_store import (
    CorruptDeliveryJournalSchema,
    JournalStore,
    LegacyDeliveryJournalMigrationRequired,
)
from trw_mcp.tools._delivery_models import (
    TERMINAL_OPERATION_STATES,
    OperationRecord,
    OperationState,
    StepDisposition,
    StepRecord,
    StepState,
)
from trw_mcp.tools._delivery_request import (
    DeliveryLimits,
    DeliveryRequestError,
    validate_delivery_id,
)

_REQUEST_DIGEST_PREFIX_LEN = 12

# Non-terminal operation states that already imply the critical milestone passed.
_CRITICAL_MILESTONE_STATES = frozenset(
    {
        OperationState.CRITICAL_COMPLETE,
        OperationState.DEFERRED_QUEUED,
        OperationState.DEFERRED_RUNNING,
        OperationState.SUCCEEDED,
    }
)


def build_status_projection(
    store: JournalStore, delivery_id: str, *, now_ms: int, verbose: bool = False
) -> dict[str, object]:
    """Return a stable read-only projection for ``delivery_id`` (FR05).

    Distinct stable results: ``not_found_store`` (no database, nothing created),
    ``legacy_wal_migration_required`` (read-only upgrade preflight),
    ``unsupported_schema``, ``corrupt_store`` (unreadable meta), ``invalid_id``,
    ``tombstone``, ``not_found_id``, and ``ok``. A missing store never creates a
    directory/file.

    ``verbose=False`` (default) compacts the ``ok`` response: only steps that
    have actually run (state != ``not_started``) are enumerated, each without the
    static per-registry ``replay_class`` metadata, plus a
    ``steps_total``/``steps_started``/``steps_succeeded`` summary. ``verbose=True``
    restores the full 46-entry census with ``replay_class`` for FR05 audits — the
    underlying journal/DB truth is always complete regardless of this flag; only
    the MCP *response* shape is compacted.
    """
    try:
        conn = store.connect_ro()
    except FileNotFoundError:
        return {"result": "not_found_store", "schema_version": DeliveryLimits.SCHEMA_VERSION}
    except LegacyDeliveryJournalMigrationRequired:
        return {
            "result": "legacy_wal_migration_required",
            "schema_version": DeliveryLimits.SCHEMA_VERSION,
        }
    try:
        try:
            store_schema_version = store.read_schema_version(conn)
        except CorruptDeliveryJournalSchema:
            return {"result": "corrupt_store", "schema_version": DeliveryLimits.SCHEMA_VERSION}
        if store_schema_version != DeliveryLimits.SCHEMA_VERSION:
            return {
                "result": "unsupported_schema",
                "store_schema_version": store_schema_version,
                "supported_schema_version": DeliveryLimits.SCHEMA_VERSION,
            }
        try:
            high_water = store.get_high_water(conn)
        except Exception:
            return {"result": "corrupt_store", "schema_version": DeliveryLimits.SCHEMA_VERSION}
        effective_now = max(now_ms, high_water)
        try:
            validate_delivery_id(delivery_id, effective_now)
        except DeliveryRequestError as exc:
            return {"result": "invalid_id", "reason_code": exc.code}
        op = store.get_operation(conn, delivery_id)
        if op is None:
            tombstone = store.get_tombstone(conn, delivery_id)
            if tombstone is not None:
                return {
                    "result": "tombstone",
                    "operation_id": delivery_id,
                    "terminal_state": tombstone.terminal_state,
                    "request_digest_prefix": tombstone.request_digest[:_REQUEST_DIGEST_PREFIX_LEN],
                    "schema_version": DeliveryLimits.SCHEMA_VERSION,
                }
            return {"result": "not_found_id", "operation_id": delivery_id}
        return _project_operation(store, conn, op, effective_now, verbose=verbose)
    finally:
        conn.close()


def _build_step_view(steps: tuple[StepRecord, ...], *, verbose: bool) -> dict[str, dict[str, str]]:
    """Project step state, compact by default (PRD-CORE-208 census gate).

    Verbose mode reproduces the full 46-entry census with ``replay_class`` for
    FR05 audits. Compact mode enumerates only steps that have actually run
    (state != ``not_started``) and drops the static per-registry ``replay_class``
    metadata — a caller that needs the replay taxonomy consults the effect
    registry once, not on every status poll.
    """
    if verbose:
        step_view: dict[str, dict[str, str]] = {
            s.effect_id: {
                "state": s.state.value,
                "disposition": s.disposition.value,
                "replay_class": s.replay_class.value,
            }
            for s in steps
        }
        for effect_id, descriptor in DELIVERY_EFFECT_REGISTRY.items():
            step_view.setdefault(
                effect_id,
                {
                    "state": StepState.NOT_STARTED.value,
                    "disposition": StepDisposition.NONE.value,
                    "replay_class": descriptor.replay_class.value,
                },
            )
        return step_view
    return {
        s.effect_id: {"state": s.state.value, "disposition": s.disposition.value}
        for s in steps
        if s.state is not StepState.NOT_STARTED
    }


def _project_operation(
    store: JournalStore,
    conn: sqlite3.Connection,
    op: OperationRecord,
    now_ms: int,
    *,
    verbose: bool = False,
) -> dict[str, object]:
    steps = store.get_steps(conn, op.operation_id)
    step_view = _build_step_view(steps, verbose=verbose)
    steps_started = sum(1 for s in steps if s.state is not StepState.NOT_STARTED)
    steps_succeeded = sum(1 for s in steps if s.state is StepState.SUCCEEDED)
    queue = store.get_queue(conn)
    my_link = next((link for link in queue if link.operation_id == op.operation_id), None)
    lease_current = bool(op.lease_expiry_utc_ms) and now_ms < op.lease_expiry_utc_ms
    return {
        "result": "ok",
        "schema_version": DeliveryLimits.SCHEMA_VERSION,
        "operation_id": op.operation_id,
        "request_digest_prefix": op.request_digest[:_REQUEST_DIGEST_PREFIX_LEN],
        "state": op.state.value,
        "revision": op.revision,
        "created_utc_ms": op.created_utc_ms,
        "updated_utc_ms": op.updated_utc_ms,
        "expiry_utc_ms": op.expiry_utc_ms,
        "run_identity": op.run_identity,
        "lease_current": lease_current,
        "caller_recoverable": op.caller_recoverable,
        "attached_to_operation_id": op.attached_to_operation_id,
        "critical_complete": op.state in _CRITICAL_MILESTONE_STATES,
        "aggregate_success": op.state is OperationState.SUCCEEDED,
        "queue_disposition": my_link.state.value if my_link else "none",
        "queue_position": my_link.position if my_link else -1,
        "steps": step_view,
        "steps_total": len(DELIVERY_EFFECT_REGISTRY),
        "steps_started": steps_started,
        "steps_succeeded": steps_succeeded,
        "recovery_eligible": not lease_current and op.state not in TERMINAL_OPERATION_STATES,
    }
