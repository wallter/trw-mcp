"""Schema v4 (v3 plus fixed column steps) and read-only correspondence validation.

One DDL path: a fresh v4 mailbox is the v3 DDL followed by ``V4_STEPS``, which is
exactly what the explicit FR16 upgrade applies, so migrated and fresh files carry
identical ``sqlite_master`` text (PRD-CORE-274 FR16). The expected text for each
version is derived by running that path in memory, never hand-maintained.

Validates retained state, not tamper-proof history. A cooperative actor rewriting
all consistent evidence remains outside the security boundary. No repair/reset.
"""

from __future__ import annotations

import functools
import math
import re
import sqlite3
from itertools import pairwise
from pathlib import Path

from trw_mcp.comms._envelope import (
    DELIVERY_CLASSES,
    KINDS,
    MEMBER_ID,
    MESSAGE_STATES,
    MILESTONE_FACTS,
    TERMINAL_MESSAGE_STATES,
    MessageState,
)
from trw_mcp.comms._policy import MAX_COUNTER, REFUSALS

SCHEMA_VERSION = 4
V3_SCHEMA = """
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

#: The FR16 upgrade steps, applied in order and nowhere else. ``protocol`` defaults
#: to 3 so a migrated endpoint row is truthfully labelled as enrolled by a v3 build.
V4_STEPS: tuple[str, ...] = (
    "ALTER TABLE admissions ADD COLUMN expires_at REAL",
    "ALTER TABLE admissions ADD COLUMN delivery_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE admissions ADD COLUMN canonical_sha256 TEXT",
    "ALTER TABLE endpoints ADD COLUMN generation INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE endpoints ADD COLUMN protocol INTEGER NOT NULL DEFAULT 3",
    # FR15: live body bytes, snapshotted at group birth like the other admission policy.
    "ALTER TABLE groups ADD COLUMN body_budget INTEGER NOT NULL DEFAULT 16777216",
)
#: The endpoint protocol a v4 build records at enroll.
ENDPOINT_PROTOCOL = 4


def ddl_statements(version: int) -> list[str]:
    """Every DDL statement that builds a schema of *version*, in order."""
    statements = [statement for statement in V3_SCHEMA.split(";") if statement.strip()]
    return statements + list(V4_STEPS) if version >= 4 else statements


class SchemaVersionError(ValueError):
    """An unsupported schema must not be migrated implicitly."""


class UpgradeRequiredError(SchemaVersionError):
    """A v3 mailbox opened by a v4 build: refuse and name the explicit upgrade (FR16)."""


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).lower()


@functools.cache
def _expected_ddl(version: int) -> frozenset[str]:
    memory = sqlite3.connect(":memory:")
    try:
        for statement in ddl_statements(version):
            memory.execute(statement)
        rows = memory.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    finally:
        memory.close()
    return frozenset(_normalize(str(row[0])) for row in rows)


def check(condition: bool, detail: str) -> None:
    if not condition:
        raise ValueError(detail)


def ordered_times(values: tuple[object, ...]) -> bool:
    # Direct numeric comparison once every value is proven a finite non-negative
    # number: identical ordering to the former float(str(x)) round trip for these
    # values, at a fraction of the cost (PRD-CORE-274 NFR08 runs this per row, under
    # the write lock).
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
        for value in values
    ):
        return False
    return all(left <= right for left, right in pairwise(values))  # type: ignore[operator]


_HEX32 = re.compile(r"[0-9a-f]{32}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _hex(value: object) -> bool:
    return isinstance(value, str) and _HEX32.fullmatch(value) is not None


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
        if "body_budget" in row.keys():  # noqa: SIM118 - sqlite3.Row `in` tests VALUES, not column names (v4 check)
            check(_bounded_int(row["body_budget"], 65536, 16777216), "invalid group body budget")
        groups[row["group_id"]] = row
    return groups


def _endpoints(
    conn: sqlite3.Connection, groups: dict[str, sqlite3.Row], version: int
) -> dict[tuple[str, str], sqlite3.Row]:
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
        if version >= 4:
            check(_bounded_int(row["generation"], 1, MAX_COUNTER), "invalid endpoint generation")
            check(
                row["protocol"] in (3, ENDPOINT_PROTOCOL) and type(row["protocol"]) is int, "invalid endpoint protocol"
            )
        endpoints[(row["group_id"], row["member_id"])] = row
    return endpoints


def _admissions(
    conn: sqlite3.Connection,
    groups: dict[str, sqlite3.Row],
    endpoints: dict[tuple[str, str], sqlite3.Row],
    version: int,
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
        check(row["state"] in MESSAGE_STATES, "invalid admission state")
        if version >= 4:
            check(ordered_times((row["admitted_at"], row["expires_at"])), "invalid admission expiry")
            check(_bounded_int(row["delivery_count"], 0, MAX_COUNTER), "invalid delivery count")
            check(
                isinstance(row["canonical_sha256"], str) and _HEX64.fullmatch(row["canonical_sha256"]) is not None,
                "invalid canonical digest",
            )
        key = (row["group_id"], row["recipient_member_id"])
        if row["state"] == MessageState.PENDING:
            if version < 4:  # v4 rows are member-addressed (FR13): no endpoint is required
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
        check(row["fact"] in MILESTONE_FACTS, "unknown milestone")
        check(
            ordered_times((admission["admitted_at"], row["at"], groups[admission["group_id"]]["group_time"])),
            "invalid milestone time",
        )
        facts.setdefault(row["message_id"], {})[row["fact"]] = row["at"]
    for message_id, admission in admissions.items():
        message_facts = facts.get(message_id, {})
        check(message_facts.get("admitted") == admission["admitted_at"], "missing or mismatched admitted fact")
        terminals = set(message_facts) & TERMINAL_MESSAGE_STATES
        expected = set() if admission["state"] == MessageState.PENDING else {admission["state"]}
        check(terminals == expected, "terminal status/milestone disagreement")
        if terminals and "fetch_prepared" in message_facts:
            check(
                message_facts["fetch_prepared"] <= message_facts[next(iter(terminals))], "fetch fact after termination"
            )


def stored_version(conn: sqlite3.Connection) -> str:
    """The single recorded schema version, or refuse on malformed metadata."""
    metadata = conn.execute("SELECT key,value FROM schema_meta").fetchall()
    check(len(metadata) == 1 and metadata[0][0] == "schema_version", "invalid schema metadata")
    return str(metadata[0][1])


def verify(conn: sqlite3.Connection, *, version: int = SCHEMA_VERSION) -> None:
    """Exact schema and row correspondence, in caller-owned consistent snapshot.

    *version* is what the caller requires. A v3 file where v4 is required raises
    :class:`UpgradeRequiredError` so the facade names the explicit upgrade; any other
    mismatch raises :class:`SchemaVersionError`. Nothing is ever migrated here.
    """
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("integrity check failed")
    stored = stored_version(conn)
    if stored != str(version):
        if stored == "3" and version == 4:
            raise UpgradeRequiredError("v3 mailbox; run the explicit comms upgrade")
        raise SchemaVersionError("unsupported schema version; no implicit migration")
    actual = {
        _normalize(str(row[0])) for row in conn.execute("SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    check(actual == _expected_ddl(version), "unexpected or incomplete schema")
    groups = _groups(conn)
    admissions = _admissions(conn, groups, _endpoints(conn, groups, version), version)
    _milestones(conn, groups, admissions)
    for row in conn.execute("SELECT * FROM refusal_counts"):
        check(row["group_id"] in groups and row["reason"] in REFUSALS, "invalid refusal category or group")
        check(_bounded_int(row["count"], 1, MAX_COUNTER), "invalid refusal counter")
