"""SQLite row -> frozen-model mapping — PRD-CORE-208 (journal store helper).

Belongs to the ``tools/_delivery_operations.py`` facade via
``_delivery_journal_store.py``. Explicit column-by-column construction because
the strict/frozen Pydantic records reject silent int->bool / int->enum coercion,
so a stored ``caller_recoverable`` int and a stored enum value are converted here
rather than trusting Pydantic to guess.
"""

from __future__ import annotations

import sqlite3

from trw_mcp.tools._delivery_effect_registry import ReplayClass
from trw_mcp.tools._delivery_models import (
    OperationRecord,
    OperationState,
    QueueLink,
    QueueState,
    RecoveryAction,
    RecoveryEvent,
    StepDisposition,
    StepRecord,
    StepState,
    Tombstone,
)


def row_to_operation(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=str(row["operation_id"]),
        project_scope=str(row["project_scope"]),
        run_identity=str(row["run_identity"]),
        request_digest=str(row["request_digest"]),
        capability_salt=str(row["capability_salt"]),
        capability_hash=str(row["capability_hash"]),
        state=OperationState(str(row["state"])),
        revision=int(row["revision"]),
        created_utc_ms=int(row["created_utc_ms"]),
        updated_utc_ms=int(row["updated_utc_ms"]),
        expiry_utc_ms=int(row["expiry_utc_ms"]),
        lease_owner=str(row["lease_owner"]),
        lease_pid=int(row["lease_pid"]),
        lease_expiry_utc_ms=int(row["lease_expiry_utc_ms"]),
        attached_to_operation_id=str(row["attached_to_operation_id"]),
        terminal_utc_ms=int(row["terminal_utc_ms"]),
        caller_recoverable=bool(row["caller_recoverable"]),
    )


def row_to_step(row: sqlite3.Row) -> StepRecord:
    return StepRecord(
        effect_id=str(row["effect_id"]),
        state=StepState(str(row["state"])),
        disposition=StepDisposition(str(row["disposition"])),
        replay_class=ReplayClass(str(row["replay_class"])),
        attempt=int(row["attempt"]),
        proof_ref=str(row["proof_ref"]),
        proof_digest=str(row["proof_digest"]),
        finding_code=str(row["finding_code"]),
        updated_utc_ms=int(row["updated_utc_ms"]),
    )


def row_to_queue_link(row: sqlite3.Row, position: int) -> QueueLink:
    return QueueLink(
        operation_id=str(row["operation_id"]),
        deferred_digest=str(row["deferred_digest"]),
        state=QueueState(str(row["state"])),
        enqueued_utc_ms=int(row["enqueued_utc_ms"]),
        position=position,
    )


def row_to_recovery_event(row: sqlite3.Row) -> RecoveryEvent:
    return RecoveryEvent(
        operation_id=str(row["operation_id"]),
        action=RecoveryAction(str(row["action"])),
        reason=str(row["reason"]),
        evidence_ref=str(row["evidence_ref"]),
        effect_id=str(row["effect_id"]),
        decided_utc_ms=int(row["decided_utc_ms"]),
    )


def row_to_tombstone(row: sqlite3.Row) -> Tombstone:
    return Tombstone(
        operation_id=str(row["operation_id"]),
        project_scope=str(row["project_scope"]),
        request_digest=str(row["request_digest"]),
        terminal_state=str(row["terminal_state"]),
        findings=str(row["findings"]),
        created_utc_ms=int(row["created_utc_ms"]),
        expiry_utc_ms=int(row["expiry_utc_ms"]),
    )
