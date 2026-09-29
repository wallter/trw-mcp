"""Admission, delivery, expiry and handoff facts for the public comms facade; no delivery inference.

This module is the ONLY writer of ``milestones`` and ``handoff_reports`` (PRD-CORE-322
FR08, enforced by the census in ``tests/comms/test_milestones.py``). The ledger stores
no actor, so the handoff role rule (the recipient accepts and reports, the sender
completes) is enforced here, at write time, and nowhere else.

Messages are addressed to the MEMBER (PRD-CORE-274 FR13). ``recipient_incarnation``
records the incarnation that last prepared the row for fetch (``NEVER_PREPARED`` until
then), which is what lets a new generation's first fetch count as a redelivery.
"""

from __future__ import annotations

import math
import sqlite3
import sys
import uuid

from trw_mcp.comms._envelope import AdmissionError, Envelope, MessageState, valid_next_read
from trw_mcp.comms._identity import CallerBinding


def insert_admission(
    conn: sqlite3.Connection,
    binding: CallerBinding,
    envelope: Envelope,
    now: float,
    *,
    ttl_seconds: int,
) -> sqlite3.Row:
    """Admit one member-addressed row (FR13); ``recipient_incarnation`` starts as NEVER_PREPARED."""
    message_id = uuid.uuid4().hex
    # trw:intentional saturate, never overflow: a huge finite group clock plus the TTL
    # must stay a finite, ordered expiry rather than become inf and corrupt the row.
    expires_at = now + float(ttl_seconds)
    if not math.isfinite(expires_at):
        expires_at = sys.float_info.max
    conn.execute(
        "INSERT INTO admissions(group_id,sender_member_id,request_key,recipient_member_id,kind,delivery_class,"
        "body,message_id,recipient_incarnation,admitted_at,state,expires_at,canonical_sha256) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            binding.group_id,
            binding.member_id,
            envelope.request_key,
            envelope.recipient_member_id,
            envelope.kind,
            envelope.delivery_class,
            envelope.body,
            message_id,
            NEVER_PREPARED,
            now,
            MessageState.PENDING.value,
            expires_at,
            envelope.canonical_sha256(),
        ),
    )
    conn.execute("UPDATE groups SET charge=charge+1 WHERE group_id=?", (binding.group_id,))
    conn.execute("INSERT INTO milestones(message_id,fact,at) VALUES (?,'admitted',?)", (message_id, now))
    row: sqlite3.Row = conn.execute("SELECT * FROM admissions WHERE message_id=?", (message_id,)).fetchone()
    return row


#: ``recipient_incarnation`` value of a row no incarnation has prepared yet.
NEVER_PREPARED = "0" * 32


_EXPIRE_MILESTONES = (
    "INSERT OR IGNORE INTO milestones(message_id,fact,at) SELECT message_id,?,? FROM admissions "
    "WHERE group_id=? AND state=? AND "
)
_EXPIRE_ROWS = "UPDATE admissions SET state=? WHERE group_id=? AND state=? AND "
_PENDING, _EXPIRED = MessageState.PENDING.value, MessageState.EXPIRED.value


def expire_due(conn: sqlite3.Connection, group_id: str, now: float, terminal_members: frozenset[str]) -> None:
    """Expire pending rows past their deadline or addressed to a terminal member (FR13). Never retargets."""
    for condition, value in (
        ("expires_at <= ?", now),
        *(("recipient_member_id = ?", member) for member in sorted(terminal_members)),
    ):
        conn.execute(_EXPIRE_MILESTONES + condition, (_EXPIRED, now, group_id, _PENDING, value))
        conn.execute(_EXPIRE_ROWS + condition, (_EXPIRED, group_id, _PENDING, value))


def tombstone_due(conn: sqlite3.Connection, group_id: str, now: float, grace_seconds: int) -> None:
    """FR15: drop the BODY of terminal rows past deadline plus grace. The row, rowid,
    receipt fields and canonical digest stay, so cursors and exact retries are unchanged."""
    conn.execute(
        "UPDATE admissions SET body='' WHERE group_id=? AND state!=? AND body!='' AND expires_at + ? <= ?",
        (group_id, _PENDING, float(grace_seconds), now),
    )


def prepare_fetch(conn: sqlite3.Connection, rows: list[sqlite3.Row], now: float, incarnation: str) -> None:
    """Record preparation of returned rows; a new preparer counts one more delivery (FR13)."""
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO milestones(message_id,fact,at) VALUES (?,'fetch_prepared',?)",
            (row["message_id"], now),
        )
        if row["recipient_incarnation"] != incarnation:
            conn.execute(
                "UPDATE admissions SET delivery_count=delivery_count+1, recipient_incarnation=? WHERE message_id=?",
                (incarnation, row["message_id"]),
            )


def validate_ack(conn: sqlite3.Connection, binding: CallerBinding, ids: list[str]) -> list[sqlite3.Row]:
    """Whole-batch authorization precedes every ACK mutation, including retries.

    The caller's incarnation is fenced as the current one before this runs (FR12);
    the row need only be this member's, so a message prepared by an older
    generation can be acknowledged by the new one.
    """
    rows: list[sqlite3.Row] = []
    for message_id in ids:
        row = conn.execute("SELECT * FROM admissions WHERE message_id=?", (message_id,)).fetchone()
        if (
            row is None
            or row["group_id"] != binding.group_id
            or row["recipient_member_id"] != binding.member_id
            or row["state"] not in (MessageState.PENDING, MessageState.ACKED)
        ):
            raise AdmissionError("ack_not_authorized")
        rows.append(row)
    return rows


