"""Crash-safe SQLite delivery operation store — PRD-CORE-208 FR02/NFR02/NFR03.

Belongs to the ``tools/_delivery_operations.py`` facade. Owns the *only* durable
authority for operation identity, leases, steps, queue links, recovery events,
and tombstones. Separate from the learning database and never enrolled in its
transactions, so no cross-store atomicity is claimed (§6.1).

Storage contract:
- ``.trw/delivery/operations.sqlite3`` with rollback journaling,
  ``synchronous=FULL``, foreign keys, a bounded busy timeout, and owner-only
  (0600) permissions.
- ``BEGIN IMMEDIATE`` for every claim/revision transaction so concurrent writers
  serialize on one write lock without deadlock (NFR03).
- A read-only projection opens ``mode=ro`` and never creates the database (FR05).
- A single monotonic ``max_observed_utc_ms`` meta value so a backward wall-clock
  jump can never reopen an expired identifier (NFR04).
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from trw_mcp.tools._delivery_journal_schema import _SCHEMA as _SCHEMA
from trw_mcp.tools._delivery_models import (
    OperationRecord,
    QueueLink,
    QueueState,
    RecoveryEvent,
    StepRecord,
    Tombstone,
)
from trw_mcp.tools._delivery_request import DeliveryLimits
from trw_mcp.tools._delivery_rowmap import (
    row_to_operation,
    row_to_queue_link,
    row_to_recovery_event,
    row_to_step,
    row_to_tombstone,
)

SCHEMA_VERSION = DeliveryLimits.SCHEMA_VERSION
_HIGH_WATER_KEY = "max_observed_utc_ms"


class LegacyDeliveryJournalMigrationRequired(RuntimeError):
    """A read-only status call found a pre-rollback-journal database."""


class CorruptDeliveryJournalSchema(RuntimeError):
    """The delivery journal has no strictly parseable schema version."""


class UnsupportedDeliveryJournalSchema(RuntimeError):
    """The delivery journal belongs to a different schema version."""

    def __init__(self, store_schema_version: int) -> None:
        self.store_schema_version = store_schema_version
        super().__init__(f"unsupported delivery journal schema {store_schema_version}")


class JournalStore:
    """Durable operation authority. One instance owns one project's store file."""

    def __init__(self, db_path: Path, busy_timeout_ms: int = 5000) -> None:
        self.db_path = db_path
        self.busy_timeout_ms = busy_timeout_ms

    # --- connection lifecycle ---

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        # Rollback journaling keeps committed state in the main database while
        # allowing a true mode=ro status connection to participate in SQLite's
        # reader/writer locks without creating WAL sidecars.
        self._set_rollback_journal(conn)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")

    def _set_rollback_journal(self, conn: sqlite3.Connection) -> None:
        """Migrate legacy WAL stores to DELETE mode within the configured timeout."""
        deadline = time.monotonic() + max(self.busy_timeout_ms, 0) / 1000
        while True:
            try:
                row = conn.execute("PRAGMA journal_mode=DELETE").fetchone()
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(0.05, remaining))
                continue
            mode = str(row[0]).lower() if row is not None else ""
            if mode != "delete":
                raise sqlite3.OperationalError(f"delivery journal mode migration returned {mode!r}")
            return

    def connect(self) -> sqlite3.Connection:
        """Open (creating + initializing) the read-write store with 0600 perms."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:  # pragma: no cover - best effort on non-POSIX
            pass
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
        try:
            conn.execute("BEGIN IMMEDIATE")
            created = self._initialize_schema_locked(conn)
            conn.commit()
            self._apply_pragmas(conn)
            if created:
                self._harden_permissions()
            return conn
        except BaseException:
            conn.rollback()
            conn.close()
            raise

    def _initialize_schema_locked(self, conn: sqlite3.Connection) -> bool:
        """Validate or create schema while the caller holds ``BEGIN IMMEDIATE``."""
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if not str(row[0]).startswith("sqlite_")
        }
        created = not tables
        if tables:
            if "meta" not in tables:
                raise CorruptDeliveryJournalSchema("schema_version_unreadable")
            version = self.read_schema_version(conn)
            if version != SCHEMA_VERSION:
                raise UnsupportedDeliveryJournalSchema(version)
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        if created:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        return created

    def _harden_permissions(self) -> None:
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                try:
                    os.chmod(candidate, 0o600)
                except OSError:  # pragma: no cover
                    pass

    def connect_ro(self) -> sqlite3.Connection:
        """Open a read-only connection; raises ``FileNotFoundError`` if absent.

        Rollback-mode commits land in the main database, and ``mode=ro`` joins
        SQLite's native reader/writer locking without creating journal sidecars.
        This preserves both committed-state visibility and the FR05
        zero-mutation contract.
        """
        if not self.db_path.exists():
            raise FileNotFoundError(str(self.db_path))
        if self._uses_legacy_wal_mode():
            raise LegacyDeliveryJournalMigrationRequired(str(self.db_path))
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def _uses_legacy_wal_mode(self) -> bool:
        """Inspect SQLite header journal versions without opening or mutating the DB."""
        try:
            with self.db_path.open("rb") as handle:
                header = handle.read(20)
        except OSError:
            return False
        return header.startswith(b"SQLite format 3\x00") and 2 in header[18:20]

    def read_schema_version(self, conn: sqlite3.Connection) -> int:
        """Return the strict stored schema version or raise typed corruption."""
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.Error as exc:
            raise CorruptDeliveryJournalSchema("schema_version_unreadable") from exc
        if row is None:
            raise CorruptDeliveryJournalSchema("schema_version_missing")
        raw = str(row[0])
        if not raw.isascii() or not raw.isdecimal() or len(raw) > 9:
            raise CorruptDeliveryJournalSchema("schema_version_malformed")
        return int(raw)

    @contextmanager
    def immediate(self, conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
        """``BEGIN IMMEDIATE`` transaction: commit on success, rollback on error."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()

    # --- meta high-water (NFR04) ---

    def get_high_water(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (_HIGH_WATER_KEY,)).fetchone()
        return int(row["value"]) if row is not None else 0

    def advance_high_water(self, conn: sqlite3.Connection, now_ms: int) -> int:
        current = self.get_high_water(conn)
        high = max(current, now_ms)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_HIGH_WATER_KEY, str(high)),
        )
        return high

    # --- operations ---

    def get_operation(self, conn: sqlite3.Connection, operation_id: str) -> OperationRecord | None:
        row = conn.execute("SELECT * FROM operations WHERE operation_id=?", (operation_id,)).fetchone()
        return row_to_operation(row) if row is not None else None

    def insert_operation(self, conn: sqlite3.Connection, op: OperationRecord) -> None:
        conn.execute(
            "INSERT INTO operations (operation_id, project_scope, run_identity, request_digest, "
            "capability_salt, capability_hash, state, revision, created_utc_ms, updated_utc_ms, "
            "expiry_utc_ms, lease_owner, lease_pid, lease_expiry_utc_ms, attached_to_operation_id, "
            "terminal_utc_ms, caller_recoverable) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                op.operation_id,
                op.project_scope,
                op.run_identity,
                op.request_digest,
                op.capability_salt,
                op.capability_hash,
                op.state.value,
                op.revision,
                op.created_utc_ms,
                op.updated_utc_ms,
                op.expiry_utc_ms,
                op.lease_owner,
                op.lease_pid,
                op.lease_expiry_utc_ms,
                op.attached_to_operation_id,
                op.terminal_utc_ms,
                1 if op.caller_recoverable else 0,
            ),
        )

    def replace_operation(self, conn: sqlite3.Connection, op: OperationRecord) -> None:
        conn.execute(
            "UPDATE operations SET state=?, revision=?, updated_utc_ms=?, lease_owner=?, "
            "lease_pid=?, lease_expiry_utc_ms=?, attached_to_operation_id=?, terminal_utc_ms=?, "
            "caller_recoverable=? WHERE operation_id=?",
            (
                op.state.value,
                op.revision,
                op.updated_utc_ms,
                op.lease_owner,
                op.lease_pid,
                op.lease_expiry_utc_ms,
                op.attached_to_operation_id,
                op.terminal_utc_ms,
                1 if op.caller_recoverable else 0,
                op.operation_id,
            ),
        )

    # --- steps ---

    def get_steps(self, conn: sqlite3.Connection, operation_id: str) -> tuple[StepRecord, ...]:
        rows = conn.execute("SELECT * FROM steps WHERE operation_id=? ORDER BY effect_id", (operation_id,)).fetchall()
        return tuple(row_to_step(r) for r in rows)

    def get_step(self, conn: sqlite3.Connection, operation_id: str, effect_id: str) -> StepRecord | None:
        row = conn.execute(
            "SELECT * FROM steps WHERE operation_id=? AND effect_id=?", (operation_id, effect_id)
        ).fetchone()
        return row_to_step(row) if row is not None else None

    def upsert_step(self, conn: sqlite3.Connection, operation_id: str, step: StepRecord) -> None:
        conn.execute(
            "INSERT INTO steps (operation_id, effect_id, state, disposition, replay_class, attempt, "
            "proof_ref, proof_digest, finding_code, updated_utc_ms) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(operation_id, effect_id) DO UPDATE SET state=excluded.state, "
            "disposition=excluded.disposition, attempt=excluded.attempt, proof_ref=excluded.proof_ref, "
            "proof_digest=excluded.proof_digest, finding_code=excluded.finding_code, "
            "updated_utc_ms=excluded.updated_utc_ms",
            (
                operation_id,
                step.effect_id,
                step.state.value,
                step.disposition.value,
                step.replay_class.value,
                step.attempt,
                step.proof_ref,
                step.proof_digest,
                step.finding_code,
                step.updated_utc_ms,
            ),
        )

    # --- queue links ---

    def insert_queue_link(self, conn: sqlite3.Connection, link: QueueLink) -> None:
        conn.execute(
            "INSERT INTO queue_links (operation_id, deferred_digest, state, enqueued_utc_ms) VALUES (?,?,?,?)",
            (link.operation_id, link.deferred_digest, link.state.value, link.enqueued_utc_ms),
        )

    def get_queue(self, conn: sqlite3.Connection) -> tuple[QueueLink, ...]:
        rows = conn.execute("SELECT * FROM queue_links ORDER BY seq").fetchall()
        return tuple(row_to_queue_link(r, pos) for pos, r in enumerate(rows))

    def count_queue(self, conn: sqlite3.Connection, states: tuple[QueueState, ...]) -> int:
        # trw:intentional placeholders is a `?`-only join; values are bound params.
        placeholders = ",".join("?" for _ in states)
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM queue_links WHERE state IN ({placeholders})",  # noqa: S608
            tuple(s.value for s in states),
        ).fetchone()
        return int(row["c"])

    def update_queue_state(self, conn: sqlite3.Connection, operation_id: str, state: QueueState) -> None:
        conn.execute("UPDATE queue_links SET state=? WHERE operation_id=?", (state.value, operation_id))

    # --- recovery events + tombstones ---

    def insert_recovery_event(self, conn: sqlite3.Connection, event: RecoveryEvent) -> None:
        conn.execute(
            "INSERT INTO recovery_events (operation_id, action, reason, evidence_ref, effect_id, "
            "decided_utc_ms) VALUES (?,?,?,?,?,?)",
            (
                event.operation_id,
                event.action.value,
                event.reason,
                event.evidence_ref,
                event.effect_id,
                event.decided_utc_ms,
            ),
        )

    def get_recovery_events(self, conn: sqlite3.Connection, operation_id: str) -> tuple[RecoveryEvent, ...]:
        rows = conn.execute(
            "SELECT * FROM recovery_events WHERE operation_id=? ORDER BY id", (operation_id,)
        ).fetchall()
        return tuple(row_to_recovery_event(r) for r in rows)

    def insert_tombstone(self, conn: sqlite3.Connection, tombstone: Tombstone) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO tombstones (operation_id, project_scope, request_digest, "
            "terminal_state, findings, created_utc_ms, expiry_utc_ms) VALUES (?,?,?,?,?,?,?)",
            (
                tombstone.operation_id,
                tombstone.project_scope,
                tombstone.request_digest,
                tombstone.terminal_state,
                tombstone.findings,
                tombstone.created_utc_ms,
                tombstone.expiry_utc_ms,
            ),
        )

    def get_tombstone(self, conn: sqlite3.Connection, operation_id: str) -> Tombstone | None:
        row = conn.execute("SELECT * FROM tombstones WHERE operation_id=?", (operation_id,)).fetchone()
        return row_to_tombstone(row) if row is not None else None

    def delete_operation(self, conn: sqlite3.Connection, operation_id: str) -> None:
        conn.execute("DELETE FROM operations WHERE operation_id=?", (operation_id,))

    def delete_tombstone(self, conn: sqlite3.Connection, operation_id: str) -> None:
        conn.execute("DELETE FROM tombstones WHERE operation_id=?", (operation_id,))

    # --- accounting (retention/caps NFR04) ---

    def count_rows(self, conn: sqlite3.Connection) -> int:
        total = 0
        for table in ("operations", "steps", "recovery_events"):
            # trw:intentional table is a hardcoded literal from a fixed tuple, not caller input.
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()  # noqa: S608
            total += int(row["c"])
        return total

    def store_bytes(self) -> int:
        total = 0
        for suffix in ("", "-journal", "-wal", "-shm"):
            candidate = Path(str(self.db_path) + suffix)
            if candidate.exists():
                total += candidate.stat().st_size
        return total

    def iter_operations(self, conn: sqlite3.Connection) -> tuple[OperationRecord, ...]:
        rows = conn.execute("SELECT * FROM operations ORDER BY created_utc_ms").fetchall()
        return tuple(row_to_operation(r) for r in rows)

    def iter_tombstones(self, conn: sqlite3.Connection) -> tuple[Tombstone, ...]:
        rows = conn.execute("SELECT * FROM tombstones ORDER BY created_utc_ms").fetchall()
        return tuple(row_to_tombstone(r) for r in rows)
