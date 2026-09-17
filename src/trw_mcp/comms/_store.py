"""Fail-closed SQLite persistence for comms (PRD-CORE-274-NFR02, NFR03, FR09).

One database per formation, beside the canonical manifest. Transactional
updates use ``BEGIN IMMEDIATE``; initialization uses atomic no-overwrite
publication. No application advisory file lock carries correctness.

Every pragma is READ BACK after being set. Requesting a pragma is not the same
as getting one — SQLite silently ignores ``journal_mode`` changes inside a
transaction — and a durability setting believed-but-not-verified is worse than
a known-absent one.

Nothing here deletes, recreates or "repairs" storage. A schema mismatch or
corrupt database REFUSES and preserves the file, because the evidence in a
damaged mailbox is worth more than the convenience of a fresh one.
"""

from __future__ import annotations

import math
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from enum import Enum
from pathlib import Path

from trw_mcp.comms._schema import SCHEMA as _SCHEMA

#: Bumped only for an incompatible on-disk change. A mismatch refuses; it never
#: migrates silently, because a silent migration of a shared mailbox loses the
#: evidence of what the other side thought it had written.
from trw_mcp.comms._schema import SCHEMA_VERSION as SCHEMA_VERSION
from trw_mcp.comms._schema import SchemaVersionError, verify

#: Beside the canonical formation manifest (FR10), so a group's mailbox is
#: located by the same path that derives its group_id.
DATABASE_FILENAME = "comms.sqlite3"

_REQUIRED_PRAGMAS = {"journal_mode": "delete", "synchronous": 2}


class StoreRefusal(str, Enum):
    """Closed refusal vocabulary for persistence."""

    SCHEMA_MISMATCH = "schema_version_mismatch"
    CORRUPT = "storage_corrupt"
    PRAGMA_UNAPPLIED = "pragma_not_applied"
    CONTENDED = "storage_contended"
    CLOSED_GROUP = "group_closed"
    UNAVAILABLE = "storage_unavailable"
    PUBLISH_UNCERTAIN = "storage_publication_uncertain"


class StoreError(RuntimeError):
    """Raised on any refusal. ``retryable`` distinguishes contention from damage."""

    def __init__(self, refusal: StoreRefusal, detail: str) -> None:
        super().__init__(f"{refusal.value}: {detail}")
        self.refusal = refusal
        self.detail = detail
        self.retryable = refusal is StoreRefusal.CONTENDED


def database_path(manifest_path: Path) -> Path:
    """The mailbox for the formation whose manifest is *manifest_path*."""

    return manifest_path.parent / DATABASE_FILENAME


def _apply_and_verify_pragmas(conn: sqlite3.Connection, *, busy_timeout_ms: int) -> None:
    """Set the durability pragmas, then read each one back and refuse on drift.

    ``busy_timeout`` is set FIRST and the order is load-bearing. ``journal_mode``
    takes a lock, so setting it before the timeout makes it wait under whatever
    timeout the connection was constructed with — measured as a 5s stall under a
    configured 20ms bound, i.e. the configured value silently not applying to the
    first thing that could block. The construction-time ``timeout`` in
    :func:`connect` closes the same gap for anything earlier still.
    """

    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    for pragma, expected in _REQUIRED_PRAGMAS.items():
        row = conn.execute(f"PRAGMA {pragma}").fetchone()
        actual = row[0] if row else None
        normalized = actual.lower() if isinstance(actual, str) else actual
        if normalized != expected:
            raise StoreError(
                StoreRefusal.PRAGMA_UNAPPLIED,
                f"{pragma} is {actual!r}, expected {expected!r}; durability cannot be assumed",
            )
    row = conn.execute("PRAGMA busy_timeout").fetchone()
    if row is None or int(row[0]) != int(busy_timeout_ms):
        raise StoreError(
            StoreRefusal.PRAGMA_UNAPPLIED,
            f"busy_timeout is {row[0] if row else None!r}, expected {busy_timeout_ms}",
        )


def _prepare_schema(conn: sqlite3.Connection) -> None:
    """Initialize only an exclusively owned staging file, in one transaction."""
    with immediate(conn):
        for statement in _SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version',?)", (str(SCHEMA_VERSION),))


def _verify_schema(conn: sqlite3.Connection) -> None:
    """Read-only schema and accounting validation; never repair evidence."""
    try:
        verify(conn)
    except SchemaVersionError as exc:
        raise StoreError(StoreRefusal.SCHEMA_MISMATCH, str(exc)) from exc
    except (ValueError, TypeError, OverflowError) as exc:
        raise StoreError(StoreRefusal.CORRUPT, "schema or accounting inconsistency") from exc


def validate_operation(conn: sqlite3.Connection) -> None:
    """Repeat validation after obtaining the operation lock, not only on open."""
    if not conn.in_transaction:
        raise RuntimeError("operation validation requires transaction")
    _verify_schema(conn)