def acknowledge(conn: sqlite3.Connection, rows: list[sqlite3.Row], now: float) -> None:
    for row in rows:
        if row["state"] == MessageState.ACKED:
            continue
        conn.execute(
            "INSERT INTO milestones(message_id,fact,at) VALUES (?,?,?)",
            (row["message_id"], MessageState.ACKED.value, now),
        )
        conn.execute("UPDATE admissions SET state=? WHERE message_id=?", (MessageState.ACKED.value, row["message_id"]))


def _handoff_rows(
    conn: sqlite3.Connection, binding: CallerBinding, ids: list[str], *, as_sender: bool
) -> list[tuple[sqlite3.Row, dict[str, float]]]:
    """Whole-batch authorization for a handoff write, with each row's recorded facts.

    The caller must be the row's recipient (accept, report) or, *as_sender*, its sender
    and not also its recipient (complete): an owner never verifies its own report. A
    row outside the caller's reach refuses ``handoff_not_authorized`` before its kind is
    looked at, so a non-party learns nothing about it. Runtime callers: :func:`accept`,
    :func:`report`, :func:`complete`.
    """
    checked: list[tuple[sqlite3.Row, dict[str, float]]] = []
    for message_id in ids:
        row = conn.execute("SELECT * FROM admissions WHERE message_id=?", (message_id,)).fetchone()
        member = binding.member_id
        if (
            row is None
            or row["group_id"] != binding.group_id
            or (row["sender_member_id"] if as_sender else row["recipient_member_id"]) != member
            or (as_sender and row["recipient_member_id"] == member)
        ):
            raise AdmissionError("handoff_not_authorized")
        if row["kind"] != "request":
            raise AdmissionError("not_a_handoff")
        facts = dict(conn.execute("SELECT fact,at FROM milestones WHERE message_id=?", (message_id,)).fetchall())
        checked.append((row, facts))
    return checked


def accept(conn: sqlite3.Connection, binding: CallerBinding, ids: list[str], now: float) -> None:
    """FR02: the recipient records ``accepted``; a pending row is ACKed first, as its own fact.

    An already accepted row is an exact retry and writes nothing. An expired row
    refuses ``handoff_not_authorized``, as ACK refuses it. Runtime caller:
    ``_inbox_page.inbox_action`` for ``trw_inbox(action="accept")``.
    """
    checked = _handoff_rows(conn, binding, ids, as_sender=False)
    if any(row["state"] == MessageState.EXPIRED for row, _facts in checked):
        raise AdmissionError("handoff_not_authorized")
    acknowledge(conn, [row for row, _facts in checked], now)
    for row, facts in checked:
        if "accepted" not in facts:
            conn.execute("INSERT INTO milestones(message_id,fact,at) VALUES (?,'accepted',?)", (row["message_id"], now))


def report(conn: sqlite3.Connection, binding: CallerBinding, message_id: str, next_read: object, now: float) -> None:
    """FR03: the owner records ``reported`` and its next-read pointer with ONE timestamp.

    The pointer is untrusted data (NFR02), checked with the verifier's own predicate.
    An identical retry writes nothing; a different pointer refuses, since the record
    is append-only. Runtime caller: ``_inbox_page.inbox_action`` for ``action="report"``.
    """
    if not valid_next_read(next_read):
        raise AdmissionError("invalid_next_read")
    ((row, facts),) = _handoff_rows(conn, binding, [message_id], as_sender=False)
    if "accepted" not in facts:
        raise AdmissionError("handoff_not_accepted")
    if "reported" in facts:
        stored = conn.execute("SELECT next_read FROM handoff_reports WHERE message_id=?", (message_id,)).fetchone()
        if stored is None or stored[0] != next_read:
            raise AdmissionError("handoff_already_reported")
        return
    conn.execute("INSERT INTO milestones(message_id,fact,at) VALUES (?,'reported',?)", (row["message_id"], now))
    conn.execute(
        "INSERT INTO handoff_reports(message_id,next_read,reported_at) VALUES (?,?,?)",
        (row["message_id"], next_read, now),
    )


def complete(conn: sqlite3.Connection, binding: CallerBinding, ids: list[str], now: float) -> None:
    """FR04: only the requester records ``completed``, and only after a report.

    Records that the requester closed the handoff, not that the work is correct. An
    already completed row writes nothing. Runtime caller: ``_inbox_page.inbox_action``
    for ``trw_inbox(action="complete")``.
    """
    checked = _handoff_rows(conn, binding, ids, as_sender=True)
    if any("reported" not in facts for _row, facts in checked):
        raise AdmissionError("handoff_not_reported")
    for row, facts in checked:
        if "completed" not in facts:
            conn.execute(
                "INSERT INTO milestones(message_id,fact,at) VALUES (?,'completed',?)", (row["message_id"], now)
            )
