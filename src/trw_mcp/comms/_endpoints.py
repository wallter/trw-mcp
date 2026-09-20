"""Endpoint generation and consumer fencing (PRD-CORE-274 FR12, Amendment 02).

An *endpoint* is one member's serving process in one group. It records which
incarnation may consume the member's messages, a monotonic ``generation``, the
serving build's protocol, and advisory availability (``last_seen_at`` /
``lease_expires_at``).

**Incarnation is generated here, in the serving process, and is never a public
argument.** A process proves which incarnation it is by holding the token in
memory, which a message cannot forge. The rules:

- a process that has NEVER held an incarnation for its binding may take the
  endpoint over (generation + 1) whatever the lease says -- the binding (FR01:
  manifest run_path AND pin) is the authority, not elapsed time;
- a process whose incarnation was taken over is remembered as displaced and is
  refused for every later enroll, fetch, ACK or send under that binding, so a
  stale process can never take the endpoint back (no ping-pong);
- every successful owning operation renews the lease in the same transaction;
  nothing else does. Renewal records ACTIVITY, not attention.

The lease never gates admission, fetch or ACK. Replacement never expires,
retargets or acknowledges a message: messages are addressed to the member.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import cast

from trw_mcp.comms._identity import CallerBinding
from trw_mcp.comms._schema import ENDPOINT_PROTOCOL

#: Incarnations this PROCESS owns, keyed by (group_id, member_id, canonical run, resolved pin). In-memory by
#: design: an incarnation a caller could read is an incarnation a caller could
#: claim. Losing it on restart is correct — a restarted process IS a new
#: incarnation and must re-enroll.
_PROCESS_INCARNATIONS: dict[tuple[str, str, str, str], str] = {}
#: Bindings whose incarnation in THIS process was taken over by another process.
#: Permanent for the process lifetime: the recovery is a new process (reconnect).
_DISPLACED: set[tuple[str, str, str, str]] = set()
#: The action a displaced process must take; surfaced by the facade (FR12).
DISPLACED_RECOVERY = "this process was displaced by a newer server for this member; reconnect this client's MCP server or stop this process"


def _ownership_key(binding: CallerBinding) -> tuple[str, str, str, str]:
    return binding.group_id, binding.member_id, str(binding.run_path.resolve()), binding.session_id


class EndpointRefusal(str, Enum):
    """Closed refusal vocabulary for endpoint operations."""

    COLLISION = "live_endpoint_held_by_other_incarnation"
    FENCED = "endpoint_replaced_by_newer_incarnation"
    NOT_ENROLLED = "no_endpoint_for_member"
    EXPIRED = "receiver_lease_expired"


class EndpointError(RuntimeError):
    def __init__(self, refusal: EndpointRefusal, detail: str) -> None:
        super().__init__(f"{refusal.value}: {detail}")
        self.refusal = refusal
        self.detail = detail


@dataclass(frozen=True)
class Endpoint:
    """One member's presence. ``incarnation`` is never returned to a caller."""

    group_id: str
    member_id: str
    session_id: str
    run_path: Path
    enrolled_at: float
    last_seen_at: float
    lease_expires_at: float
    generation: int = 1

    def is_live(self, now: float) -> bool:
        return now < self.lease_expires_at


def _row_to_endpoint(row: sqlite3.Row) -> Endpoint:
    return Endpoint(
        group_id=str(row["group_id"]),
        member_id=str(row["member_id"]),
        session_id=str(row["session_id"]),
        run_path=Path(str(row["run_path"])),
        enrolled_at=float(row["enrolled_at"]),
        last_seen_at=float(row["last_seen_at"]),
        lease_expires_at=float(row["lease_expires_at"]),
        generation=int(row["generation"]),
    )


def _fetch_row(conn: sqlite3.Connection, group_id: str, member_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM endpoints WHERE group_id = ? AND member_id = ?",
        (group_id, member_id),
    ).fetchone()
    # cast, not a runtime conversion: row_factory already yields sqlite3.Row,
    # but fetchone() is typed Any.
    return cast("sqlite3.Row | None", row)


def _assert_not_displaced(key: tuple[str, str, str, str], row: sqlite3.Row | None, mine: str | None) -> None:
    """Refuse a process whose incarnation was taken over, and remember it permanently.

    Named and separate so a test can disable exactly this rule and watch the
    displaced process regain the endpoint -- the ping-pong it exists to prevent.
    """
    if key in _DISPLACED:
        raise EndpointError(EndpointRefusal.FENCED, DISPLACED_RECOVERY)
    if row is not None and mine is not None and str(row["incarnation"]) != mine:
        _DISPLACED.add(key)
        raise EndpointError(EndpointRefusal.FENCED, DISPLACED_RECOVERY)


def _current(
    conn: sqlite3.Connection, binding: CallerBinding, *, require_row: bool
) -> tuple[sqlite3.Row | None, str | None]:
    """The endpoint row and this process's incarnation, refusing a displaced process.

    Marks the binding displaced the first time it observes that another incarnation
    holds the endpoint this process owned, so the refusal is permanent from then on.
    """
    if not conn.in_transaction:
        raise RuntimeError("endpoint operations require the facade transaction")
    key = _ownership_key(binding)
    row = _fetch_row(conn, binding.group_id, binding.member_id)
    mine = _PROCESS_INCARNATIONS.get(key)
    _assert_not_displaced(key, row, mine)
    if require_row and row is None:
        raise EndpointError(EndpointRefusal.NOT_ENROLLED, f"member {binding.member_id!r} has no endpoint; enroll first")
    return row, mine


