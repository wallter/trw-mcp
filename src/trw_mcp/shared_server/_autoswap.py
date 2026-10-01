"""HOTSWAP-AUTO: a shared server replaces itself when the installed trw-mcp / trw-memory changes.

One task beside the idle watchdog polls two signals: the installed distributions' versions (in process, about a
millisecond) and the env's interpreter record (``envs.json``, ``envs-pythonpath.json``). A change has to repeat on a
second poll, because ``pip`` is not atomic, and the interpreter the record names then has to import cleanly
(``probe_interpreter``) before anything starts. Then the server does what ``trw-mcp swap`` does by hand: it starts a
successor (``spawn_server(successor=True)`` + ``wait_published``). The successor publishes the record, drains this
server through the door, and every proxy follows the record on its next call: no client restart, no ``/mcp``.

Nothing here can take the env down. The old server keeps serving until the successor PUBLISHES (a successor that
fails to boot leaves it serving, and that target is retried with a doubling backoff). Only the server the record
names may start a successor, never while draining, never while another successor holds the claim lock, and never
twice at once (the env's spawn lock is held from the last re-check until the successor publishes).

The memory daemon follows the same way: once this server is current, a daemon that is strictly OLDER than this
process's trw-memory is drained by the graceful handshake (``_upgrade.drain_daemon``: in-flight calls finish, other
sessions mid-call answer ``busy`` and the daemon resumes; never a signal), retried with backoff. The next memory
call starts the new daemon through the client's own auto-start.

``swap --src`` envs are the operator's dev flow and are left alone. Every decision is one structured log line
(``shared_mcp_autoswap``); the last swap is kept in ``<env>.last-auto-swap`` for the successor to show in
``trw_status(detail="surface")`` and ``/admin/status``.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from packaging.version import InvalidVersion, Version
from trw_memory.daemon._discovery import DRAIN_CAPABILITY
from trw_memory.daemon._versions import is_older

from trw_mcp.models.config._fields_shared_mcp import SharedMcpConfig
from trw_mcp.shared_server._autoswap_ports import (
    Ports,
    Record,
    Versions,
    booted_versions,
    claim_busy,
    installed_versions,
    probe_interpreter,
    read_last_auto_swap,
    real_ports,
    spawn_lock,
    write_last_auto_swap,
)
from trw_mcp.shared_server._records import (
    SharedPaths,
    SharedServerError,
    env_python,
    env_pythonpath,
    read_live_record,
)

__all__ = [
    "HotSwap",
    "Ports",
    "Record",
    "Versions",
    "activate",
    "booted_versions",
    "build_hot_swap",
    "claim_busy",
    "installed_versions",
    "probe_interpreter",
    "read_last_auto_swap",
    "spawn_lock",
    "strictly_older",
    "surface_block",
    "write_last_auto_swap",
]

logger = structlog.get_logger(__name__)

_BACKOFF_CAP_SECONDS = 3600.0
#: Failed successor boots of ONE target before the watcher stops trying it (until the install or record changes).
_MAX_SWAP_FAILURES = 3
#: Drain attempts per episode (each closes the daemon's door for up to the drain window), then "drain it manually".
_DAEMON_MAX_ATTEMPTS = 5
#: An episode ends after this long with no older daemon to drain.
_DAEMON_EPISODE_SECONDS = 3600.0


# --------------------------------------------------------------------------- the watcher


def _key(hint: Versions, record: Record) -> tuple[Any, ...]:
    return (tuple(sorted(hint.items())), record)


def strictly_older(theirs: str, mine: str) -> bool:
    """Whether *theirs* is provably older than *mine*: PEP 440 (a ``.dev10`` precedes ``.dev11``), else numeric order."""
    try:
        return Version(theirs) < Version(mine)
    except InvalidVersion:
        return is_older(theirs, mine)


def baseline_hint(booted: Versions, hint: Versions) -> Versions:
    """The metadata baseline a fresh watcher compares against.

    An install that changed between this process importing its code and the watcher's first read would be absorbed as
    "no change". When the metadata disagrees with what the process booted (also every editable install, whose dist-info
    is stale), the baseline is empty: the first poll probes the interpreter and adopts or swaps on what it finds.
    """
    return hint if all(hint.get(name) == booted.get(name) for name in ("trw-mcp", "trw-memory")) else {}


class HotSwap:
    """Polls one shared server's installation; starts its own successor when it changed. See the module docstring."""

    def __init__(
        self,
        env: str,
        door: Any,
        ports: Ports,
        *,
        booted: Versions,
        hint: Versions,
        record: Record,
        pid: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        backoff_seconds: float = 300.0,
        daemon_backoff_seconds: float = 120.0,
    ) -> None:
        self._env, self._door, self._ports = env, door, ports
        self._booted, self._hint, self._record = booted, hint, record
        self._pid = os.getpid() if pid is None else pid
        self._clock, self._backoff, self._daemon_backoff = clock, backoff_seconds, daemon_backoff_seconds
        self._pending: tuple[Any, ...] | None = None
        self._retry_at: dict[tuple[Any, ...], tuple[float, int]] = {}
        self._daemon_attempts, self._daemon_next_at, self._daemon_reset_at = 0, 0.0, 0.0
        self._logged: tuple[Any, ...] | None = None
        self._state, self._detail, self._daemon_state = "watching", "", ""
        self.finished = False

    # ----- reporting

    def status(self) -> dict[str, Any]:
        return {"enabled": True, "state": self._state, "detail": self._detail, "daemon": self._daemon_state}

    def _decide(self, decision: str, **fields: Any) -> None:
        """One structured log line per decision; an unchanged repeat (a broken install, every poll) stays quiet."""
        marker = (decision, tuple(sorted((k, str(v)) for k, v in fields.items())))
        if marker != self._logged:
            self._logged = marker
            logger.info("shared_mcp_autoswap", decision=decision, env=self._env, **fields)

    def _set(self, state: str, detail: str = "") -> None:
        self._state, self._detail = state, detail

    def _skip(self, reason: str) -> None:
        self._set("skipped", reason)
        self._decide("skipped", reason=reason)

    # ----- the loop

    async def run(self, interval: float) -> None:
        """Poll until this server has handed over or is leaving; a failed poll is logged and never ends the server."""
        while not self.finished and not self._door.draining:
            await asyncio.sleep(interval)
            try:
                await self.step()
            except Exception:  # justified: boundary -- the watcher must never end the server it watches
                logger.warning("shared_mcp_autoswap_step_failed", env=self._env, exc_info=True)

    async def step(self) -> None:
        """One poll: at most one decision."""
        if self.finished or self._door.draining:
            return
        hint, record = self._ports.installed(), self._ports.recorded()
        if record[1] is not None and record == self._record:
            self._skip("the env serves a `swap --src` worktree; swap it by hand")
            return
        if hint == self._hint and record == self._record:
            self._pending = None
            self._set("watching")
            await self._daemon_step()
            return
        signal = _key(hint, record)
        if signal != self._pending:  # a first sighting waits one more poll: an install is not atomic
            self._pending = signal
            self._set("drift", "an install change was seen; confirming")
            self._decide(
                "drift_seen",
                **{"from": self._booted["trw-mcp"], "to": hint.get("trw-mcp", "?"), "record": record[0]},
            )
            return
        await self._swap(hint, record)

    # ----- the server swap

    async def _swap(self, hint: Versions, record: Record) -> None:
        key = _key(hint, record)
        if self._retry_at.get(key, (0.0, 0))[0] > self._clock():
            return
        if self._ports.live_pid() != self._pid:
            self._skip("the env's record names another server")
            return
        if self._ports.claim_busy():
            self._skip("a successor is already booting")
            return
        python, pythonpath = record
        probed = await asyncio.to_thread(self._ports.probe, python, pythonpath)
        if probed is None:
            self._set("drift", f"{python} does not import cleanly yet; retrying")
            self._decide("probe_failed", python=python)
            return
        if probed == self._booted and record == self._record:  # a same-version reinstall: nothing to load
            self._hint, self._pending = hint, None
            self._set("watching")
            self._decide("probe_same", version=probed["trw-mcp"])
            return
        origin, target = self._booted["trw-mcp"], probed["trw-mcp"]
        self._decide("probe_ok", **{"from": origin, "to": target, "python": python})
        with self._ports.spawn_lock() as held:
            if not held:
                self._skip("another process holds the env's spawn lock")
                return
            if self._ports.live_pid() != self._pid or self._door.draining or self._ports.claim_busy():
                self._skip("the env's record was taken over, or a successor began booting, during the probe")
                return
            row: dict[str, Any] = {
                "from": origin,
                "to": target,
                "at": datetime.now(timezone.utc).isoformat(),
                "outcome": "swapping",
                "from_pid": self._pid,
                "to_pid": None,
            }
            self._ports.write_last(row)
            self._set("swapping", f"{origin} -> {target}")
            try:
                successor = await asyncio.to_thread(self._ports.start_successor)
            except (
                SharedServerError
            ) as exc:  # trw-fail-silent-allow: logged + persisted + surfaced below, retried with backoff
                failures = self._retry_at.get(key, (0.0, 0))[1] + 1
                final = failures >= _MAX_SWAP_FAILURES  # a target that keeps failing (a train's RED build) stops here
                wait = math.inf if final else min(self._backoff * 2 ** (failures - 1), _BACKOFF_CAP_SECONDS)
                self._retry_at[key] = (self._clock() + wait, failures)
                self._ports.write_last({**row, "outcome": f"failed: {exc}"})
                again = "gave up: fix the install or run `trw-mcp swap`" if final else f"retry in {wait:.0f}s"
                self._set("failed", f"{origin} -> {target}: {exc}; serving {origin}, {again}")
                self._decide(
                    "successor_failed", error=str(exc), retry_seconds=None if final else wait, attempt=failures
                )
                return
            self._ports.write_last({**row, "outcome": "swapped", "to_pid": successor.pid})
            self.finished = True
            self._set("swapped", f"{origin} -> {target} (pid {successor.pid}); draining")
            self._decide("successor_published", **{"from": origin, "to": successor.version, "pid": successor.pid})

    # ----- the memory daemon

    async def _daemon_step(self) -> None:
        mine = self._booted.get("trw-memory")
        info = self._ports.daemon() if mine else None
        now = self._clock()
        if info is None or mine is None or not strictly_older(info.version, mine):
            if self._daemon_attempts and now >= self._daemon_reset_at:
                self._daemon_attempts = 0  # a quiet hour: a later older daemon starts a fresh episode
            return
        if DRAIN_CAPABILITY not in info.capabilities:
            self._daemon_state = f"trw-memory {info.version} daemon cannot be drained (no handshake): stop it by hand"
            self._decide("daemon_unsupported", version=info.version, pid=info.pid)
            return
        if self._daemon_attempts >= _DAEMON_MAX_ATTEMPTS:  # busy, refused or re-spawned older: stop closing its door
            self._daemon_state = (
                f"gave up draining the trw-memory {info.version} daemon after {self._daemon_attempts} attempts: "
                "drain it manually (`trw-mcp swap --daemon`)"
            )
            self._decide("daemon_gave_up", version=info.version, pid=info.pid, attempts=self._daemon_attempts)
            return
        if now < self._daemon_next_at:
            return
        why = await asyncio.to_thread(self._ports.drain_daemon, info, mine)
        self._daemon_attempts += 1  # one budget for every attempt: each closes the daemon's door for the drain window
        wait = min(self._daemon_backoff * 2 ** (self._daemon_attempts - 1), _BACKOFF_CAP_SECONDS)
        self._daemon_next_at = self._clock() + wait
        self._daemon_reset_at = self._clock() + _DAEMON_EPISODE_SECONDS
        if why:
            self._daemon_state = f"kept trw-memory {info.version} daemon serving: {why}; retry in {wait:.0f}s"
            decision = "daemon_busy" if "busy" in why else "daemon_refused"
            self._decide(decision, version=info.version, pid=info.pid, why=why, retry_seconds=wait)
            return
        self._daemon_state = f"drained trw-memory {info.version} daemon (pid {info.pid}); next call starts {mine}"
        self._decide("daemon_drained", **{"from": info.version, "to": mine, "pid": info.pid})


