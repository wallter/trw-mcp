"""Body-free pending hint for a managed member (PRD-CORE-274 FR13 hint clause, lane-B seam).

A managed runtime (lane B, private trw-loop) polls this while its child is idle
and sends ONE body-free nudge when the watermark advances; the child then fetches
and ACKs by itself. The hint is not a message channel and grants nothing.

Authority is the actual parent binding (lead board 661), never the pin alone:
the manifest member must hold ``pin_key`` for its recorded run, and the caller's
parent process must be the parent of the client that owns that pin record --
the same client process (pid AND start time) the pin store recorded. Anything
else returns None. Any sibling of the client (another child of the same parent,
e.g. a launching shell) also passes; that is accepted because the hint is
body-free and inside the same-OS-user boundary of FR08. Read-only by construction: one ``mode=ro`` connection, one
query, no verification writes, no lease/endpoint/milestone/telemetry effect.

Watermark monotonicity: rows are never deleted or VACUUMed (tombstoning is an
UPDATE), so admission rowids never recede and are never reused. For ANY new
admission to the member, ``hint_advanced(w, new)`` is true for every earlier
watermark ``w`` -- including after the queue was emptied by ACK or expiry.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from trw_mcp.comms._pins import member_pin_entry


@dataclass(frozen=True)
class PendingHint:
    count: int
    watermark: str
    member_status: str


def hint_advanced(previous: str | None, current: str) -> bool:
    """True when *current* names an admission newer than *previous* (opaque to callers)."""
    try:
        now_mark = int(current, 16) if current else 0
        before = int(previous, 16) if previous else 0
    except ValueError:
        return bool(current)
    return now_mark > before


def _same_run(recorded: object, member_run: object) -> bool:
    """Both paths present and canonically equal. ``Path("")`` is the cwd, so empty never matches."""
    if not isinstance(recorded, str) or not recorded or not isinstance(member_run, str) or not member_run:
        return False
    return Path(recorded).resolve() == Path(member_run).resolve()


def _parent_pid(pid: int) -> int | None:
    stat = Path(f"/proc/{pid}/stat")
    try:
        if stat.exists():
            return int(stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[1])
        out = subprocess.run(  # noqa: S603 - fixed argv, integer pid
            ["/bin/ps", "-o", "ppid=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5, check=False
        )
        return int(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        # trw-fail-silent-allow: an unknown parent proves nothing; the hint refuses (returns None)
        return None


def _parent_bound(entry: dict[str, object], caller_parent_pid: int) -> bool:
    from trw_mcp.state._process_identity import process_start_time

    client_pid = entry.get("client_pid")
    if type(client_pid) is not int or client_pid <= 1:
        return False
    started = process_start_time(client_pid)
    if started is None or started != entry.get("client_start"):
        return False  # not the client the pin store recorded (exited or pid recycled)
    return _parent_pid(client_pid) == caller_parent_pid


def _lean_roots(formation_id: str, member_id: str) -> tuple[Path, Path]:
    """``(project_root, trw_dir)`` for a lean call, honouring FR17 for a recorded worktree member.

    The main root is used only when this worktree's membership record names this
    exact formation and member; the pin and run checks that follow still decide.
    """
    from trw_mcp.formation import own_root, shared_authority_root

    own = own_root()
    shared = shared_authority_root(own)
    if shared is not None and (shared[1].formation_id, shared[1].member_id) == (formation_id, member_id):
        return shared[0].project_root, shared[0].trw_dir
    return own.project_root, own.trw_dir


def pending_hint(
    *,
    pin_key: str,
    formation_id: str,
    member_id: str,
    trw_dir: Path | None = None,
    project_root: Path | None = None,
    caller_parent_pid: int | None = None,
) -> PendingHint | None:
    from trw_mcp.comms._envelope import MessageState
    from trw_mcp.comms._identity import ELIGIBLE_STATUSES, derive_group_id
    from trw_mcp.comms._store import database_path
    from trw_mcp.formation import FormationError, read_manifest, resolve_manifest_path

    try:
        if trw_dir is None or project_root is None:
            default_root, default_trw_dir = _lean_roots(formation_id, member_id)
            project_root, trw_dir = project_root or default_root, trw_dir or default_trw_dir
        root = project_root
        manifest_path = resolve_manifest_path(trw_dir, formation_id)
        member = read_manifest(manifest_path).member(member_id)
    except (FormationError, OSError, ValueError, KeyError):
        # trw-fail-silent-allow: no formation or member means no hint, never an error to the poller
        return None
    if member.pin_key != pin_key or member.run_path is None:
        return None
    entry = member_pin_entry(member.run_path, pin_key)
    if entry is None or not _same_run(entry.get("run_path"), member.run_path):
        return None
    if not _parent_bound(entry, os.getppid() if caller_parent_pid is None else caller_parent_pid):
        return None
    status = str(member.status)
    if status not in ELIGIBLE_STATUSES:
        return PendingHint(count=0, watermark="", member_status=status)
    database = database_path(manifest_path)
    try:
        conn = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except sqlite3.Error:
        # trw-fail-silent-allow: an unopenable mailbox gives no hint; the poller falls back
        return None
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if version is None or version[0] != "4":
            return None  # a v3 mailbox gets no hint and is never touched (FR16)
        count, newest = conn.execute(
            "SELECT COUNT(*), MAX(rowid) FROM admissions WHERE group_id=? AND recipient_member_id=? "
            "AND state=? AND expires_at > ?",
            (derive_group_id(root, manifest_path), member_id, MessageState.PENDING.value, time.time()),
        ).fetchone()
    except sqlite3.Error:
        # trw-fail-silent-allow: a locked or damaged mailbox gives no hint; nothing is written
        return None
    finally:
        conn.close()
    return PendingHint(count=int(count), watermark=f"{int(newest):016x}" if newest else "", member_status=status)


@dataclass(frozen=True)
class PinLineage:
    member_status: str
    member_run: str | None
    pin_matches: bool
    pin_run_matches: bool
    terminal: bool


def pin_lineage(*, pin_key: str, formation_id: str, member_id: str, trw_dir: Path | None = None) -> PinLineage | None:
    """Pre-spawn restart check: does the persisted pin still name this member's run?

    This is a consistency check, not a credential: anyone who can run it as the
    same OS user can already read the owner-only manifest (FR08's boundary; the
    file mode is the access control). The run path is still withheld unless the
    manifest records the presented pin, so a mismatched pin gets only the
    member's status and terminal flag, which carry nothing the pin would unlock.
    There is no parent binding, because the child that would satisfy one does not
    exist yet. Read-only: the manifest and the pin store are read and nothing else.
    """
    from trw_mcp.formation import TERMINAL_STATUSES, FormationError, read_manifest, resolve_manifest_path

    try:
        store = trw_dir or _lean_roots(formation_id, member_id)[1]
        member = read_manifest(resolve_manifest_path(store, formation_id)).member(member_id)
    except (FormationError, OSError, ValueError, KeyError):
        # trw-fail-silent-allow: no formation or member means no lineage; the driver refuses recoverably
        return None
    status = str(member.status)
    pin_matches = member.pin_key == pin_key and member.run_path is not None
    entry = member_pin_entry(member.run_path, pin_key) if pin_matches and member.run_path else None
    pin_run_matches = entry is not None and _same_run(entry.get("run_path"), member.run_path)
    return PinLineage(
        member_status=status,
        member_run=str(member.run_path) if pin_matches else None,
        pin_matches=pin_matches,
        pin_run_matches=pin_run_matches,
        terminal=status in TERMINAL_STATUSES,
    )


def main(argv: list[str] | None = None) -> int:
    """``python -m trw_mcp.comms._hint``: the same answer with no server bootstrap at all."""
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m trw_mcp.comms._hint")
    parser.add_argument("--pin-key", required=True)
    parser.add_argument("--formation", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--since", default=None)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--lineage", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.lineage:
        print(json.dumps(lineage_payload(args.pin_key, args.formation, args.member)))
        return 0
    print(json.dumps(hint_payload(args.pin_key, args.formation, args.member, since=args.since, probe=args.probe)))
    return 0


def hint_payload(pin_key: str, formation: str, member: str, *, since: str | None, probe: bool) -> dict[str, object]:
    """The exact CLI JSON contract agreed with lane B: {"hint": {...} | null}."""
    if probe:
        return {"hint": None}
    hint = pending_hint(pin_key=pin_key, formation_id=formation, member_id=member)
    if hint is None:
        return {"hint": None}
    return {
        "hint": {
            "count": hint.count,
            "watermark": hint.watermark,
            "advanced": hint_advanced(since, hint.watermark),
            "member_status": hint.member_status,
        }
    }


def lineage_payload(pin_key: str, formation: str, member: str) -> dict[str, object]:
    """``--lineage`` JSON contract agreed with lane B: {"lineage": {...} | null}."""
    found = pin_lineage(pin_key=pin_key, formation_id=formation, member_id=member)
    if found is None:
        return {"lineage": None}
    return {
        "lineage": {
            "member_status": found.member_status,
            "member_run": found.member_run,
            "pin_matches": found.pin_matches,
            "pin_run_matches": found.pin_run_matches,
            "terminal": found.terminal,
        }
    }


if __name__ == "__main__":  # pragma: no cover - exercised by a subprocess test
    sys.exit(main())


__all__ = [
    "PendingHint",
    "PinLineage",
    "hint_advanced",
    "hint_payload",
    "lineage_payload",
    "main",
    "pending_hint",
    "pin_lineage",
]