def _publish_new(path: Path, busy_timeout_ms: int) -> None:
    """Publish a complete DB without replacing a concurrently created winner.

    Hard-link creation is the no-overwrite primitive, not an advisory lock. If
    unavailable on the local filesystem/platform, refuse; never substitute a
    racy exists+rename fallback. Directory fsync follows publication on POSIX;
    platforms lacking that primitive fail closed, not silently degrade. No
    all-platform power-loss certification or automatic crash-orphan GC is claimed.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=".comms-initializing-", suffix=".sqlite3", dir=path.parent)
    os.close(descriptor)
    staging = Path(temporary)
    try:
        conn = sqlite3.connect(staging, timeout=busy_timeout_ms / 1000.0, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            _apply_and_verify_pragmas(conn, busy_timeout_ms=busy_timeout_ms)
            _prepare_schema(conn)
            _verify_schema(conn)
        finally:
            conn.close()
        if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
            raise StoreError(StoreRefusal.UNAVAILABLE, "durable directory publication unsupported on this platform")
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        published = False
        try:
            try:
                os.link(staging, path)  # No overwrite; only a complete DB becomes canonical.
                published = True
            except FileExistsError:
                # trw-fail-silent-allow: another publisher won the no-overwrite race; open_store validates that winner (regular nonempty file + _verify_schema) before any caller uses it
                pass
            except (OSError, NotImplementedError) as exc:
                raise StoreError(StoreRefusal.UNAVAILABLE, "atomic no-overwrite publication unavailable") from exc
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                refusal = StoreRefusal.PUBLISH_UNCERTAIN if published else StoreRefusal.UNAVAILABLE
                raise StoreError(
                    refusal, "directory durability failed; canonical mailbox retained if published"
                ) from exc
        finally:
            os.close(directory_fd)
    finally:
        # Only this call's exclusive temporary files are owned; never delete canonical storage.
        primary_error = sys.exc_info()[1]
        try:
            staging.unlink(missing_ok=True)
            staging.with_name(staging.name + "-journal").unlink(missing_ok=True)
        except OSError as cleanup_error:
            if primary_error is not None:
                raise primary_error from cleanup_error
            raise


@contextmanager
def connect(manifest_path: Path, *, busy_timeout_ms: int) -> Iterator[sqlite3.Connection]:
    """Validate existing evidence or atomically publish a genuinely new mailbox.

    URI mode=rw forbids sqlite's implicit file creation for the canonical path.
    An empty preexisting file is damaged evidence, never proof of initializer
    ownership. Each potentially blocking SQL operation uses the configured bound.
    """
    if not 1 <= busy_timeout_ms <= 30000:
        raise StoreError(StoreRefusal.UNAVAILABLE, "busy timeout outside supported bounds")
    path = database_path(manifest_path)
    conn = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() and not path.is_symlink():
            _publish_new(path, busy_timeout_ms)
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise StoreError(StoreRefusal.CORRUPT, "mailbox is not a nonempty regular file")
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=rw", uri=True, timeout=busy_timeout_ms / 1000.0, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        # Schema and rows must be one read snapshot; no pragma writes before validation.
        conn.execute("BEGIN")
        try:
            _verify_schema(conn)
        finally:
            conn.rollback()
        _apply_and_verify_pragmas(conn, busy_timeout_ms=busy_timeout_ms)
        yield conn
    except sqlite3.DatabaseError as exc:
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise StoreError(StoreRefusal.CONTENDED, str(exc)) from exc
        raise StoreError(StoreRefusal.CORRUPT, str(exc)) from exc
    except (OSError, NotImplementedError) as exc:
        raise StoreError(StoreRefusal.UNAVAILABLE, "storage primitive unavailable") from exc
    finally:
        if conn is not None:
            conn.close()


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A single write transaction, acquiring the write lock up front.

    ``BEGIN IMMEDIATE`` rather than the default deferred begin: a deferred
    transaction takes the write lock at its first write, leaving a read-then-
    write sequence able to interleave with another process between the two.
    Every accounting decision in this module reads before it writes.
    """

    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        raise StoreError(StoreRefusal.CONTENDED, f"could not begin write transaction: {exc}") from exc
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()


def effective_time(conn: sqlite3.Connection, group_id: str) -> float:
    """The group's clock: ``max(wall clock, last persisted group time)``.

    Never bare ``time.time()``. A backwards clock step — NTP correction, a VM
    restore, an operator with a shell — would otherwise replenish rate budgets
    and extend leases that had already expired. Time may stall under this rule,
    which refuses too much; it can never run backwards, which would admit too
    much.
    """

    row = conn.execute("SELECT group_time FROM groups WHERE group_id = ?", (group_id,)).fetchone()
    persisted = float(row["group_time"]) if row is not None else 0.0
    wall = time.time()
    if not math.isfinite(wall) or wall < 0:
        raise StoreError(StoreRefusal.UNAVAILABLE, "invalid wall clock")
    return max(wall, persisted)


def touch_group_time(conn: sqlite3.Connection, group_id: str, now: float) -> None:
    """Advance the persisted group clock. Monotonic by construction."""

    conn.execute(
        "UPDATE groups SET group_time = ? WHERE group_id = ? AND group_time < ?",
        (now, group_id, now),
    )
