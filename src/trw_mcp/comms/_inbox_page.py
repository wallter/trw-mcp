"""Bounded moving-view inbox pages and whole-batch ACK validation.

Append rowids are stable only under the subsystem's no-delete/reinsert/VACUUM
contract. Cursors resume traversal, not a snapshot or authority; a fresh fetch
recovers still-pending rows even after preparation or a lost prior response.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any

from trw_mcp.comms import _paging
from trw_mcp.comms._envelope import AdmissionError, InboxAction, MessageState, canonical_bytes, receipt
from trw_mcp.comms._identity import CallerBinding
from trw_mcp.comms._messages import acknowledge, prepare_fetch, validate_ack


def _scope(binding: CallerBinding, action: InboxAction, incarnation: str | None) -> str:
    # No raw token/pin/path is exposed even reversibly in a cursor.
    return hashlib.sha256(
        canonical_bytes(
            [
                1,
                binding.group_id,
                binding.member_id,
                str(binding.run_path.resolve()),
                binding.session_id,
                action,
                incarnation,
            ]
        )
    ).hexdigest()


def _encode(scope: str, after: int) -> str:
    return _paging.encode_cursor(scope, after)


def _decode(cursor: str | None, scope: str) -> int:
    if cursor is None:
        return 0
    fields = _paging.decode_cursor(cursor, max_chars=256, arity=2)
    if fields is None or fields[0] != scope or type(fields[1]) is not int or not 1 <= fields[1] <= 9223372036854775807:
        raise AdmissionError("invalid_cursor")
    return int(fields[1])


def _read_rows(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    action: InboxAction,
    incarnation: str | None,
    after: int,
    limit: int,
) -> list[sqlite3.Row]:
    if action == "fetch":
        return list(
            conn.execute(
                # FR13: the member's rows, whichever generation they were admitted under.
                "SELECT rowid AS append_id,* FROM admissions WHERE group_id=? AND recipient_member_id=? "
                "AND state=? AND rowid>? ORDER BY rowid LIMIT ?",
                (binding.group_id, binding.member_id, MessageState.PENDING.value, after, limit + 1),
            )
        )
    return list(
        conn.execute(
            "SELECT rowid AS append_id,* FROM admissions WHERE group_id=? "
            "AND (sender_member_id=? OR recipient_member_id=?) AND rowid>? ORDER BY rowid LIMIT ?",
            (binding.group_id, binding.member_id, binding.member_id, after, limit + 1),
        )
    )


def _project(
    conn: sqlite3.Connection, row: sqlite3.Row, action: InboxAction, incarnation: str | None
) -> dict[str, Any]:
    item = receipt(row)
    if action == "fetch":
        item["body"] = row["body"]
        # FR13 at-least-once: flag only when true, so the common case costs nothing.
        deliveries = int(row["delivery_count"]) + (row["recipient_incarnation"] != incarnation)
        if deliveries > 1:
            item["redelivered"] = True
    else:
        item["state"] = row["state"]
        item["milestones"] = dict(
            conn.execute("SELECT fact,at FROM milestones WHERE message_id=?", (row["message_id"],))
        )
    return item


def inbox_action(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    action: InboxAction,
    ids: list[str] | None,
    cursor: str | None,
    incarnation: str | None,
    *,
    now: float,
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Caller owns eligibility, receiver fencing and the operation transaction."""
    if action == "ack":
        if cursor is not None:
            raise AdmissionError("invalid_inbox_arguments")
        if not ids or len(ids) > limit or any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in ids):
            raise AdmissionError("invalid_ack_ids")
        normalized = list(dict.fromkeys(ids))
        rows = validate_ack(conn, binding, normalized)
        result = {"status": "ok", "delivery": "pull_only", "acknowledged_ids": normalized}
        if not _paging.fits(result, max_bytes):
            raise AdmissionError("response_too_small")
        acknowledge(conn, rows, now)
        return result
    if action not in ("fetch", "status") or ids is not None:
        raise AdmissionError("invalid_inbox_arguments")
    scope = _scope(binding, action, incarnation)
    after = _decode(cursor, scope)
    if action == "fetch":
        body_limit = conn.execute("SELECT body_limit FROM groups WHERE group_id=?", (binding.group_id,)).fetchone()[0]
        if max_bytes < 6 * body_limit + 4096:
            raise AdmissionError("response_body_policy_incompatible")
    rows = _read_rows(conn, binding, action, incarnation, after, limit)
    entries = (
        (
            _project(conn, row, action, incarnation),
            _encode(scope, row["append_id"]) if index + 1 < len(rows) else None,
        )
        for index, row in enumerate(rows[:limit])
    )
    payload, count = _paging.pack(
        {"status": "ok", "delivery": "pull_only"}, "items", entries, max_bytes=max_bytes, refuse=AdmissionError
    )
    included = rows[:count]
    if action == "fetch":
        prepare_fetch(conn, included, now, str(incarnation))
    return payload
