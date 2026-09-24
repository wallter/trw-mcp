"""``trw-mcp formation watch``: a harness-tailable, body-free wake for one member.

Belongs to the ``trw_mcp.comms`` package; the ``formation watch`` CLI verb runs it.
Design: the formation watch-wake design (rev 3, reviewed READY).

An MCP server cannot schedule a model turn, so an idle member never learns mail
arrived. A harness background tailer (Claude Code's Monitor, a loop driver) CAN
wake its model on a printed line. This command is that line, and nothing more:

- it prints ``pending count=<n> seq=<k> status=<s>`` only when this member's
  pending watermark advances or its status changes. ``seq`` is local to the watch,
  so another member's traffic volume never shows;
- it prints no body, sender, kind or message id, and writes nothing anywhere;
- it re-checks authority on EVERY poll. The pin's recorded client (pid plus
  start time) must be an ANCESTOR of this process, must be the client captured at
  start (``pin_rebound`` otherwise), and must not hold pins for more than one run
  (``ambiguous_client``: a multi-session host);
- a refusal prints one ``refused <reason>`` line from a closed set and exits with
  a code a supervisor can act on.
"""

from __future__ import annotations

import os
import signal
import sqlite3
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import FrameType

from trw_mcp.comms._hint import _lean_roots, _parent_pid, _same_run, hint_advanced, live_client, pending_counts
from trw_mcp.comms._pins import member_pin_entry, member_pin_store
from trw_mcp.formation import StallFinding, stall_scan

#: reason -> exit code. 2 = authority, 3 = platform, 4 = upgrade the mailbox, 5 = unreadable.
EXIT_CODES: dict[str, int] = {
    "not_ancestor": 2,
    "client_gone": 2,
    "pin_rebound": 2,
    "ambiguous_client": 2,
    "pin_mismatch": 2,
    "lineage_mismatch": 2,
    "unsupported_platform": 3,
    "mailbox_upgrade_required": 4,
    "formation_unavailable": 5,
}
MAX_ANCESTOR_HOPS = 32
_SUPPORTED_PLATFORMS = ("linux", "darwin")


@dataclass(frozen=True)
class Observation:
    """One poll's answer: a refusal reason, or the member's pending facts."""

    refused: str | None = None
    client: tuple[int, str] | None = None
    count: int = 0
    watermark: int = 0
    status: str = ""
    stalls: tuple[StallFinding, ...] = ()
    stall_scope: tuple[str, str] = ("measured", "measured")


def _is_ancestor(client_pid: int, caller_pid: int) -> bool:
    pid: int | None = caller_pid
    for _ in range(MAX_ANCESTOR_HOPS):
        pid = _parent_pid(pid) if pid is not None else None
        if pid is None or pid <= 1:
            return False
        if pid == client_pid:
            return True
    return False


def _ambiguous(run_path: str, client: tuple[int, str]) -> bool:
    """More than one distinct RUN pinned by this client: a multi-session host, not a reconnect."""
    store = member_pin_store(run_path) or {}
    runs = {
        str(Path(str(entry.get("run_path", ""))).resolve())
        for entry in store.values()
        if (entry.get("client_pid"), entry.get("client_start")) == client and entry.get("run_path")
    }
    return len(runs) > 1


