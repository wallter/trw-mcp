"""``trw-mcp doctor`` row for the loopback memory daemon (PRD-CORE-253 FR03).

Kept out of ``_subcommands_doctor.py`` (already past the module-size gate) as a
sibling, the same shape ``_doctor_embedding_egress`` uses.

The check is a **probe**, never a start: asking whether a daemon is running must
not be the thing that starts one, or ``doctor`` would report a healthy daemon it
had just spawned itself.

Four states, and the status mapping is the load-bearing part:

``running``
    PASS, with process id, uptime, endpoint and store path.
``no record``
    PASS. No shipped client attaches to the daemon yet, and none starts one:
    ``trw-mcp`` reads and writes its store directly, and the only
    ``DaemonClient`` caller in the tree is ``trw_memory.cli_namespace``. The
    daemon is started by hand (``trw-memory-server serve http``); client attach
    is a planned slice (PRD-CORE-253 Slice B, deferred). "Not running" is
    therefore the ordinary resting state of a healthy install -- reporting it as
    WARN would make a permanent warning out of correct behaviour and train an
    operator to ignore the row. Do not restore the earlier auto-start wording
    unless a shipped client actually starts the daemon.
``a record naming a dead process``
    WARN. That IS a fault: a client reads that file, tries to reach a daemon
    that is gone, and fails closed. The remedy is named.
``a record that cannot be trusted`` (unreadable, malformed, or schema mismatch)
    WARN, naming the file and the reason. This is NOT the same as "no record":
    an untrusted record says nothing about whether a daemon is serving, so a
    client that folded it into "no daemon" would risk a second writer on the
    same store. Liveness cannot be assessed from an untrusted record, so the
    message reports it as ``not_measured`` rather than guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["memory_daemon_row"]


def _uptime_seconds(started_at: str) -> float | None:
    """Seconds since *started_at*, or None when the timestamp is unusable."""
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds()


def memory_daemon_row() -> tuple[str, str]:
    """Return ``(status, message)`` describing the user's memory daemon.

    Returns:
        ``("PASS", ...)`` both when a daemon is serving (with pid, uptime and
        store path) and when none is recorded; ``("WARN", ...)`` for a record
        naming a process that is gone, or for a record that cannot be trusted
        (unreadable, malformed, or schema mismatch) -- a client would fail
        closed against either.
    """
    from trw_memory.daemon import DaemonPaths, DiscoveryAbsent, DiscoveryInvalid, read_discovery_result
    from trw_memory.daemon.client import DAEMON_START_COMMAND

    paths = DaemonPaths.resolve(create=False)
    result = read_discovery_result(paths)
    if isinstance(result, DiscoveryAbsent):
        return (
            "PASS",
            f"no memory daemon running; nothing starts one for you — start it "
            f"manually with: {DAEMON_START_COMMAND} (client attach is a planned "
            f"slice). store: {paths.user_memory_dir}",
        )
    if isinstance(result, DiscoveryInvalid):
        return (
            "WARN",
            f"untrusted daemon record: {result.path} {result.reason} (liveness: not_measured). "
            f"A client cannot tell whether a daemon holds this store. "
            f"Remove the file, or run: {DAEMON_START_COMMAND}",
        )
    if not result.is_live(paths.lock):
        return (
            "WARN",
            f"stale daemon record: {paths.discovery} names pid {result.pid}, which is not running. "
            f"A client would try that endpoint and fail closed. Remove the file, or run: {DAEMON_START_COMMAND}",
        )
    info = result
    uptime = _uptime_seconds(info.started_at)
    uptime_text = f"{uptime:.0f}s" if uptime is not None else "unknown"
    logger.debug("doctor_memory_daemon_reachable", pid=info.pid)
    return (
        "PASS",
        f"memory daemon reachable at {info.url} (pid {info.pid}, up {uptime_text}, "
        f"version {info.version}). store: {paths.user_memory_dir}",
    )
