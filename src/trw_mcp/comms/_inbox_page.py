"""Bounded moving-view inbox pages and whole-batch ACK validation.

Append rowids are stable only under the subsystem's no-delete/reinsert/VACUUM
contract. Cursors resume traversal, not a snapshot or authority; a fresh fetch
recovers still-pending rows even after preparation or a lost prior response.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import sqlite3
from typing import Any

from trw_mcp.comms._envelope import AdmissionError, InboxAction, canonical_bytes, receipt
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
    return base64.urlsafe_b64encode(canonical_bytes([1, scope, after])).decode("ascii")


def _decode(cursor: str | None, scope: str) -> int:
    if cursor is None:
        return 0
    if not cursor or len(cursor) > 256:
        raise AdmissionError("invalid_cursor")
    try:
        decoded = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise AdmissionError("invalid_cursor") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 3
        or type(decoded[0]) is not int
        or decoded[0] != 1
        or decoded[1] != scope
        or type(decoded[2]) is not int
        or not 1 <= decoded[2] <= 9223372036854775807
        or _encode(scope, decoded[2]) != cursor
    ):
        raise AdmissionError("invalid_cursor")
    return int(decoded[2])


def _bounded(payload: dict[str, Any], max_bytes: int) -> bool:
    return len(canonical_bytes(payload)) <= max_bytes


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
                "SELECT rowid AS append_id,* FROM admissions WHERE group_id=? AND recipient_member_id=? "
                "AND recipient_incarnation=? AND state='pending' AND rowid>? ORDER BY rowid LIMIT ?",
                (binding.group_id, binding.member_id, incarnation, after, limit + 1),
            )
        )
    return list(
        conn.execute(
            "SELECT rowid AS append_id,* FROM admissions WHERE group_id=? "
            "AND (sender_member_id=? OR recipient_member_id=?) AND rowid>? ORDER BY rowid LIMIT ?",
            (binding.group_id, binding.member_id, binding.member_id, after, limit + 1),
        )
    )


def _project(conn: sqlite3.Connection, row: sqlite3.Row, action: InboxAction) -> dict[str, Any]:
    item = receipt(row)
    if action == "fetch":
        item["body"] = row["body"]
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
        rows = validate_ack(conn, binding, str(incarnation), normalized)
        result = {"status": "ok", "delivery": "pull_only", "acknowledged_ids": normalized}
        if not _bounded(result, max_bytes):
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
    payload: dict[str, Any] = {"status": "ok", "delivery": "pull_only", "items": [], "next_cursor": None}
    if not _bounded(payload, max_bytes):
        raise AdmissionError("response_too_small")
    included: list[sqlite3.Row] = []
    for index, row in enumerate(rows[:limit]):
        candidate = {
            **payload,
            "items": [*payload["items"], _project(conn, row, action)],
            "next_cursor": _encode(scope, row["append_id"]) if index + 1 < len(rows) else None,
        }
        if not _bounded(candidate, max_bytes):
            if not included:
                raise AdmissionError("response_too_small")
            break
        payload = candidate
        included.append(row)
    if action == "fetch":
        prepare_fetch(conn, included, now)
    return payload
