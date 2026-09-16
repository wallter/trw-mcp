"""One-lock send decision for the comms facade (CORE274 FR02/03/07/10)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from trw_mcp.comms._envelope import AdmissionError, Envelope, receipt
from trw_mcp.comms._identity import CallerSnapshot
from trw_mcp.comms._messages import insert_admission
from trw_mcp.comms._policy import check_limits
from trw_mcp.comms._scope import is_shard_key, shard_prefix


def admit(conn: sqlite3.Connection, snapshot: CallerSnapshot, envelope: Envelope, now: float) -> dict[str, Any]:
    """Called only inside the facade's validated BEGIN IMMEDIATE transaction."""
    if not conn.in_transaction:
        raise RuntimeError("send admission requires operation transaction")
    envelope.validate()
    binding = snapshot.binding
    existing = conn.execute(
        "SELECT * FROM admissions WHERE group_id=? AND sender_member_id=? AND request_key=?",
        (binding.group_id, binding.member_id, envelope.request_key),
    ).fetchone()
    if existing is not None:
        if not envelope.matches(existing):
            raise AdmissionError("idempotency_conflict")
        return receipt(existing)
    if not is_shard_key(envelope.request_key):
        # A request key names ONE message from one sender. A direct send and a
        # scoped notify store their rows under different keys (raw versus
        # derived), so without this a caller could have two live messages under
        # one name and a retry of either would find only its own.
        prefix = shard_prefix(envelope.request_key)
        taken = conn.execute(
            "SELECT 1 FROM admissions WHERE group_id=? AND sender_member_id=? AND substr(request_key,1,?)=?",
            (binding.group_id, binding.member_id, len(prefix), prefix),
        ).fetchone()
        if taken is not None:
            raise AdmissionError("idempotency_conflict")
    group = conn.execute("SELECT * FROM groups WHERE group_id=?", (binding.group_id,)).fetchone()
    if group["closed"]:
        raise AdmissionError("group_closed")
    recipient = next((peer for peer in snapshot.recipients if peer.member_id == envelope.recipient_member_id), None)
    if recipient is None or not recipient.eligible:
        raise AdmissionError("recipient_not_eligible")
    endpoint = conn.execute(
        "SELECT * FROM endpoints WHERE group_id=? AND member_id=?", (binding.group_id, recipient.member_id)
    ).fetchone()
    if endpoint is None or endpoint["lease_expires_at"] <= now:
        raise AdmissionError("recipient_unavailable")
    try:
        endpoint_run = str(Path(endpoint["run_path"]).resolve())
    except (OSError, RuntimeError) as exc:  # unavailable/looping aliases cannot authorize delivery
        raise AdmissionError("recipient_binding_mismatch") from exc
    if endpoint_run != recipient.run_path or endpoint["session_id"] != recipient.pin_key:
        raise AdmissionError("recipient_binding_mismatch")
    check_limits(conn, group, binding.member_id, recipient.member_id, envelope.body, now)
    return receipt(insert_admission(conn, binding, envelope, str(endpoint["incarnation"]), now))
