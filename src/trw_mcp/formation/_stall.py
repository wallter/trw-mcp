"""Body-free formation stalls from one mailbox scan and durable call markers.

The tool-call timing wrapper owns marker start/clear. Status and watch share this
read-only scan; no timer, new mailbox, or external-harness call inference.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from trw_mcp.formation._manifest import TERMINAL_STATUSES, FormationManifest
from trw_mcp.state._process_identity import pid_is_alive, read_process_start_time

STALL_SECONDS = 600
_CALL_DIR = "call-in-flight"
#: PRD-CORE-338-FR07 default; the served callers pass ``formation_activity_stall_seconds``.
ACTIVITY_STALL_SECONDS = 1800
#: PRD-CORE-322 FR07: the ``comms_message_ttl_seconds`` default; an unaccepted request older than this leaves the board.
HANDOFF_TTL_SECONDS = 86400
_TAIL_BYTES = 65536
StallReason = Literal["mail_unfetched", "heartbeat_lost", "call_in_flight", "activity_stale"]


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
    activity_measurement: str = "measured"
    #: PRD-CORE-322 FR07: open handoffs (untrusted ``next_read`` text included), filled only with include_handoffs.
    handoffs: tuple[dict[str, Any], ...] = ()
    handoffs_omitted: int = 0
    handoff_measurement: str = "not_measured"


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
            pid = os.getpid()
            owner = {"pid": pid, "started": read_process_start_time(pid)}
            json.dump({"member_id": member_id, "blocked_since": blocked_since, "owner": owner}, handle)
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


def _owner_alive(owner: object) -> bool:
    """Whether the process that wrote a marker still runs; a dead owner's marker is an orphan, not a call (B71-26 D4)."""
    if not isinstance(owner, dict) or not isinstance(owner.get("pid"), int) or isinstance(owner["pid"], bool):
        return False  # a marker with no owner (pre-7.0.1 format) cannot be tied to a live call
    pid = owner["pid"]
    if not pid_is_alive(pid):
        return False
    started = owner.get("started")
    return started is None or read_process_start_time(pid) in (None, started)


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
        if not _owner_alive(data.get("owner")):
            continue
        member = data["member_id"]
        ages[member] = max(ages.get(member, 0), int(max(0, now - blocked_since)))
    return ages


def newest_event_age(run_path: Path, now: float) -> int:
    """Seconds since the newest recorded ``ts`` in a run's meta/events.jsonl (FR07).

    Reads the file's tail (the log is append-only, so the newest line is last),
    doubling the window until a ``ts`` is found or the whole file was read, so one
    oversized final record cannot hide activity. Raises ``OSError`` when the file
    is missing or unreadable and ``ValueError`` when it holds no parseable ``ts``;
    the caller turns both into ``activity_measurement: not_measured``.
    """
    path = run_path / "meta" / "events.jsonl"
    window = _TAIL_BYTES
    with path.open("rb") as handle:
        size = handle.seek(0, os.SEEK_END)
        while True:
            handle.seek(max(0, size - window))
            newest = _newest_ts(handle.read().decode("utf-8", errors="replace"))
            if newest is not None:
                return int(max(0.0, now - newest))
            if window >= size:
                raise ValueError(f"no recorded ts in {path}")
            window *= 4


def _newest_ts(text: str) -> float | None:
    from trw_mcp.state.timekeeping import parse_ts

    newest: float | None = None
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except (
            ValueError
        ):  # trw-fail-silent-allow: a torn or partial line is skipped; no ts at all raises in the caller
            continue
        stamp = parse_ts(record.get("ts")) if isinstance(record, dict) else None
        if stamp is not None and (newest is None or stamp.timestamp() > newest):
            newest = stamp.timestamp()
    return newest


def _activity_ages(manifest: FormationManifest, project_root: Path, now: float) -> tuple[dict[str, int], str]:
    """Per-member run-event ages; any unreadable member marks the scan not_measured."""
    ages: dict[str, int] = {}
    measurement = "measured"
    for member in manifest.members:
        if str(member.status) in TERMINAL_STATUSES or not member.run_path:
            continue
        run_path = Path(member.run_path)
        try:
            ages[member.member_id] = newest_event_age(
                run_path if run_path.is_absolute() else project_root / run_path, now
            )
        except (OSError, ValueError):  # trw-fail-silent-allow: source is explicitly not_measured
            measurement = "not_measured"
    return ages, measurement


def stall_scan(
    manifest: FormationManifest,
    manifest_path: Path,
    project_root: Path,
    *,
    now: float,
    activity_stall_seconds: int = ACTIVITY_STALL_SECONDS,
    include_handoffs: bool = False,
    handoff_ttl_seconds: int = HANDOFF_TTL_SECONDS,
) -> StallScan:
    """Report overdue unfetched mail or absent heartbeat, never message content.

    Missing/unreadable sources are independently marked not_measured.
    Pending means never prepared for fetch, not merely unacknowledged.
    ``include_handoffs`` (PRD-CORE-322 FR07/NFR01) also reads the capped open-handoff
    list on the SAME read-only connection, in its own try block, so a pre-v5 or broken
    mailbox marks only ``handoff_measurement`` not_measured. ``formation status`` turns
    it on; ``formation watch`` leaves it off and pays nothing.
    """
    from trw_mcp.comms._handoff import open_handoffs
    from trw_mcp.comms._identity import derive_group_id
    from trw_mcp.comms._store import database_path, open_mailbox_ro

    unread: dict[str, tuple[int, float]] = {}
    seen: dict[str, float] = {}
    mail_measurement = "measured"
    handoffs: list[dict[str, Any]] = []
    omitted, handoff_measurement = 0, "not_measured"
    try:
        group_id = derive_group_id(project_root, manifest_path)
        conn = open_mailbox_ro(database_path(manifest_path))
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
            if include_handoffs:
                try:
                    handoffs, omitted = open_handoffs(conn, group_id, now=now, ttl_seconds=handoff_ttl_seconds)
                    handoff_measurement = "measured"
                except (
                    sqlite3.Error,
                    TypeError,
                    ValueError,
                ):  # trw-fail-silent-allow: handoff_measurement is not_measured
                    handoffs, omitted = [], 0
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
    activity_ages, activity_measurement = _activity_ages(manifest, project_root, now)
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
                if not (math.isfinite(heartbeat_age) and math.isfinite(mail_age)):
                    raise ValueError("non-finite mailbox time")  # a malformed row is not_measured, not a crash
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
        activity_age = activity_ages.get(member.member_id)
        if activity_age is not None and activity_age > activity_stall_seconds:
            findings.append(StallFinding(member.member_id, "activity_stale", activity_age))
    return StallScan(
        findings,
        mail_measurement,
        call_measurement,
        activity_measurement,
        tuple(handoffs),
        omitted,
        handoff_measurement,
    )
