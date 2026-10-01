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

from trw_mcp.comms import _ahr_events, _paging
from trw_mcp.comms._envelope import (
    AHR_ACTIONS,
    HANDOFF_ACTIONS,
    AdmissionError,
    InboxAction,
    MessageState,
    canonical_bytes,
    clean_text,
    receipt,
)
from trw_mcp.comms._handoff import derive_handoff, handoff_inputs
from trw_mcp.comms._identity import CallerBinding
from trw_mcp.comms._messages import (
    accept,
    acknowledge,
    complete,
    handoff_rows,
    prepare_fetch,
    report,
    validate_ack,
)


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
        record = _ahr_events.fetch_record(conn, row["message_id"])  # PRD-CORE-349: the record itself, as data
        if record is not None:
            item["ahr_record"] = record
        # FR13 at-least-once: flag only when true, so the common case costs nothing.
        deliveries = int(row["delivery_count"]) + (row["recipient_incarnation"] != incarnation)
        if deliveries > 1:
            item["redelivered"] = True
    else:
        item["state"] = row["state"]
        # FR06: the raw facts stay; the handoff block is the FR01 derivation over them.
        facts, pointers = handoff_inputs(conn, [row["message_id"]])
        item["milestones"] = facts[row["message_id"]]
        handoff = derive_handoff(
            row,
            item["milestones"],
            pointers.get(row["message_id"]),
            ahr=_ahr_events.view(conn, row["message_id"], row["group_id"]),
        )
        if handoff is not None:
            item["handoff"] = handoff
    return item


#: The receipt key naming what each handoff action recorded.
_RESULT_KEY = {
    "accept": "accepted_ids",
    "report": "reported_ids",
    "complete": "completed_ids",
    "read_back": "read_back_ids",
    "answer": "answered_ids",
    "decline": "declined_ids",
    "offer_withdraw": "withdrawn_ids",
}
#: PRD-CORE-349: the one ``handoff`` key each AHR use requires (``report`` needs it only on an AHR request).
_AHR_ARGUMENT = {
    "read_back": "path",
    "answer": "path",
    "decline": "reason",
    "offer_withdraw": "reason",
    "report": "outcome",
}
_OUTCOMES = frozenset({"met", "returned", "escalated"})


def _ahr_argument(action: InboxAction, handoff: object) -> str | None:
    """The single string value *action* takes from ``handoff``, or refuse a malformed argument."""
    if handoff is None:
        if action in AHR_ACTIONS:
            raise AdmissionError("invalid_inbox_arguments")
        return None
    key = _AHR_ARGUMENT.get(action)
    if key is None or not isinstance(handoff, dict) or set(handoff) != {key}:
        raise AdmissionError("invalid_inbox_arguments")
    value = handoff[key]
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 2000:
        raise AdmissionError("invalid_inbox_arguments")
    if key == "outcome" and value not in _OUTCOMES:
        raise AdmissionError("invalid_inbox_arguments")
    return value


def _ahr_write(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    action: InboxAction,
    message_id: str,
    value: str | None,
    next_read: str | None,
    now: float,
) -> None:
    """The AHR event for one handoff action on an AHR request (PRD-CORE-349); plain requests are untouched.

    Runs after the PRD-CORE-322 write in the same savepoint, so a refusal here rolls both back.
    """
    if action in AHR_ACTIONS:
        as_sender = action in ("answer", "offer_withdraw")
        ((row, _facts),) = handoff_rows(conn, binding, [message_id], as_sender=as_sender)
        if not as_sender and row["state"] == MessageState.EXPIRED:
            raise AdmissionError("handoff_not_authorized")  # as accept refuses an expired carrier
        if action == "read_back":
            _ahr_events.read_back(conn, message_id, value, now)
        elif action == "answer":
            _ahr_events.answer(conn, message_id, value, now)
        elif action == "offer_withdraw":
            _ahr_events.withdraw(conn, message_id, str(value), now)
        else:
            _ahr_events.decline(conn, message_id, str(value), now)
            if row["state"] == MessageState.PENDING:
                acknowledge(conn, [row], now)  # a declined offer was received: it leaves the fetch queue
        return
    if _ahr_events.handoff_of(conn, message_id) is None:
        if value is not None:
            raise AdmissionError("invalid_inbox_arguments")  # handoff={...} only applies to an AHR request
        return
    if action == "accept":
        _ahr_events.accept(conn, message_id, now)
    elif action == "report":
        if value is None:
            raise AdmissionError("ahr_report_needs_outcome")
        _ahr_events.report(conn, message_id, str(next_read), value, now)
    else:
        _ahr_events.complete(conn, message_id, now)


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
    handoff: object = None,
) -> dict[str, Any]:
    """PRD-CORE-322 FR02-FR04 argument rules, then the one writer; the refusal rolls every write back.

    PRD-CORE-349: on an AHR request each action also appends its AHR event (``_ahr_write``), and the
    AHR-only actions (read_back, answer, decline, offer_withdraw) take one id and a ``handoff`` value.
    Runtime caller: :func:`inbox_action` for every ``HANDOFF_ACTIONS`` member.
    """
    if not ids or len(ids) > limit:
        raise AdmissionError("invalid_inbox_arguments")
    if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in ids):
        raise AdmissionError("invalid_message_id")
    normalized = list(dict.fromkeys(ids))
    value = _ahr_argument(action, handoff)
    if action in AHR_ACTIONS and len(normalized) != 1:
        raise AdmissionError("invalid_inbox_arguments")
    if action == "report":
        if next_read is None:
            raise AdmissionError("report_needs_next_read")
        if len(normalized) != 1:
            raise AdmissionError("report_takes_one_message_id")
        next_read = clean_text(next_read)
    elif next_read is not None:
        raise AdmissionError("invalid_inbox_arguments")
    result = {"status": "ok", "delivery": "pull_only", _RESULT_KEY[action]: normalized}
    if not _paging.fits(result, max_bytes):
        raise AdmissionError("response_too_small")
    if action == "accept":
        accept(conn, binding, normalized, now)
    elif action == "report":
        report(conn, binding, normalized[0], next_read, now)
    elif action == "complete" and (unresolved := complete(conn, binding, normalized, now)):
        result["unresolved_next_read"] = unresolved  # E2E-INC-092: the sender checks these pointers
    for message_id in normalized:
        _ahr_write(conn, binding, action, message_id, value, next_read, now)
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
    handoff: object = None,
) -> dict[str, Any]:
    """Caller owns eligibility, receiver fencing and the operation transaction."""
    if cursor is not None and action != "fetch" and action != "status":
        raise AdmissionError("invalid_inbox_arguments")
    if action in HANDOFF_ACTIONS:
        return _handoff(
            conn, binding, action, ids, next_read, now=now, limit=limit, max_bytes=max_bytes, handoff=handoff
        )
    if next_read is not None or handoff is not None:
        raise AdmissionError("invalid_inbox_arguments")
    if action == "ack":
        if not ids or len(ids) > limit:
            raise AdmissionError("invalid_ack_ids")
        if any(not re.fullmatch(r"[0-9a-f]{32}", value) for value in ids):
            raise AdmissionError("invalid_message_id")
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
