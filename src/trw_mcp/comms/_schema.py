"""Schema3 and read-only correspondence validation for the comms store facade.

Validates retained state, not tamper-proof history. A cooperative actor rewriting
all consistent evidence remains outside the security boundary. No repair/reset.
"""

from __future__ import annotations

import math
import re
import sqlite3
from itertools import pairwise
from pathlib import Path

from trw_mcp.comms._envelope import DELIVERY_CLASSES, KINDS, MEMBER_ID
from trw_mcp.comms._policy import MAX_COUNTER, REFUSALS

SCHEMA_VERSION = 3
SCHEMA = """
CREATE TABLE schema_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE groups (
 group_id TEXT PRIMARY KEY,formation_id TEXT NOT NULL,manifest_path TEXT NOT NULL,
 created_at REAL NOT NULL,group_time REAL NOT NULL,closed INTEGER NOT NULL DEFAULT 0,
 group_limit INTEGER NOT NULL,body_limit INTEGER NOT NULL,outstanding_limit INTEGER NOT NULL,
 rate_limit INTEGER NOT NULL,charge INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE endpoints (
 group_id TEXT NOT NULL,member_id TEXT NOT NULL,incarnation TEXT NOT NULL,
 session_id TEXT NOT NULL,run_path TEXT NOT NULL,enrolled_at REAL NOT NULL,
 last_seen_at REAL NOT NULL,lease_expires_at REAL NOT NULL,PRIMARY KEY (group_id,member_id)
);
CREATE TABLE admissions (
 group_id TEXT NOT NULL,sender_member_id TEXT NOT NULL,request_key TEXT NOT NULL,
 recipient_member_id TEXT NOT NULL,kind TEXT NOT NULL,delivery_class TEXT NOT NULL,
 body TEXT NOT NULL,message_id TEXT PRIMARY KEY,recipient_incarnation TEXT NOT NULL,
 admitted_at REAL NOT NULL,state TEXT NOT NULL,
 UNIQUE(group_id,sender_member_id,request_key)
);
CREATE TABLE milestones (
 message_id TEXT NOT NULL,fact TEXT NOT NULL,at REAL NOT NULL,PRIMARY KEY(message_id,fact)
);
CREATE TABLE refusal_counts (
 group_id TEXT NOT NULL,reason TEXT NOT NULL,count INTEGER NOT NULL,PRIMARY KEY(group_id,reason)
);
"""


class SchemaVersionError(ValueError):
    """An unsupported schema must not be migrated implicitly."""


def check(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError(detail)


def ordered_times(values: tuple[object, ...]) -> bool:
    return all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
        for value in values
    ) and all(float(str(left)) <= float(str(right)) for left, right in pairwise(values))