def build_hot_swap(
    env: str, door: Any, paths: SharedPaths, config: SharedMcpConfig, *, project_root: Path
) -> HotSwap | None:
    """The watcher for the server now booting, or ``None`` when ``shared_mcp.auto_swap`` is off."""
    if not config.auto_swap:
        return None
    booted = booted_versions()
    return HotSwap(
        env,
        door,
        real_ports(env, paths, project_root, pid=os.getpid()),
        booted=booted,
        hint=baseline_hint(booted, installed_versions()),
        record=(env_python(paths, env), env_pythonpath(paths, env)),
    )


# --------------------------------------------------------------------------- what trw_status shows


@dataclass(frozen=True)
class _Active:
    env: str
    paths: SharedPaths
    booted: Versions
    status: Callable[[], dict[str, Any]]


#: The shared server this process is, for ``surface_block``; ``None`` in every other process (stdio, CLI).
_ACTIVE: _Active | None = None


def activate(env: str, paths: SharedPaths, booted: Versions, status: Callable[[], dict[str, Any]]) -> None:
    """Make ``surface_block`` answer for this shared server."""
    global _ACTIVE
    _ACTIVE = _Active(env=env, paths=paths, booted=booted, status=status)


def surface_block() -> dict[str, Any] | None:
    """The ``version_drift`` block of ``trw_status(detail="surface")`` for a shared server, else ``None``."""
    active = _ACTIVE
    if active is None:
        return None
    try:
        record = read_live_record(active.paths, active.env)
    except SharedServerError:  # trw-fail-silent-allow: a status extra; an untrusted record shows no swap row
        record = None
    block: dict[str, Any] = {
        "booted_version": active.booted.get("trw-mcp", "?"),
        "installed_version": installed_versions().get("trw-mcp", "?"),
        "auto_swap": active.status(),
    }
    last = read_last_auto_swap(active.paths, active.env, live_pid=None if record is None else record.pid)
    if last is not None:
        block["last_auto_swap"] = {k: last.get(k) for k in ("from", "to", "at", "outcome")}
    return block
