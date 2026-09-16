"""Endpoint leases and incarnation fencing (PRD-CORE-274-FR07).

An *endpoint* is one member's live presence in one group. It exists so a peer
that has vanished stops being a valid target promptly, and so a respawned peer
cannot inherit the traffic of the process it replaced.

**Incarnation is generated here, in the serving process, and is never a public
argument.** It is not in any tool signature, so a caller cannot name one — the
process proves which incarnation it is by holding the token in memory, which a
message cannot forge. Enrollment rules follow from that:

- an unexpired endpoint cannot be replaced by a different incarnation;
- an EXPIRED one can, and replacement is recorded rather than merged;
- heartbeat renews even a lapsed lease for the SAME incarnation while
  membership is still valid, but can never undo a replacement that already
  happened.

Liveness is explicit. A peer that is thinking hard is not heartbeating, and this
module deliberately cannot tell the difference between that and a dead one —
inferring liveness from activity is what makes a mailbox quietly lie.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import cast

from trw_mcp.comms._identity import CallerBinding
from trw_mcp.comms._messages import expire_replaced

#: Incarnations this PROCESS owns, keyed by (group_id, member_id, canonical run, resolved pin). In-memory by
#: design: an incarnation a caller could read is an incarnation a caller could
#: claim. Losing it on restart is correct — a restarted process IS a new
#: incarnation and must re-enroll.
_PROCESS_INCARNATIONS: dict[tuple[str, str, str, str], str] = {}


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
    )


def _fetch_row(conn: sqlite3.Connection, group_id: str, member_id: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT * FROM endpoints WHERE group_id = ? AND member_id = ?",
        (group_id, member_id),
    ).fetchone()
    # cast, not a runtime conversion: row_factory already yields sqlite3.Row,
    # but fetchone() is typed Any.
    return cast("sqlite3.Row | None", row)


def _assert_no_live_collision(row: sqlite3.Row, *, mine: str | None, member_id: str, now: float) -> None:
    """Refuse when a DIFFERENT incarnation holds an unexpired lease.

    Extracted and named so a test can disable exactly this rule and show the
    collision would otherwise be admitted. A guard that cannot be switched off
    in a test cannot be proved to be doing anything.
    """

    if now < float(row["lease_expires_at"]) and str(row["incarnation"]) != mine:
        raise EndpointError(
            EndpointRefusal.COLLISION,
            f"member {member_id!r} has a live endpoint until {row['lease_expires_at']}",
        )


def _assert_not_fenced(row: sqlite3.Row, *, mine: str | None, member_id: str) -> None:
    """Refuse when this process no longer owns the endpoint it is renewing."""

    if mine is None or str(row["incarnation"]) != mine:
        raise EndpointError(
            EndpointRefusal.FENCED,
            f"member {member_id!r} endpoint is held by a different incarnation",
        )


def enroll(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    *,
    now: float,
    lease_ttl_seconds: int,
) -> tuple[Endpoint, str]:
    """Claim this member's endpoint for THIS process, or refuse.

    Refuses when a different incarnation holds an unexpired lease: two live
    processes claiming one member is a real conflict, and picking a winner
    silently would send one of them into a mailbox it does not own.
    """

    key = _ownership_key(binding)
    if not conn.in_transaction:
        raise RuntimeError("endpoint operations require the facade transaction")
    row = _fetch_row(conn, binding.group_id, binding.member_id)
    mine = _PROCESS_INCARNATIONS.get(key)
    if row is not None and (
        row["session_id"] != binding.session_id or Path(row["run_path"]).resolve() != binding.run_path.resolve()
    ):
        mine = None
    if row is not None:
        _assert_no_live_collision(row, mine=mine, member_id=binding.member_id, now=now)
    incarnation = mine if (row is not None and str(row["incarnation"]) == mine) else secrets.token_hex(16)
    if row is not None and str(row["incarnation"]) != incarnation:
        expire_replaced(conn, binding.group_id, binding.member_id, str(row["incarnation"]), now)
    expires = now + float(lease_ttl_seconds)
    enrolled_at = float(row["enrolled_at"]) if row is not None and str(row["incarnation"]) == mine else now
    conn.execute(
        "INSERT INTO endpoints(group_id, member_id, incarnation, session_id, run_path, "
        "enrolled_at, last_seen_at, lease_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(group_id, member_id) DO UPDATE SET "
        "incarnation=excluded.incarnation, session_id=excluded.session_id, run_path=excluded.run_path, "
        "enrolled_at=excluded.enrolled_at, last_seen_at=excluded.last_seen_at, "
        "lease_expires_at=excluded.lease_expires_at",
        (
            binding.group_id,
            binding.member_id,
            incarnation,
            binding.session_id,
            str(binding.run_path),
            enrolled_at,
            now,
            expires,
        ),
    )
    # The facade commits these exact values before projecting a response.
    # Re-reading after commit could return a later incarnation instead.
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
    """Renew this process's own lease, including one that has already lapsed.

    A lapsed lease is renewable because expiry alone does not mean the peer is
    gone — only that it stopped saying so. What is NOT renewable is an endpoint
    another incarnation has already taken over: that refuses as FENCED, so the
    displaced process learns it lost the endpoint instead of silently writing
    into a mailbox that now belongs to someone else.
    """

    key = _ownership_key(binding)
    if not conn.in_transaction:
        raise RuntimeError("endpoint operations require the facade transaction")
    row = _fetch_row(conn, binding.group_id, binding.member_id)
    if row is None:
        raise EndpointError(
            EndpointRefusal.NOT_ENROLLED,
            f"member {binding.member_id!r} has no endpoint; enroll before heartbeat",
        )
    mine = _PROCESS_INCARNATIONS.get(key)
    if row["session_id"] != binding.session_id or Path(row["run_path"]).resolve() != binding.run_path.resolve():
        mine = None
    _assert_not_fenced(row, mine=mine, member_id=binding.member_id)
    expires = now + float(lease_ttl_seconds)
    conn.execute(
        "UPDATE endpoints SET last_seen_at = ?, lease_expires_at = ? WHERE group_id = ? AND member_id = ?",
        (now, expires, binding.group_id, binding.member_id),
    )
    enrolled_at = float(row["enrolled_at"])
    return Endpoint(
        group_id=binding.group_id,
        member_id=binding.member_id,
        session_id=binding.session_id,
        run_path=binding.run_path,
        enrolled_at=enrolled_at,
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


def confirm_enrollment(binding: CallerBinding, incarnation: str) -> None:
    """Publish ownership in memory only after the database transaction commits."""
    _PROCESS_INCARNATIONS[_ownership_key(binding)] = incarnation


def receiver_incarnation(conn: sqlite3.Connection, binding: CallerBinding, now: float) -> str:
    """Verify a live, binding-owned receiver without renewing or enrolling it.

    Reads only, but the guard is the same as its writing siblings' on purpose:
    the incarnation it returns is used to authorize the fetch that follows, so
    a read outside the write lock could return an incarnation another process
    displaces before the caller acts on it.
    """
    if not conn.in_transaction:
        raise RuntimeError("endpoint operations require the facade transaction")
    row = _fetch_row(conn, binding.group_id, binding.member_id)
    if row is None:
        raise EndpointError(EndpointRefusal.NOT_ENROLLED, "receiver enrollment required")
    mine = _PROCESS_INCARNATIONS.get(_ownership_key(binding))
    if row["session_id"] != binding.session_id or Path(row["run_path"]).resolve() != binding.run_path.resolve():
        mine = None
    _assert_not_fenced(row, mine=mine, member_id=binding.member_id)
    if row["lease_expires_at"] <= now:
        raise EndpointError(EndpointRefusal.EXPIRED, "explicit heartbeat required")
    return str(row["incarnation"])