def _hex(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _member(value: object) -> bool:
    return isinstance(value, str) and MEMBER_ID.fullmatch(value) is not None


def _bounded_int(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _groups(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    groups = {}
    for row in conn.execute("SELECT * FROM groups"):
        check(
            _hex(row["group_id"]) and _member(row["formation_id"]) and _text(row["manifest_path"]),
            "invalid group identity",
        )
        check(Path(row["manifest_path"]).is_absolute(), "nonabsolute group path")
        check(ordered_times((row["created_at"], row["group_time"])), "invalid group clock")
        for name, low, high in (
            ("closed", 0, 1),
            ("group_limit", 1, 4096),
            ("body_limit", 1, 65536),
            ("outstanding_limit", 1, 256),
            ("rate_limit", 1, 256),
            ("charge", 0, row["group_limit"]),
        ):
            check(_bounded_int(row[name], low, high), "invalid group policy/accounting")
        groups[row["group_id"]] = row
    return groups


def _endpoints(conn: sqlite3.Connection, groups: dict[str, sqlite3.Row]) -> dict[tuple[str, str], sqlite3.Row]:
    endpoints = {}
    for row in conn.execute("SELECT * FROM endpoints"):
        check(row["group_id"] in groups and _member(row["member_id"]), "orphan/invalid endpoint")
        check(
            _hex(row["incarnation"]) and _text(row["session_id"]) and _text(row["run_path"]),
            "invalid endpoint identity",
        )
        check(Path(row["run_path"]).is_absolute(), "nonabsolute endpoint path")
        check(
            ordered_times((row["enrolled_at"], row["last_seen_at"], row["lease_expires_at"])), "invalid endpoint clock"
        )
        check(row["last_seen_at"] <= groups[row["group_id"]]["group_time"], "endpoint ahead of persisted clock")
        endpoints[(row["group_id"], row["member_id"])] = row
    return endpoints


def _admissions(
    conn: sqlite3.Connection, groups: dict[str, sqlite3.Row], endpoints: dict[tuple[str, str], sqlite3.Row]
) -> dict[str, sqlite3.Row]:
    rows = {}
    counts = dict.fromkeys(groups, 0)
    outstanding: dict[tuple[str, str], int] = {}
    sender_times: dict[tuple[str, str], list[float]] = {}
    for row in conn.execute("SELECT rowid AS append_id,* FROM admissions"):
        check(_bounded_int(row["append_id"], 1, MAX_COUNTER), "invalid append order")
        check(row["group_id"] in groups and _hex(row["message_id"]), "orphan/invalid admission")
        group = groups[row["group_id"]]
        check(_member(row["sender_member_id"]) and _member(row["recipient_member_id"]), "invalid admission member")
        check(row["kind"] in KINDS and row["delivery_class"] in DELIVERY_CLASSES, "invalid admission enum")
        check(
            isinstance(row["request_key"], str) and 1 <= len(row["request_key"].encode("utf-8")) <= 128,
            "invalid retained key",
        )
        check(
            isinstance(row["body"], str) and len(row["body"].encode("utf-8")) <= group["body_limit"],
            "invalid retained body",
        )
        check(_hex(row["recipient_incarnation"]), "invalid recipient incarnation")
        check(ordered_times((group["created_at"], row["admitted_at"], group["group_time"])), "invalid admission time")
        check(row["state"] in ("pending", "acked", "expired"), "invalid admission state")
        key = (row["group_id"], row["recipient_member_id"])
        if row["state"] == "pending":
            check(
                key in endpoints and endpoints[key]["incarnation"] == row["recipient_incarnation"],
                "outstanding incarnation orphan",
            )
            outstanding[key] = outstanding.get(key, 0) + 1
            check(outstanding[key] <= group["outstanding_limit"], "outstanding limit inconsistent")
        sender_times.setdefault((row["group_id"], row["sender_member_id"]), []).append(row["admitted_at"])
        counts[row["group_id"]] += 1
        rows[row["message_id"]] = row
    for (group_id, _sender), times in sender_times.items():
        ordered = sorted(times)
        start = 0
        for end, now in enumerate(ordered):
            while start <= end and now - ordered[start] >= 60:
                start += 1
            check(end - start + 1 <= groups[group_id]["rate_limit"], "historical sender rate exceeds immutable policy")
    for group_id, group in groups.items():
        check(counts[group_id] == group["charge"], "lifetime charge differs from retained admissions")
    return rows


def _milestones(conn: sqlite3.Connection, groups: dict[str, sqlite3.Row], admissions: dict[str, sqlite3.Row]) -> None:
    facts: dict[str, dict[str, float]] = {}
    for row in conn.execute("SELECT * FROM milestones"):
        check(row["message_id"] in admissions, "orphan milestone")
        admission = admissions[row["message_id"]]
        check(row["fact"] in ("admitted", "fetch_prepared", "acked", "expired"), "unknown milestone")
        check(
            ordered_times((admission["admitted_at"], row["at"], groups[admission["group_id"]]["group_time"])),
            "invalid milestone time",
        )
        facts.setdefault(row["message_id"], {})[row["fact"]] = row["at"]
    for message_id, admission in admissions.items():
        message_facts = facts.get(message_id, {})
        check(message_facts.get("admitted") == admission["admitted_at"], "missing or mismatched admitted fact")
        terminals = set(message_facts) & {"acked", "expired"}
        expected = set() if admission["state"] == "pending" else {admission["state"]}
        check(terminals == expected, "terminal status/milestone disagreement")
        if terminals and "fetch_prepared" in message_facts:
            check(
                message_facts["fetch_prepared"] <= message_facts[next(iter(terminals))], "fetch fact after termination"
            )


def verify(conn: sqlite3.Connection) -> None:
    """Exact schema and row correspondence, in caller-owned consistent snapshot."""
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("integrity check failed")
    metadata = conn.execute("SELECT key,value FROM schema_meta").fetchall()
    check(len(metadata) == 1 and metadata[0][0] == "schema_version", "invalid schema metadata")
    if metadata[0][1] != str(SCHEMA_VERSION):
        raise SchemaVersionError("unsupported schema version; no implicit migration")

    def normalize(sql: str) -> str:
        return re.sub(r"\s+", " ", sql.strip()).lower()

    expected = {normalize(statement) for statement in SCHEMA.split(";") if statement.strip()}
    actual = {
        normalize(str(row[0])) for row in conn.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    check(actual == expected, "unexpected or incomplete schema")
    groups = _groups(conn)
    admissions = _admissions(conn, groups, _endpoints(conn, groups))
    _milestones(conn, groups, admissions)
    for row in conn.execute("SELECT * FROM refusal_counts"):
        check(row["group_id"] in groups and row["reason"] in REFUSALS, "invalid refusal category or group")
        check(_bounded_int(row["count"], 1, MAX_COUNTER), "invalid refusal counter")