def _renew(conn: sqlite3.Connection, binding: CallerBinding, now: float, lease_ttl_seconds: int) -> float:
    expires = now + float(lease_ttl_seconds)
    conn.execute(
        "UPDATE endpoints SET last_seen_at = ?, lease_expires_at = ? WHERE group_id = ? AND member_id = ?",
        (now, expires, binding.group_id, binding.member_id),
    )
    return expires


def enroll(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    *,
    now: float,
    lease_ttl_seconds: int,
) -> tuple[Endpoint, str]:
    """Renew this process's endpoint, or take it over as a process that never held it."""
    row, mine = _current(conn, binding, require_row=False)
    renewing = row is not None and mine is not None and str(row["incarnation"]) == mine
    incarnation = mine if renewing and mine is not None else secrets.token_hex(16)
    expires = now + float(lease_ttl_seconds)
    enrolled_at = float(row["enrolled_at"]) if renewing and row is not None else now
    generation = 1 if row is None else int(row["generation"]) + (0 if renewing else 1)
    conn.execute(
        "INSERT INTO endpoints(group_id, member_id, incarnation, session_id, run_path, "
        "enrolled_at, last_seen_at, lease_expires_at, generation, protocol) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(group_id, member_id) DO UPDATE SET "
        "incarnation=excluded.incarnation, session_id=excluded.session_id, run_path=excluded.run_path, "
        "enrolled_at=excluded.enrolled_at, last_seen_at=excluded.last_seen_at, "
        "lease_expires_at=excluded.lease_expires_at, generation=excluded.generation, protocol=excluded.protocol",
        (
            binding.group_id,
            binding.member_id,
            incarnation,
            binding.session_id,
            str(binding.run_path),
            enrolled_at,
            now,
            expires,
            generation,
            ENDPOINT_PROTOCOL,
        ),
    )
    # The facade commits these exact values before projecting a response.
    return Endpoint(
        group_id=binding.group_id,
        member_id=binding.member_id,
        session_id=binding.session_id,
        run_path=binding.run_path,
        enrolled_at=enrolled_at,
        last_seen_at=now,
        lease_expires_at=expires,
    ), incarnation


def heartbeat(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    *,
    now: float,
    lease_ttl_seconds: int,
) -> Endpoint:
    """Renew this process's own endpoint; a process that holds none is told to enroll."""
    row, mine = _current(conn, binding, require_row=True)
    assert row is not None  # noqa: S101 - require_row
    if mine is None:
        raise EndpointError(EndpointRefusal.FENCED, "this process holds no endpoint for this member; enroll first")
    expires = _renew(conn, binding, now, lease_ttl_seconds)
    return Endpoint(
        group_id=binding.group_id,
        member_id=binding.member_id,
        session_id=binding.session_id,
        run_path=binding.run_path,
        enrolled_at=float(row["enrolled_at"]),
        last_seen_at=now,
        lease_expires_at=expires,
    )


def list_endpoints(conn: sqlite3.Connection, group_id: str, *, limit: int, after_member: str = "") -> list[Endpoint]:
    """Bounded group-scoped keyset read; member ordering survives reincarnation."""

    rows = conn.execute(
        "SELECT * FROM endpoints WHERE group_id = ? AND member_id > ? ORDER BY member_id ASC LIMIT ?",
        (group_id, after_member, int(limit)),
    ).fetchall()
    return [_row_to_endpoint(row) for row in rows]


def _reset_process_incarnations_for_test() -> None:
    """Clear this process's incarnation memory.

    Tests need a way to simulate a RESTARTED process, which is exactly "the
    same database, none of the in-memory incarnations". Named for its only
    caller rather than dressed up as a general API.
    """

    _PROCESS_INCARNATIONS.clear()
    _DISPLACED.clear()


def holds_endpoint(binding: CallerBinding) -> bool:
    """Whether THIS process holds the member's current, undisplaced endpoint (FR18 ``state``)."""
    key = _ownership_key(binding)
    return key in _PROCESS_INCARNATIONS and key not in _DISPLACED


def confirm_enrollment(binding: CallerBinding, incarnation: str) -> None:
    """Publish ownership in memory only after the database transaction commits."""
    _PROCESS_INCARNATIONS[_ownership_key(binding)] = incarnation


def receiver_incarnation(
    conn: sqlite3.Connection, binding: CallerBinding, now: float, *, lease_ttl_seconds: int, renew: bool = True
) -> str:
    """The incarnation that may fetch/ACK for this binding, renewing it (FR12).

    Runs under the operation's write lock, so the incarnation it returns cannot be
    displaced before the caller acts on it. An expired lease is NOT a refusal.
    """
    row, mine = _current(conn, binding, require_row=True)
    assert row is not None  # noqa: S101 - require_row
    if mine is None:
        raise EndpointError(EndpointRefusal.FENCED, "this process holds no endpoint for this member; enroll first")
    if renew:  # FR11: retries inside a bounded wait never renew; only the ordinary attempt does
        _renew(conn, binding, now, lease_ttl_seconds)
    return mine


def touch(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    now: float,
    *,
    lease_ttl_seconds: int,
    refuse_displaced: bool = True,
) -> None:
    """Send/status side of FR12: renew a current process, else no-op.

    A displaced process is refused for send (``refuse_displaced``). Body-free status is
    not on FR12's refusal list, so for it a displaced process simply does not renew.
    """
    if not refuse_displaced:
        key = _ownership_key(binding)
        row = _fetch_row(conn, binding.group_id, binding.member_id)
        mine = _PROCESS_INCARNATIONS.get(key)
        if key not in _DISPLACED and row is not None and mine is not None and str(row["incarnation"]) == mine:
            _renew(conn, binding, now, lease_ttl_seconds)
        return
    row, mine = _current(conn, binding, require_row=False)
    if row is not None and mine is not None:
        _renew(conn, binding, now, lease_ttl_seconds)
