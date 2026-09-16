"""Admission and replacement facts for the public comms facade; no delivery inference."""

from __future__ import annotations

import sqlite3
import uuid

from trw_mcp.comms._envelope import AdmissionError, Envelope
from trw_mcp.comms._identity import CallerBinding


def insert_admission(
    conn: sqlite3.Connection, binding: CallerBinding, envelope: Envelope, incarnation: str, now: float
) -> sqlite3.Row:
    message_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO admissions(group_id,sender_member_id,request_key,recipient_member_id,kind,delivery_class,"
        "body,message_id,recipient_incarnation,admitted_at,state) VALUES (?,?,?,?,?,?,?,?,?,?,'pending')",
        (
            binding.group_id,
            binding.member_id,
            envelope.request_key,
            envelope.recipient_member_id,
            envelope.kind,
            envelope.delivery_class,
            envelope.body,
            message_id,
            incarnation,
            now,
        ),
    )
    conn.execute("UPDATE groups SET charge=charge+1 WHERE group_id=?", (binding.group_id,))
    conn.execute("INSERT INTO milestones(message_id,fact,at) VALUES (?,'admitted',?)", (message_id, now))
    row: sqlite3.Row = conn.execute("SELECT * FROM admissions WHERE message_id=?", (message_id,)).fetchone()
    return row


def expire_replaced(conn: sqlite3.Connection, group_id: str, member_id: str, incarnation: str, now: float) -> None:
    """Same operation transaction as endpoint replacement; retained charge never decreases."""
    conn.execute(
        "INSERT INTO milestones(message_id,fact,at) SELECT message_id,'expired',? FROM admissions "
        "WHERE group_id=? AND recipient_member_id=? AND recipient_incarnation=? AND state='pending'",
        (now, group_id, member_id, incarnation),
    )
    conn.execute(
        "UPDATE admissions SET state='expired' WHERE group_id=? AND recipient_member_id=? "
        "AND recipient_incarnation=? AND state='pending'",
        (group_id, member_id, incarnation),
    )


def prepare_fetch(conn: sqlite3.Connection, rows: list[sqlite3.Row], now: float) -> None:
    """Preparation records only returned rows, never consumes pending traffic."""
    for row in rows:
        conn.execute(
            "INSERT OR IGNORE INTO milestones(message_id,fact,at) VALUES (?,'fetch_prepared',?)",
            (row["message_id"], now),
        )


def validate_ack(
    conn: sqlite3.Connection, binding: CallerBinding, incarnation: str, ids: list[str]
) -> list[sqlite3.Row]:
    """Whole-batch authorization precedes every ACK mutation, including retries."""
    rows: list[sqlite3.Row] = []
    for message_id in ids:
        row = conn.execute("SELECT * FROM admissions WHERE message_id=?", (message_id,)).fetchone()
        if (
            row is None
            or row["group_id"] != binding.group_id
            or row["recipient_member_id"] != binding.member_id
            or row["recipient_incarnation"] != incarnation
            or row["state"] not in ("pending", "acked")
        ):
            raise AdmissionError("ack_not_authorized")
        rows.append(row)
    return rows


def acknowledge(conn: sqlite3.Connection, rows: list[sqlite3.Row], now: float) -> None:
    for row in rows:
        if row["state"] == "acked":
            continue
        conn.execute("INSERT INTO milestones(message_id,fact,at) VALUES (?,'acked',?)", (row["message_id"], now))
        conn.execute("UPDATE admissions SET state='acked' WHERE message_id=?", (row["message_id"],))
