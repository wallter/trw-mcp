"""``trw-mcp doctor`` row for the loopback memory daemon (PRD-CORE-253 FR03).

Kept out of ``_subcommands_doctor.py`` (already past the module-size gate) as a
sibling, the same shape ``_doctor_embedding_egress`` uses.

The check is a **probe**, never a start: asking whether a daemon is running must
not be the thing that starts one, or ``doctor`` would report a healthy daemon it
had just spawned itself.

Liveness is an answer from the endpoint, not a pid (PRD-CORE-310 FR05): a live
process whose endpoint serves nothing is exactly the state every client fails
in, and a pid check reported it PASS.

The states, and the status mapping is the load-bearing part:

``running``
    PASS once the endpoint answers one MCP ping (sent with the checkout's grant
    when it has one; a 401 without a grant is still an answer), with process id,
    uptime, endpoint and store path. A rejected grant is WARN naming the verb that
    mints one. A live process that does not answer is FAIL naming it.
    An answering daemon on another trw-memory version than the installed one is
    WARN naming how to stop it: after an upgrade it keeps serving the old code.
``no record``
    PASS. The daemon is started on demand: the first memory operation of a
    ``DaemonClient`` that finds no daemon (``_attach``) launches one detached,
    under the same interpreter (``start_daemon_detached``), and the daemon exits
    after ``memory_daemon_idle_shutdown_seconds`` without a request. "Not
    running" is therefore the ordinary resting state of a healthy install
    between sessions -- reporting it as WARN would make a permanent warning out
    of correct behaviour and train an operator to ignore the row.
``a record naming a dead process`` (gone, a zombie, or a pid now reused)
    WARN: the daemon crashed or was killed. The next memory call replaces the
    record and starts a successor, so nothing is stuck, but a crash is worth seeing.
``a record that cannot be trusted`` (unreadable, malformed, or schema mismatch)
    WARN, naming the file and the reason. This is NOT the same as "no record":
    an untrusted record says nothing about whether a daemon is serving, so a
    client that folded it into "no daemon" would risk a second writer on the
    same store. Liveness cannot be assessed from an untrusted record, so the
    message reports it as ``not_measured`` rather than guessing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["memory_daemon_row"]

#: How long the endpoint gets to answer the ping; a daemon under load answers a ping in milliseconds.
_PROBE_TIMEOUT_SECONDS = 5.0


def _uptime_seconds(started_at: str) -> float | None:
    """Seconds since *started_at*, or None when the timestamp is unusable."""
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - started).total_seconds()


def memory_daemon_row(target: Path) -> tuple[str, str]:
    """Return ``(status, message)`` describing the user's memory daemon, as the checkout *target* reaches it.

    Returns:
        ``("PASS", ...)`` when the endpoint answered (with pid, uptime and store
        path) and when no daemon is recorded; ``("WARN", ...)`` for a record
        naming a process that is gone, a record that cannot be trusted, or a
        rejected grant; ``("FAIL", ...)`` for a live process whose endpoint does
        not answer -- every client fails against it.
    """
    from trw_memory.daemon import DaemonPaths, DiscoveryAbsent, DiscoveryInvalid, read_discovery_result
    from trw_memory.daemon.client import DAEMON_START_COMMAND, probe_endpoint
    from trw_memory.exceptions import DaemonUnreachableError

    paths = DaemonPaths.resolve(create=False)
    if paths.token.exists():
        return (
            "WARN",
            f"a Slice A all-namespace token remains at {paths.token}; the daemon refuses to start "
            f"while it exists (PRD-CORE-298 FR02). Run: trw-mcp memory token --migrate",
        )
    result = read_discovery_result(paths)
    if isinstance(result, DiscoveryAbsent):
        return (
            "PASS",
            f"no memory daemon running; the next memory call starts one (it exits when idle), "
            f"or start it now with: {DAEMON_START_COMMAND}. store: {paths.user_memory_dir}",
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
            f"stale daemon record: {paths.discovery} names pid {result.pid}, which is not running (the daemon "
            f"crashed or was killed; see {paths.start_log}). The next memory call replaces the record and starts one.",
        )
    info = result
    token = _checkout_grant(target)
    try:
        answer = asyncio.run(probe_endpoint(info, token, _PROBE_TIMEOUT_SECONDS))
    except DaemonUnreachableError as exc:
        return (
            "FAIL",
            f"memory daemon pid {info.pid} is running but its endpoint does not answer: {exc}. Every memory call "
            f"fails until it exits: {info.stop_remedy(paths.discovery)}; the next memory call starts a fresh one.",
        )
    if answer == "grant_refused" and token is not None:
        return (
            "WARN",
            f"memory daemon pid {info.pid} answers but rejects this checkout's grant. Run: trw-mcp memory token",
        )
    from trw_memory import __version__ as installed

    if info.version != installed:
        from trw_mcp.server._doctor_version_skew import daemon_skew

        skew = daemon_skew(installed, str(info.version), int(info.pid))
        if skew is not None and skew.daemon_is_newer and not skew.incompatible:
            return (
                "WARN",
                f"memory daemon pid {info.pid} runs trw-memory {info.version}, newer than this client's {installed} but the "
                f"same major, so memory still works; upgrade this client when convenient.",
            )
        if skew is not None and skew.daemon_is_newer:
            return (
                "FAIL",
                f"memory daemon pid {info.pid} runs trw-memory {info.version}, NEWER than this client's {installed}: "
                f"their tool signatures differ, so this client can neither read nor write memory (memory_backend fails "
                f"the same way). Fix: {skew.remedy()}.",
            )
        # An upgrade leaves the old daemon serving old code until it exits (a pre-8.0 record carries no
        # start, so the installer cannot prove the pid and will not stop it); PASS here greened a stale verify.
        return (
            "WARN",
            f"memory daemon pid {info.pid} answers but runs trw-memory {info.version}, not the installed "
            f"{installed}; it serves the old code until it exits: {info.stop_remedy(paths.discovery)}; "
            f"the next memory call starts a {installed} daemon.",
        )
    uptime = _uptime_seconds(info.started_at)
    uptime_text = f"{uptime:.0f}s" if uptime is not None else "unknown"
    logger.debug("doctor_memory_daemon_reachable", pid=info.pid)
    return (
        "PASS",
        f"memory daemon answered at {info.url} (pid {info.pid}, up {uptime_text}, "
        f"version {info.version}). store: {paths.user_memory_dir}",
    )


def _checkout_grant(target: Path) -> str | None:
    """The grant of the checkout enclosing *target*, or ``None`` when it has none (the ping then goes without)."""
    from trw_memory.daemon import read_checkout_grant
    from trw_memory.exceptions import DaemonAuthError

    try:
        return read_checkout_grant(target)
    except DaemonAuthError:  # trw-fail-silent-allow: no grant is a probe without one; a 401 still proves the endpoint
        return None
