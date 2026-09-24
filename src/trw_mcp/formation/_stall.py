"""Body-free formation stalls from one mailbox scan and durable call markers.

The tool-call timing wrapper owns marker start/clear. Status and watch share this
read-only scan; no timer, new mailbox, or external-harness call inference.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from trw_mcp.formation._manifest import TERMINAL_STATUSES, FormationManifest

STALL_SECONDS = 600
_CALL_DIR = "call-in-flight"
StallReason = Literal["mail_unfetched", "heartbeat_lost", "call_in_flight"]


@dataclass(frozen=True)
class StallFinding:
    member_id: str
    reason: StallReason
    age_seconds: int
    pending: int = 0

    def as_dict(self) -> dict[str, str | int]:
        return {
            "member_id": self.member_id,
            "reason": self.reason,
            "age_seconds": self.age_seconds,
            "pending": self.pending,
        }

    def line(self) -> str:
        suffix = f" pending={self.pending}" if self.reason == "mail_unfetched" else ""
        return f"stalled member={self.member_id} reason={self.reason} age={self.age_seconds // 60}{suffix}"


@dataclass(frozen=True)
class StallScan:
    findings: list[StallFinding]
    mail_measurement: str
    call_measurement: str


def _joined_at(raw: str | None) -> float | None:
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def start_call(manifest_path: Path, member_id: str, blocked_since: float) -> Path:
    """Persist one call episode before executing the formed member's MCP tool."""
    call_dir = manifest_path.parent / _CALL_DIR
    call_dir.mkdir(mode=0o700, exist_ok=True)
    marker = call_dir / f"{member_id}.{uuid.uuid4().hex}.json"
    tmp = marker.with_suffix(".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump({"member_id": member_id, "blocked_since": blocked_since}, handle)
            handle.flush()
        os.replace(tmp, marker)
    finally:
        tmp.unlink(missing_ok=True)
    return marker


def begin_call(ctx: object | None, blocked_since: float) -> Path | None:
    """Bind a FastMCP caller to its formation before persisting an episode."""
    if ctx is None:
        return None
    from typing import cast

    from fastmcp import Context

    from trw_mcp.comms._identity import IdentityError, resolve_authority_snapshot
    from trw_mcp.formation._coordination import own_root

    root = own_root()
    try:
        binding = resolve_authority_snapshot(
            cast("Context", ctx), trw_dir=root.trw_dir, project_root=root.project_root
        ).binding
    except IdentityError:  # trw-fail-silent-allow: nonmembers have no call marker; a valid tool call still runs
        return None
    return start_call(binding.manifest_path, binding.member_id, blocked_since)


def clear_call(marker: Path) -> None:
    """Completion and cancellation end this exact call episode."""
    marker.unlink(missing_ok=True)


def _call_ages(manifest_path: Path, now: float) -> dict[str, int]:
    ages: dict[str, int] = {}
    call_dir = manifest_path.parent / _CALL_DIR
    if not call_dir.exists():
        return ages
    for marker in call_dir.glob("*.json"):
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except FileNotFoundError:  # trw-fail-silent-allow: completed call removed its marker
            # A completed call can unlink a marker after glob enumerates it.
            continue
        if not isinstance(data, dict) or not isinstance(data.get("member_id"), str):
            raise TypeError("invalid call-in-flight marker")
        blocked_since = data.get("blocked_since")
        if not isinstance(blocked_since, (float, int)) or isinstance(blocked_since, bool):
            raise TypeError("invalid blocked_since")
        member = data["member_id"]
        ages[member] = max(ages.get(member, 0), int(max(0, now - blocked_since)))
    return ages


def stall_scan(manifest: FormationManifest, manifest_path: Path, project_root: Path, *, now: float) -> StallScan:
    """Report overdue unfetched mail or absent heartbeat, never message content.

    Missing/unreadable sources are independently marked not_measured.
    Pending means never prepared for fetch, not merely unacknowledged.
    """
    from trw_mcp.comms._identity import derive_group_id
    from trw_mcp.comms._store import database_path

    unread: dict[str, tuple[int, float]] = {}
    seen: dict[str, float] = {}
    mail_measurement = "measured"
    try:
        group_id = derive_group_id(project_root, manifest_path)
        conn = sqlite3.connect(database_path(manifest_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
        try:
            unread = {
                str(member): (int(count), float(oldest))
                for member, count, oldest in conn.execute(
                    "SELECT a.recipient_member_id,COUNT(*),MIN(a.admitted_at) FROM admissions a "
                    "LEFT JOIN milestones m ON m.message_id=a.message_id AND m.fact='fetch_prepared' "
                    "WHERE a.group_id=? AND a.state='pending' AND a.expires_at>? AND m.message_id IS NULL "
                    "GROUP BY a.recipient_member_id",
                    (group_id, now),
                )
            }
            seen = {
                str(member): float(last_seen)
                for member, last_seen in conn.execute(
                    "SELECT member_id,last_seen_at FROM endpoints WHERE group_id=?", (group_id,)
                )
            }
        finally:
            conn.close()
    except (OSError, sqlite3.Error, TypeError, ValueError):  # trw-fail-silent-allow: source is explicitly not_measured
        mail_measurement = "not_measured"
    try:
        call_ages = _call_ages(manifest_path, now)
        call_measurement = "measured"
    except (OSError, TypeError, ValueError):  # trw-fail-silent-allow: source is explicitly not_measured
        call_ages = {}
        call_measurement = "not_measured"
    findings: list[StallFinding] = []
    for member in manifest.members:
        if str(member.status) in TERMINAL_STATUSES or not member.run_path:
            continue
        if mail_measurement == "measured":
            try:
                count, oldest = unread.get(member.member_id, (0, now))
                joined = _joined_at(member.joined_utc)
                heartbeat_age = now - seen.get(member.member_id, joined if joined is not None else now)
                mail_age = now - oldest if count else 0.0
            except (TypeError, ValueError):
                mail_measurement = "not_measured"
                findings = [item for item in findings if item.reason == "call_in_flight"]
            else:
                if count and mail_age > STALL_SECONDS:
                    findings.append(StallFinding(member.member_id, "mail_unfetched", int(mail_age), count))
                if heartbeat_age > STALL_SECONDS:
                    findings.append(StallFinding(member.member_id, "heartbeat_lost", int(heartbeat_age)))
        call_age = call_ages.get(member.member_id, 0)
        if call_age > STALL_SECONDS:
            findings.append(StallFinding(member.member_id, "call_in_flight", call_age))
    return StallScan(findings, mail_measurement, call_measurement)
