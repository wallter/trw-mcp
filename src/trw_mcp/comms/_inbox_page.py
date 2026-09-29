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
from trw_mcp.comms._envelope import (
    HANDOFF_ACTIONS,
    AdmissionError,
    InboxAction,
    MessageState,
    canonical_bytes,
    receipt,
)
from trw_mcp.comms._handoff import derive_handoff, handoff_inputs
from trw_mcp.comms._identity import CallerBinding
from trw_mcp.comms._messages import accept, acknowledge, complete, prepare_fetch, report, validate_ack


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
        # FR06: the raw facts stay; the handoff block is the FR01 derivation over them.
        facts, pointers = handoff_inputs(conn, [row["message_id"]])
        item["milestones"] = facts[row["message_id"]]
        handoff = derive_handoff(row, item["milestones"], pointers.get(row["message_id"]))
        if handoff is not None:
            item["handoff"] = handoff
    return item


#: The receipt key naming what each handoff action recorded.
_RESULT_KEY = {"accept": "accepted_ids", "report": "reported_ids", "complete": "completed_ids"}


def _handoff(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    action: InboxAction,
    ids: list[str] | None,
    next_read: str | None,
    *,
    now: float,
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """PRD-CORE-322 FR02-FR04 argument rules, then the one writer; the refusal rolls every write back.

    Runtime caller: :func:`inbox_action` for ``trw_inbox`` accept, report and complete.
    """
    if not ids or len(ids) > limit or any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in ids):
        raise AdmissionError("invalid_inbox_arguments")
    normalized = list(dict.fromkeys(ids))
    if (action == "report") != (next_read is not None) or (action == "report" and len(normalized) != 1):
        raise AdmissionError("invalid_inbox_arguments")
    result = {"status": "ok", "delivery": "pull_only", _RESULT_KEY[action]: normalized}
    if not _paging.fits(result, max_bytes):
        raise AdmissionError("response_too_small")
    if action == "accept":
        accept(conn, binding, normalized, now)
    elif action == "report":
        report(conn, binding, normalized[0], next_read, now)
    else:
        complete(conn, binding, normalized, now)
    return result


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
    next_read: str | None = None,
) -> dict[str, Any]:
    """Caller owns eligibility, receiver fencing and the operation transaction."""
    if cursor is not None and action != "fetch" and action != "status":
        raise AdmissionError("invalid_inbox_arguments")
    if action in HANDOFF_ACTIONS:
        return _handoff(conn, binding, action, ids, next_read, now=now, limit=limit, max_bytes=max_bytes)
    if next_read is not None:
        raise AdmissionError("invalid_inbox_arguments")
    if action == "ack":
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
