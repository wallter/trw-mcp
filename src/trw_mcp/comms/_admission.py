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


def admit(
    conn: sqlite3.Connection,
    snapshot: CallerSnapshot,
    envelope: Envelope,
    now: float,
    *,
    ttl_seconds: int,
    require_live: bool = False,
) -> dict[str, Any]:
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
    if require_live:
        # PRD-CORE-276 scoped notify keeps its contract: reach the owners listening NOW,
        # reporting (never storing for) an offline one. FR13 durability is for direct sends.
        _assert_live_recipient(conn, binding.group_id, recipient, now)
    # FR13: a direct send is stored for a joined/active member whatever its lease or generation.
    check_limits(conn, group, binding.member_id, recipient.member_id, envelope.body, now)
    return receipt(insert_admission(conn, binding, envelope, now, ttl_seconds=ttl_seconds))


def _assert_live_recipient(conn: sqlite3.Connection, group_id: str, recipient: Any, now: float) -> None:
    endpoint = conn.execute(
        "SELECT * FROM endpoints WHERE group_id=? AND member_id=?", (group_id, recipient.member_id)
    ).fetchone()
    if endpoint is None or endpoint["lease_expires_at"] <= now:
        raise AdmissionError("recipient_unavailable")
    try:
        endpoint_run = str(Path(endpoint["run_path"]).resolve())
    except (OSError, RuntimeError) as exc:  # unavailable/looping aliases cannot authorize delivery
        raise AdmissionError("recipient_binding_mismatch") from exc
    if endpoint_run != recipient.run_path or endpoint["session_id"] != recipient.pin_key:
        raise AdmissionError("recipient_binding_mismatch")


def observe_recipient(
    conn: sqlite3.Connection, group_id: str, member_id: str, now: float, *, idle_horizon_seconds: int
) -> dict[str, Any]:
    """Send-time observation of the recipient, returned BESIDE the immutable receipt (FR13).

    Kept out of the receipt on purpose: a receipt is provenance that an exact retry
    must return unchanged, while availability is a moment's observation.
    """
    endpoint = conn.execute(
        "SELECT * FROM endpoints WHERE group_id=? AND member_id=?", (group_id, member_id)
    ).fetchone()
    return {
        "availability": availability(endpoint, now, idle_horizon_seconds=idle_horizon_seconds),
        "generation": int(endpoint["generation"]) if endpoint is not None else 0,
        "wake": WAKE_UNOBSERVED,
    }


#: FR13 wake vocabulary is shared with lane B; a value is emitted only from observed
#: adapter evidence, never inferred from a client name. Nothing is observed yet.
WAKE_UNOBSERVED = "none"


def availability(endpoint: sqlite3.Row | None, now: float, *, idle_horizon_seconds: int) -> str:
    """Advisory only (FR12): activity, not attention. Never gates admission, fetch or ACK."""
    if endpoint is None:
        return "never_enrolled"
    return availability_of(
        float(endpoint["lease_expires_at"]), float(endpoint["last_seen_at"]), now, idle_horizon_seconds
    )


def availability_of(lease_expires_at: float, last_seen_at: float, now: float, idle_horizon_seconds: int) -> str:
    """One definition shared by send observations and peers rows, so they cannot disagree."""
    if now < lease_expires_at:
        return "live"
    if now - last_seen_at < idle_horizon_seconds:
        return "idle"  # would still be inside a message's delivery horizon if it returned now
    return "absent"
