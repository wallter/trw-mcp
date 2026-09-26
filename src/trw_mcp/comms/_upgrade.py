"""Explicit, orchestrator-invoked v3 -> v4 mailbox upgrade and guarded rollback (PRD-CORE-274 FR16).

Nothing here runs implicitly: opening a v3 mailbox with a v4 build refuses
``mailbox_upgrade_required`` in ``_store``; only these two entry points change the
schema. Integrity rests on one connection held in SQLite ``locking_mode=EXCLUSIVE``:
the backup copy, the column steps, the commit and the recorded file change counter
all happen before that lock is released, so no concurrent write can fall between
them (lane-C R4-3/R6-1). Quiescence is an availability courtesy for old-build
members, decided from process evidence only; leases are never consulted.

The change-counter evidence holds only in rollback-journal mode (a WAL-mode file
does not move its header counter per commit) and only for the FIRST commit of an
exclusive hold. So both entry points refuse a file not in ``journal_mode=delete``
(never converting it), enforce ``synchronous=FULL``, and the upgrade performs
exactly one commit and records the counter only if it moved by exactly one.
Rollback, like upgrade, leaves any still-running member of the other build
refused with a schema-version error; stop or reconnect members around it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trw_mcp._pinned_read import copy_to, read_at
from trw_mcp.comms._envelope import canonical_bytes
from trw_mcp.comms._pins import member_pin_entry
from trw_mcp.comms._schema import SCHEMA_VERSION, V4_STEPS, SchemaVersionError, stored_version, verify
from trw_mcp.comms._store import StoreError, StoreRefusal, database_path

#: The upgrade record, beside the mailbox. Its presence is what makes a rollback possible.
RECORD_FILENAME = "comms.sqlite3.upgrade.json"
_COUNTER_OFFSET = 24


def change_counter(path: Path) -> int:
    """SQLite's file change counter (header bytes 24-27): moves on every committed content change."""
    # Read while _exclusive() holds the file: a raw open/close would release that lock (C15).
    header = read_at(path, _COUNTER_OFFSET + 4)
    if len(header) < _COUNTER_OFFSET + 4:
        raise StoreError(StoreRefusal.CORRUPT, "mailbox header is truncated")
    return int(struct.unpack(">I", header[_COUNTER_OFFSET : _COUNTER_OFFSET + 4])[0])


def process_exited(pid: object) -> bool:
    """True only when the OS proves *pid* is gone. Unknown, denied or recycled counts as NOT exited."""
    if type(pid) is not int or pid <= 0 or os.name != "posix":
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        # trw-fail-silent-allow: a denied or failed probe is NOT proof of exit; returning False keeps quiescence fail-closed (FR16)
        return False
    return False


def unexited_members(manifest_path: Path) -> list[str]:
    """Joined members whose recorded server process cannot be shown to have exited."""
    from trw_mcp.formation import read_manifest

    pending: list[str] = []
    for member in read_manifest(manifest_path).members:
        if member.run_path is None or member.pin_key is None:
            continue  # never joined: no serving process to wait for
        entry = member_pin_entry(member.run_path, member.pin_key)
        if not process_exited(entry.get("pid") if isinstance(entry, dict) else None):
            pending.append(member.member_id)
    return sorted(pending)


@contextmanager
def _exclusive(path: Path, busy_timeout_ms: int) -> Iterator[sqlite3.Connection]:
    """One connection whose exclusive lock survives COMMIT until this block ends."""
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=busy_timeout_ms / 1000.0)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
        # Read, never set: switching a WAL file back would checkpoint it, i.e. change it.
        journal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        if journal != "delete":
            raise StoreError(
                StoreRefusal.PRAGMA_UNAPPLIED, f"journal_mode is {journal!r}; counter evidence needs delete"
            )
        conn.execute("PRAGMA synchronous=FULL")
        if int(conn.execute("PRAGMA synchronous").fetchone()[0]) != 2:
            raise StoreError(
                StoreRefusal.PRAGMA_UNAPPLIED, "synchronous=FULL did not apply; durability cannot be assumed"
            )
        mode = conn.execute("PRAGMA locking_mode=EXCLUSIVE").fetchone()[0]
        if str(mode).lower() != "exclusive":
            raise StoreError(StoreRefusal.PRAGMA_UNAPPLIED, f"locking_mode is {mode!r}, expected exclusive")
        try:
            conn.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            raise StoreError(StoreRefusal.CONTENDED, f"could not take the exclusive upgrade lock: {exc}") from exc
        yield conn
    finally:
        try:
            if conn.in_transaction:
                conn.rollback()
            # Returning to NORMAL and touching the file is what releases the retained lock.
            conn.execute("PRAGMA locking_mode=NORMAL")
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
        finally:
            conn.close()


