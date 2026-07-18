"""Caller-stable claim commit — PRD-CORE-208 FR01 (claim transaction).

Belongs to the ``tools/_delivery_operations.py`` facade. The single
``BEGIN IMMEDIATE`` transaction that binds one ``delivery_id`` to one canonical
request digest before any delivery mutation. Split out of the coordinator so the
facade stays under the 350 effective-LOC gate.

Truth table (FR01 acceptance):
- same request digest + verifying capability  -> ``EXISTING`` (follow operation).
- any bound-field / capability change          -> ``CONFLICT`` (zero effects).
- terminal tombstone, matching digest          -> ``EXISTING`` (terminal, no rerun).
- terminal tombstone, different digest          -> ``CONFLICT`` (reuse rejected).
- store over a hard cap                          -> ``STORE_FULL`` (no new claim).
"""

from __future__ import annotations

import sqlite3

from trw_mcp.tools._delivery_journal_store import JournalStore
from trw_mcp.tools._delivery_models import (
    ClaimResult,
    ClaimStatus,
    OperationRecord,
    OperationState,
)
from trw_mcp.tools._delivery_recovery import over_hard_caps
from trw_mcp.tools._delivery_request import DeliveryLimits, hash_capability, verify_capability


def commit_claim(
    store: JournalStore,
    conn: sqlite3.Connection,
    *,
    delivery_id: str,
    capability_token: str,
    request_digest: str,
    run_identity: str,
    project_scope: str,
    owner: str,
    pid: int,
    effective_now: int,
    stale_lease_ms: int,
) -> ClaimResult:
    """Run the FR01 claim in one IMMEDIATE transaction. Never executes an effect."""
    with store.immediate(conn):
        store.advance_high_water(conn, effective_now)
        existing = store.get_operation(conn, delivery_id)
        if existing is not None:
            if existing.request_digest == request_digest and verify_capability(
                capability_token, existing.capability_salt, existing.capability_hash
            ):
                return ClaimResult(
                    status=ClaimStatus.EXISTING,
                    reason_code="idempotent_same_request",
                    operation_id=existing.operation_id,
                    revision=existing.revision,
                    state=existing.state,
                    effect_calls=0,
                )
            return ClaimResult(status=ClaimStatus.CONFLICT, reason_code="delivery_request_conflict", effect_calls=0)

        tombstone = store.get_tombstone(conn, delivery_id)
        if tombstone is not None:
            same = tombstone.request_digest == request_digest
            return ClaimResult(
                status=ClaimStatus.EXISTING if same else ClaimStatus.CONFLICT,
                reason_code="tombstoned_terminal" if same else "delivery_request_conflict",
                operation_id=delivery_id,
                effect_calls=0,
            )

        if over_hard_caps(store, conn):
            return ClaimResult(status=ClaimStatus.STORE_FULL, reason_code="delivery_store_full", effect_calls=0)

        salt, cap_hash = hash_capability(capability_token)
        op = OperationRecord(
            operation_id=delivery_id,
            project_scope=project_scope,
            run_identity=run_identity,
            request_digest=request_digest,
            capability_salt=salt,
            capability_hash=cap_hash,
            state=OperationState.PENDING,
            revision=1,
            created_utc_ms=effective_now,
            updated_utc_ms=effective_now,
            expiry_utc_ms=effective_now + DeliveryLimits.TOMBSTONE_TTL_MS,
            lease_owner=owner,
            lease_pid=pid,
            lease_expiry_utc_ms=effective_now + stale_lease_ms if owner else 0,
            caller_recoverable=True,
        )
        store.insert_operation(conn, op)
        return ClaimResult(
            status=ClaimStatus.CLAIMED,
            reason_code="claimed",
            operation_id=delivery_id,
            revision=1,
            state=OperationState.PENDING,
            effect_calls=0,
        )
