"""SQLite DDL for the crash-safe delivery operation store.

Belongs to the ``tools/_delivery_journal_store.py`` module. Re-exported there
for back-compat. Extracted so ``_delivery_journal_store.py`` stays under the
350 effective-LOC gate — the multi-line ``CREATE TABLE`` DDL counts as effective
LOC (it is not a docstring), so isolating it keeps the store's method surface
readable and gate-clean.

Single source of truth for the operations/steps/queue_links/recovery_events/
tombstones schema (PRD-CORE-208 FR02).
"""

from __future__ import annotations

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    project_scope TEXT NOT NULL,
    run_identity TEXT NOT NULL DEFAULT '',
    request_digest TEXT NOT NULL,
    capability_salt TEXT NOT NULL DEFAULT '',
    capability_hash TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_utc_ms INTEGER NOT NULL,
    updated_utc_ms INTEGER NOT NULL,
    expiry_utc_ms INTEGER NOT NULL,
    lease_owner TEXT NOT NULL DEFAULT '',
    lease_pid INTEGER NOT NULL DEFAULT 0,
    lease_expiry_utc_ms INTEGER NOT NULL DEFAULT 0,
    attached_to_operation_id TEXT NOT NULL DEFAULT '',
    terminal_utc_ms INTEGER NOT NULL DEFAULT 0,
    caller_recoverable INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS steps (
    operation_id TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    state TEXT NOT NULL,
    disposition TEXT NOT NULL DEFAULT 'none',
    replay_class TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    proof_ref TEXT NOT NULL DEFAULT '',
    proof_digest TEXT NOT NULL DEFAULT '',
    finding_code TEXT NOT NULL DEFAULT '',
    updated_utc_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (operation_id, effect_id),
    FOREIGN KEY (operation_id) REFERENCES operations(operation_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS queue_links (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL UNIQUE,
    deferred_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    enqueued_utc_ms INTEGER NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES operations(operation_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS recovery_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_ref TEXT NOT NULL DEFAULT '',
    effect_id TEXT NOT NULL DEFAULT '',
    decided_utc_ms INTEGER NOT NULL,
    FOREIGN KEY (operation_id) REFERENCES operations(operation_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS tombstones (
    operation_id TEXT PRIMARY KEY,
    project_scope TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    terminal_state TEXT NOT NULL,
    findings TEXT NOT NULL DEFAULT '',
    created_utc_ms INTEGER NOT NULL,
    expiry_utc_ms INTEGER NOT NULL
);
"""