def _fsync_file_and_dir(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _verified_backup(path: Path) -> Path:
    """Copy the committed v3 file (caller holds the exclusive lock), fsync it, and verify the copy."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = path.with_name(f"comms.sqlite3.v3-{stamp}-{os.getpid()}-{time.monotonic_ns()}.bak")
    copy_to(path, backup)  # never shutil.copyfile: its close would release the held lock (C15)
    _fsync_file_and_dir(backup)
    copy = sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True)
    copy.row_factory = sqlite3.Row
    try:
        copy.execute("BEGIN")
        verify(copy, version=3)
    except (ValueError, TypeError, OverflowError, sqlite3.DatabaseError) as exc:
        raise StoreError(StoreRefusal.CORRUPT, "backup copy failed verification") from exc
    finally:
        copy.close()
    return backup


def _canonical_sha256(row: sqlite3.Row) -> str:
    """Same digest ``Envelope.canonical_sha256`` stamps at admission (FR15 exact-retry identity)."""
    payload = [row["recipient_member_id"], row["kind"], row["delivery_class"], row["body"]]
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _apply_v4(conn: sqlite3.Connection, ttl_seconds: int) -> None:
    for step in V4_STEPS:
        conn.execute(step)
    for row in conn.execute("SELECT * FROM admissions").fetchall():
        expires = float(row["admitted_at"]) + float(ttl_seconds)
        prepared = conn.execute(
            "SELECT 1 FROM milestones WHERE message_id=? AND fact='fetch_prepared'", (row["message_id"],)
        ).fetchone()
        # recipient_incarnation now means "last preparer" (FR13): an unprepared v3 row
        # must read as never prepared, or its first v4 fetch would not count.
        if not prepared:
            conn.execute(
                "UPDATE admissions SET recipient_incarnation=? WHERE message_id=?", ("0" * 32, row["message_id"])
            )
        conn.execute(
            "UPDATE admissions SET expires_at=?, delivery_count=?, canonical_sha256=? WHERE message_id=?",
            (
                expires if math.isfinite(expires) else float(row["admitted_at"]),
                1 if prepared else 0,
                _canonical_sha256(row),
                row["message_id"],
            ),
        )
    conn.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION),))


def _write_record(path: Path, record: dict[str, Any]) -> None:
    target = path.with_name(RECORD_FILENAME)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2), encoding="utf-8")
    _fsync_file_and_dir(temporary)
    os.replace(temporary, target)
    _fsync_file_and_dir(target)


def upgrade(
    manifest_path: Path, *, acknowledged: Iterable[str] = (), ttl_seconds: int, busy_timeout_ms: int = 5000
) -> dict[str, Any]:
    """Upgrade one formation's v3 mailbox to v4, or refuse leaving the v3 file unchanged."""
    path = database_path(manifest_path)
    if not path.is_file() or path.is_symlink():
        raise StoreError(StoreRefusal.UNAVAILABLE, "no mailbox to upgrade")
    acked = frozenset(acknowledged)
    unexited = [member for member in unexited_members(manifest_path) if member not in acked]
    if unexited:
        raise StoreError(StoreRefusal.UPGRADE_NOT_QUIESCENT, "not shown to have exited: " + ", ".join(unexited))
    backup: Path | None = None
    with _exclusive(path, busy_timeout_ms) as conn:
        try:
            if stored_version(conn) == str(SCHEMA_VERSION):
                return {"status": "already_current", "schema_version": SCHEMA_VERSION}
            verify(conn, version=3)
            before = change_counter(path)
            backup = _verified_backup(path)
            _apply_v4(conn, ttl_seconds)
            verify(conn, version=SCHEMA_VERSION)
        except BaseException as exc:
            if backup is not None:  # nothing committed: this attempt's backup is not evidence of anything
                backup.unlink(missing_ok=True)
            if isinstance(exc, SchemaVersionError):
                raise StoreError(StoreRefusal.SCHEMA_MISMATCH, str(exc)) from exc
            if isinstance(exc, (ValueError, TypeError, OverflowError)):
                raise StoreError(StoreRefusal.CORRUPT, "mailbox failed verification; not upgraded") from exc
            raise
        conn.execute("COMMIT")  # the ONE commit of this hold; EXCLUSIVE mode keeps the lock past it
        counter = change_counter(path)
        if counter != before + 1:
            # trw:intentional weak evidence is worse than none: without a record, rollback refuses.
            return {
                "status": "upgraded",
                "schema_version": SCHEMA_VERSION,
                "backup": backup.name,
                "rollback": "unavailable",
            }
        record = {
            "backup": backup.name,
            "backup_sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
            "change_counter": counter,
            "upgraded_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "acknowledged": sorted(acked),
        }
        _write_record(path, record)
    return {"status": "upgraded", "schema_version": SCHEMA_VERSION, "backup": record["backup"]}


def rollback(manifest_path: Path, *, busy_timeout_ms: int = 5000) -> dict[str, Any]:
    """Restore the verified v3 backup, only if nothing at all was written since the upgrade."""
    path = database_path(manifest_path)
    record_path = path.with_name(RECORD_FILENAME)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        backups = sorted(candidate.name for candidate in path.parent.glob("comms.sqlite3.v3-*.bak"))
        detail = "no upgrade record; nothing to roll back"
        if backups:
            detail += f" (v3 backups present: {', '.join(backups)}; restoring one is a manual operator step with all members stopped)"
        raise StoreError(StoreRefusal.UNAVAILABLE, detail) from exc
    backup = path.with_name(str(record["backup"]))
    wal = path.with_name(path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        raise StoreError(
            StoreRefusal.ROLLBACK_WOULD_DROP_TRAFFIC, "a write-ahead log holds uncounted writes; fix forward"
        )
    with _exclusive(path, busy_timeout_ms) as conn:
        if stored_version(conn) == "3":
            # A restore that crashed before the record rename: finish the bookkeeping only.
            conn.rollback()
            os.replace(record_path, record_path.with_suffix(".rolled-back.json"))
            return {"status": "already_rolled_back", "schema_version": 3, "backup": backup.name}
        if change_counter(path) != int(record["change_counter"]):
            raise StoreError(StoreRefusal.ROLLBACK_WOULD_DROP_TRAFFIC, "mailbox changed after upgrade; fix forward")
        if hashlib.sha256(backup.read_bytes()).hexdigest() != record["backup_sha256"]:
            raise StoreError(StoreRefusal.CORRUPT, "backup does not match the upgrade record")
        conn.execute("COMMIT")  # lock retained (EXCLUSIVE mode) while the backup is copied in
        source = sqlite3.connect(backup.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            source.backup(conn)
        finally:
            source.close()
        conn.execute("BEGIN")
        try:
            verify(conn, version=3)
        except (ValueError, TypeError, OverflowError) as exc:
            raise StoreError(StoreRefusal.CORRUPT, "restored mailbox failed v3 verification") from exc
        conn.execute("COMMIT")
    os.replace(record_path, record_path.with_suffix(".rolled-back.json"))
    return {"status": "rolled_back", "schema_version": 3, "backup": backup.name}


__all__ = ["change_counter", "process_exited", "rollback", "unexited_members", "upgrade"]