def observe(
    *,
    pin_key: str,
    formation_id: str,
    member_id: str,
    caller_pid: int,
    captured: tuple[int, str] | None = None,
    trw_dir: Path | None = None,
    project_root: Path | None = None,
) -> Observation:
    """Authorize this caller for *member_id*, then read its pending facts. Read-only."""
    from trw_mcp.comms._identity import ELIGIBLE_STATUSES, derive_group_id
    from trw_mcp.comms._store import database_path
    from trw_mcp.formation import (
        FormationError,
        orchestrator_run_of,
        read_manifest,
        read_pause,
        resolve_manifest_path,
    )

    if not sys.platform.startswith(_SUPPORTED_PLATFORMS):
        return Observation(refused="unsupported_platform")
    try:
        if trw_dir is None or project_root is None:
            default_root, default_trw_dir = _lean_roots(formation_id, member_id)
            project_root, trw_dir = project_root or default_root, trw_dir or default_trw_dir
        manifest_path = resolve_manifest_path(trw_dir, formation_id)
        manifest = read_manifest(manifest_path)
        member = manifest.member(member_id)
    except (FormationError, OSError, ValueError, KeyError):
        # trw-fail-silent-allow: reported to the tailer as the closed reason formation_unavailable
        return Observation(refused="formation_unavailable")
    if member.pin_key != pin_key or member.run_path is None:
        return Observation(refused="pin_mismatch")
    entry = member_pin_entry(member.run_path, pin_key)
    if entry is None or not _same_run(entry.get("run_path"), member.run_path):
        return Observation(refused="lineage_mismatch")
    client_pid = live_client(entry)
    if client_pid is None:
        return Observation(refused="client_gone")
    client = (client_pid, str(entry.get("client_start")))
    if captured is not None and client != captured:
        return Observation(refused="pin_rebound")
    if _ambiguous(member.run_path, client):
        return Observation(refused="ambiguous_client")
    if not _is_ancestor(client_pid, caller_pid):
        return Observation(refused="not_ancestor")
    status = str(member.status)
    if status not in ELIGIBLE_STATUSES:
        # A pending or terminal member receives nothing: its rows expire, never deliver (FR13).
        return Observation(client=client, status=status)
    try:
        # PAUSE-RESUME rev 2: a pause shows as status=paused; the orchestrator is never paused.
        is_orchestrator = Path(member.run_path).resolve() == orchestrator_run_of(manifest)
        if not is_orchestrator and read_pause(manifest_path) is not None:
            status = "paused"
    except FormationError:
        # trw-fail-silent-allow: reported to the tailer as the closed reason formation_unavailable
        return Observation(refused="formation_unavailable")
    count, newest = 0, 0
    pending_measured = False
    try:
        conn = sqlite3.connect(database_path(manifest_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    except (sqlite3.Error, ValueError):  # trw-fail-silent-allow: a raced read is retried at the next watch poll
        # trw-fail-silent-allow: mailbox is not_measured; call markers are still scanned below
        conn = None
    if conn is not None:
        try:
            version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
            if version is not None and version[0] != "4":
                return Observation(refused="mailbox_upgrade_required")
            count, newest = pending_counts(conn, derive_group_id(project_root, manifest_path), member_id)
            pending_measured = True
        except sqlite3.Error:
            # trw-fail-silent-allow: pending is not_measured; independent marker scan follows
            pass
        finally:
            conn.close()
    scan = stall_scan(manifest, manifest_path, project_root, now=time.time())
    stalls = tuple(item for item in scan.findings if item.member_id == member_id)
    return Observation(
        client=client,
        count=count,
        watermark=newest,
        status=status,
        stalls=stalls,
        stall_scope=(scan.mail_measurement if pending_measured else "not_measured", scan.call_measurement),
    )


def watch(
    *,
    pin_key: str,
    formation_id: str,
    member_id: str,
    interval_seconds: float = 15.0,
    once: bool = False,
    emit: Callable[[str], None] = print,
    wait: Callable[[float], object] | None = None,
    caller_pid: int | None = None,
    max_polls: int | None = None,
) -> int:
    """Poll until terminal, refusal or SIGTERM; return the exit code. ``once`` prints the current line."""
    from trw_mcp.comms._identity import ELIGIBLE_STATUSES
    from trw_mcp.formation import TERMINAL_STATUSES

    # An Event, not a flag plus time.sleep: sleep resumes after a handled signal
    # (PEP 475), so SIGTERM would take up to a whole interval to act on, and a
    # harness that escalates to SIGKILL after a short grace would kill a clean watch.
    stop = threading.Event()
    wait = wait or stop.wait

    def _terminate(_signum: int, _frame: FrameType | None) -> None:
        stop.set()

    if max_polls is None:  # a real run, not a test driving the loop in-process
        signal.signal(signal.SIGTERM, _terminate)
    me = os.getpid() if caller_pid is None else caller_pid
    captured: tuple[int, str] | None = None
    last_watermark, last_status, seq, polls = 0, "", 0, 0
    last_stalls: tuple[str, ...] = ()
    last_scope: tuple[str, str] | None = None
    while not stop.is_set():
        seen = observe(
            pin_key=pin_key, formation_id=formation_id, member_id=member_id, caller_pid=me, captured=captured
        )
        if seen.refused is not None:
            emit(f"refused {seen.refused}")
            return EXIT_CODES[seen.refused]
        first = captured is None
        captured = captured or seen.client
        if first:  # the baseline: wake only if mail is already waiting, never merely for starting
            changed = seen.count > 0 or bool(seen.stalls) or seen.stall_scope != ("measured", "measured")
        else:
            stall_reasons = tuple(item.reason for item in seen.stalls)
            changed = (
                hint_advanced(f"{last_watermark:x}", f"{seen.watermark:x}")
                or seen.status != last_status
                or stall_reasons != last_stalls
                or seen.stall_scope != last_scope
            )
        if once or changed:
            seq += 1
            # The first line after a pause ends says so, so a tailer can wake its model to resume.
            shown = "resumed" if last_status == "paused" and seen.status in ELIGIBLE_STATUSES else seen.status
            emit(f"pending count={seen.count} seq={seq} status={shown}")
        for stall in seen.stalls:
            if once or stall.reason not in last_stalls:
                emit(stall.line())
        if once or seen.stall_scope != last_scope:
            for source, measurement in zip(("mailbox", "mcp_tool_calls"), seen.stall_scope, strict=True):
                if measurement != "measured":
                    emit(f"not_measured source={source}")
        last_watermark, last_status = max(last_watermark, seen.watermark), seen.status
        last_stalls = tuple(item.reason for item in seen.stalls)
        last_scope = seen.stall_scope
        polls += 1
        if once or seen.status in TERMINAL_STATUSES or (max_polls is not None and polls >= max_polls):
            return 0
        wait(interval_seconds)
    return 0


def main(argv: list[str] | None = None) -> int:
    """``python -m trw_mcp.comms._watch``: the same loop with no CLI dispatcher."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m trw_mcp.comms._watch")
    parser.add_argument("--formation", required=True)
    parser.add_argument("--member", required=True)
    parser.add_argument("--pin-key", default=None)
    parser.add_argument("--interval-seconds", type=float, default=15.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    pin = args.pin_key or os.environ.get("TRW_SESSION_ID", "")
    if not pin:
        print("refused pin_mismatch", flush=True)
        return EXIT_CODES["pin_mismatch"]
    return watch(
        pin_key=pin,
        formation_id=args.formation,
        member_id=args.member,
        interval_seconds=args.interval_seconds,
        once=args.once,
        emit=lambda line: print(line, flush=True),
    )


if __name__ == "__main__":  # pragma: no cover - exercised by a subprocess test
    sys.exit(main())


__all__ = ["EXIT_CODES", "MAX_ANCESTOR_HOPS", "Observation", "main", "observe", "watch"]
