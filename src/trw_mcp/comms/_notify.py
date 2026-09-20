"""Scoped fan-out: one notify addressed to declared ground (PRD-CORE-276).

Its own module rather than more branches in ``_admission``: that file owns ONE
send decision, and scoped notify is a bounded loop over that decision, not a
second decision procedure. Everything authority-bearing — eligibility, liveness,
binding correspondence, every admission limit — stays in ``admit`` and is
re-checked per recipient exactly as a direct send is.

Atomicity is inherited, not added. The caller already holds the facade's single
``BEGIN IMMEDIATE`` transaction, so N admissions commit together or not at all;
a partially delivered notify would be a false record of who was told.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from trw_mcp.comms._admission import admit
from trw_mcp.comms._envelope import AdmissionError, DeliveryClass, Envelope, MessageKind
from trw_mcp.comms._identity import CallerSnapshot, RecipientSnapshot
from trw_mcp.comms._scope import (
    has_control_characters,
    intersects,
    scope_digest,
    scope_digest_of,
    shard_key,
    shard_prefix,
)

#: Caller key ceiling for a notify. Tighter than the 128-byte envelope bound
#: because the derived shard key wraps it: 34 bytes of prefix plus a member id
#: of up to 64 must still fit, and refusing early is clearer than refusing
#: later with a reason that names a key the caller never wrote.
MAX_NOTIFY_KEY_BYTES = 128

#: Per-recipient refusals that skip that recipient instead of failing the notify.
#: Each means "this peer cannot receive right now", which is information for the
#: sender, not a reason to withhold the message from everyone else. Every OTHER
#: refusal — the admission limits especially — aborts the whole fan-out.
SKIPPABLE = frozenset({"recipient_not_eligible", "recipient_unavailable", "recipient_binding_mismatch"})

#: Reported for a member that the scope reaches but an earlier retained notify
#: did not. The message is NOT sent to it: a request key identifies one fan-out,
#: and silently widening that fan-out on a retry would make the retry a
#: different message wearing the same name.
NOT_RETAINED = "not_in_retained_notify"


def candidates(snapshot: CallerSnapshot, scope: str) -> list[RecipientSnapshot]:
    """Members whose DECLARED ownership intersects the scope, sender excluded.

    Ordered by member id so a fan-out is deterministic: the same notify admits
    the same recipients in the same order, which is what lets the retry path
    compare sets rather than guess.
    """
    sender = snapshot.binding.member_id
    matched = [
        peer
        for peer in snapshot.recipients
        if peer.member_id != sender and any(intersects(scope, declared) for declared in peer.owned_paths)
    ]
    return sorted(matched, key=lambda peer: peer.member_id)


def _retained(conn: sqlite3.Connection, group_id: str, sender: str, request_key: str) -> list[sqlite3.Row]:
    """Shards of an earlier notify under this key, from storage alone.

    Every shard, whatever scope produced it: the prefix is over the caller's key
    only, and the scope digest sits further inside the key. That is what lets
    the retry path ASK a retained fan-out which scope it was for, without the
    schema carrying a scope column.
    """
    prefix = shard_prefix(request_key)
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT request_key, recipient_member_id FROM admissions "
        "WHERE group_id=? AND sender_member_id=? AND substr(request_key,1,?)=?",
        (group_id, sender, len(prefix), prefix),
    ).fetchall()
    return rows


def _raw_key_taken(conn: sqlite3.Connection, group_id: str, sender: str, request_key: str) -> bool:
    """Has this sender already used this key for a DIRECT message?

    A request key identifies one message from one sender. Without this, the same
    key could name a direct send and a scoped notify at the same time, because
    the notify stores derived keys and the direct send stores the raw one — two
    live messages under one name, which is exactly what the key exists to stop.
    """
    row = conn.execute(
        "SELECT 1 FROM admissions WHERE group_id=? AND sender_member_id=? AND request_key=?",
        (group_id, sender, request_key),
    ).fetchone()
    return row is not None


def notify(
    conn: sqlite3.Connection,
    snapshot: CallerSnapshot,
    scope: str,
    request_key: str,
    body: str,
    kind: MessageKind,
    delivery_class: DeliveryClass,
    now: float,
    *,
    max_recipients: int,
    ttl_seconds: int,
) -> dict[str, Any]:
    """Admit one bounded fan-out, or refuse the whole of it."""
    if not conn.in_transaction:
        raise RuntimeError("scoped notify requires the operation transaction")
    binding = snapshot.binding
    if not 1 <= len(request_key.encode("utf-8")) <= MAX_NOTIFY_KEY_BYTES or has_control_characters(request_key):
        raise AdmissionError("invalid_request_key")
    reachable = candidates(snapshot, scope)
    if not reachable:
        raise AdmissionError("scope_matches_no_peer")
    if len(reachable) > max_recipients:
        # Before the first insert, deliberately: exceeding the ceiling costs a
        # refusal, never a partial fan-out that the sender then has to reason about.
        raise AdmissionError("scope_too_broad")

    if _raw_key_taken(conn, binding.group_id, binding.member_id, request_key):
        raise AdmissionError("idempotency_conflict")
    rows = _retained(conn, binding.group_id, binding.member_id, request_key)
    retained = {str(row["recipient_member_id"]) for row in rows}
    wanted = scope_digest(scope)
    if any(scope_digest_of(str(row["request_key"])) != wanted for row in rows):
        # Same key, different scope. Even when it happens to resolve to the same
        # recipients, handing back the first call's receipts would report success
        # for a scope nothing was ever sent about.
        raise AdmissionError("idempotency_conflict")
    if retained and not retained <= {peer.member_id for peer in reachable}:
        # The same key now reaches a set that does not contain everyone it
        # reached before. That is a different message, not a retry.
        raise AdmissionError("idempotency_conflict")

    # Computed once, before anything is admitted, because it is the answer to a
    # question the sender must be able to ask of the RESPONSE: which members
    # that this scope reaches will get nothing under this key? On a first send
    # it is empty; on a retry it names the growth the frozen fan-out excludes.
    frozen = retained or {peer.member_id for peer in reachable}
    not_delivered_to = {peer.member_id: NOT_RETAINED for peer in reachable if peer.member_id not in frozen}

    receipts: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    for peer in reachable:
        if peer.member_id in not_delivered_to:
            skipped[peer.member_id] = NOT_RETAINED
            continue
        envelope = Envelope(peer.member_id, shard_key(request_key, scope, peer.member_id), body, kind, delivery_class)
        try:
            receipts[peer.member_id] = admit(conn, snapshot, envelope, now, ttl_seconds=ttl_seconds, require_live=True)
        except AdmissionError as exc:
            if exc.reason not in SKIPPABLE:
                raise
            skipped[peer.member_id] = exc.reason
    if not receipts:
        # Every reachable peer declined delivery. Refuse with the peer-shaped
        # reason rather than the scope-shaped one, so the sender can tell
        # "nobody owns this" from "the owner is not listening".
        raise AdmissionError("recipient_unavailable")
    return {"scope": scope, "recipients": receipts, "skipped": skipped, "not_delivered_to": not_delivered_to}
