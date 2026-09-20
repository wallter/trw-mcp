"""Admission, delivery and expiry facts for the public comms facade; no delivery inference.

Messages are addressed to the MEMBER (PRD-CORE-274 FR13). ``recipient_incarnation``
records the incarnation that last prepared the row for fetch (``NEVER_PREPARED`` until
then), which is what lets a new generation's first fetch count as a redelivery.
"""

from __future__ import annotations

import math
import sqlite3
import sys
import uuid

from trw_mcp.comms._envelope import AdmissionError, Envelope, MessageState
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
