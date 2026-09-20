"""Immutable group-birth admission policy and bounded refusal accounting."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from trw_mcp.comms._envelope import AdmissionError, MessageState
from trw_mcp.comms._identity import CallerBinding

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

MAX_COUNTER = 9223372036854775807
REFUSALS = frozenset(
    {
        "group_closed",
        "invalid_recipient",
        "invalid_message_enum",
        "invalid_utf8",
        "invalid_request_key",
        "idempotency_conflict",
        "body_too_large",
        "group_admission_limit",
        "recipient_outstanding_limit",
        "sender_rate_limit",
        "recipient_not_eligible",
        "recipient_unavailable",
        "recipient_binding_mismatch",
        "live_endpoint_held_by_other_incarnation",
        "endpoint_replaced_by_newer_incarnation",
        "no_endpoint_for_member",
        "invalid_cursor",
        "response_too_small",
        "receiver_lease_expired",
        "invalid_inbox_arguments",
        "invalid_ack_ids",
        "ack_not_authorized",
        "response_body_policy_incompatible",
        "invalid_scope",
        "ambiguous_addressing",
        "scope_too_broad",
        "scope_matches_no_peer",
        "group_storage_budget",
    }
)


@dataclass(frozen=True)
class AdmissionPolicy:
    group_limit: int
    body_limit: int
    outstanding_limit: int
    rate_limit: int
    body_budget: int = 16777216

    @classmethod
    def from_config(cls, config: TRWConfig) -> AdmissionPolicy:
        return cls(
            config.comms_group_row_limit,
            config.comms_body_max_bytes,
            config.comms_recipient_outstanding_limit,
            config.comms_sender_admissions_per_minute,
            config.comms_group_body_budget_bytes,
        )


def ensure_group(conn: sqlite3.Connection, binding: CallerBinding, now: float, policy: AdmissionPolicy) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO groups(group_id,formation_id,manifest_path,created_at,group_time,closed,"
        "group_limit,body_limit,outstanding_limit,rate_limit,charge,body_budget) VALUES (?,?,?,?,?,0,?,?,?,?,0,?)",
        (
            binding.group_id,
            binding.formation_id,
            str(binding.manifest_path),
            now,
            now,
            policy.group_limit,
            policy.body_limit,
            policy.outstanding_limit,
            policy.rate_limit,
            policy.body_budget,
        ),
    )
    row = conn.execute("SELECT formation_id,manifest_path FROM groups WHERE group_id=?", (binding.group_id,)).fetchone()
    if (
        row["formation_id"] != binding.formation_id
        or Path(row["manifest_path"]).resolve() != binding.manifest_path.resolve()
    ):
        from trw_mcp.comms._store import StoreError, StoreRefusal

        raise StoreError(StoreRefusal.CORRUPT, "persisted group identity contradicts trusted binding")


def count_refusal(conn: sqlite3.Connection, group_id: str, reason: str) -> None:
    if reason not in REFUSALS:
        raise ValueError("refusal category not admitted to bounded accounting")
    conn.execute(
        "INSERT INTO refusal_counts(group_id,reason,count) VALUES (?,?,1) "
        "ON CONFLICT(group_id,reason) DO UPDATE SET count=CASE WHEN count<? THEN count+1 ELSE count END",
        (group_id, reason, MAX_COUNTER),
    )


def remaining_capacity(conn: sqlite3.Connection, group_id: str) -> dict[str, int]:
    """FR15: what the group can still admit, so exhaustion is visible before it refuses."""
    group = conn.execute("SELECT group_limit, charge, body_budget FROM groups WHERE group_id=?", (group_id,)).fetchone()
    live = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(CAST(body AS BLOB))),0) FROM admissions WHERE group_id=?", (group_id,)
    ).fetchone()[0]
    return {"rows": int(group[0]) - int(group[1]), "body_bytes": int(group[2]) - int(live)}


def check_limits(
    conn: sqlite3.Connection, group: sqlite3.Row, sender: str, recipient: str, body: str, now: float
) -> None:
    if len(body.encode("utf-8")) > group["body_limit"]:
        raise AdmissionError("body_too_large")
    if group["charge"] >= group["group_limit"]:
        raise AdmissionError("group_admission_limit")
    live_bytes = conn.execute(
        "SELECT COALESCE(SUM(LENGTH(CAST(body AS BLOB))),0) FROM admissions WHERE group_id=?", (group["group_id"],)
    ).fetchone()[0]
    if live_bytes + len(body.encode("utf-8")) > group["body_budget"]:
        raise AdmissionError("group_storage_budget")  # FR15: refuse, never evict
    outstanding = conn.execute(
        "SELECT COUNT(*) FROM admissions WHERE group_id=? AND recipient_member_id=? AND state=?",
        (group["group_id"], recipient, MessageState.PENDING.value),
    ).fetchone()[0]
    if outstanding >= group["outstanding_limit"]:
        raise AdmissionError("recipient_outstanding_limit")
    recent = conn.execute(
        "SELECT COUNT(*) FROM admissions WHERE group_id=? AND sender_member_id=? AND ?-admitted_at<60 AND admitted_at<=?",
        (group["group_id"], sender, now, now),
    ).fetchone()[0]
    if recent >= group["rate_limit"]:
        raise AdmissionError("sender_rate_limit")
