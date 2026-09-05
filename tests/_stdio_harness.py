"""Test-private stdio server harness for the PRD-CORE-262 handshake benchmark.

Owns spawn, initialize, call, timing, the child registry and the teardown that
reaps every child it created. The benchmark module contains measurement logic
and no inline subprocess plumbing.

Why ``trw_mcp/probe/harness.py`` is NOT reused (PRD-CORE-262-FR02, recorded here
so a later consolidation pass does not undo the decision by reading only the
file names): that module is the PRD-CORE-144 empirical-probe runner. It executes
one shell command inside the shared sandbox primitive and folds every failure --
sandbox unavailable, out of memory, timeout, bad input after validation -- into a
typed *inconclusive* result that never raises into the caller. A benchmark whose
entire value is refusing on exception cannot sit behind a fail-open verdict
wrapper. A test-only stdio client spawner shipped inside the public BSL-1.1
wheel would also add distributed surface for zero runtime benefit.

Why raw JSON-RPC framing rather than ``fastmcp.client.transports.StdioTransport``:
the transport spawns and owns its subprocess inside ``mcp.client.stdio`` and
exposes no pid, so a teardown built on it cannot assert at the OS level that it
left nothing behind -- which is the one property NFR02 turns into a test. Here
every child is a ``subprocess.Popen`` this module created, so ``live_children``
is an ``os.kill(pid, 0)`` answer rather than a hopeful one. The transport class
is still probed by :func:`stdio_import_skip_reason`, because if it stops
importing the stdio contract this benchmark defends has moved.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_TESTS_DIR = Path(__file__).resolve().parent
_TRW_MCP_SRC = _TESTS_DIR.parent / "src"
_TRW_MEMORY_SRC = _TESTS_DIR.parent.parent / "trw-memory" / "src"

# A child process does not inherit ``sys.path`` insertions, so PYTHONPATH must
# be pinned to the same two source roots ``tests/conftest.py`` inserts or the
# benchmark silently measures a stale installed build instead of the checkout.
_SRC_ROOTS: tuple[str, str] = (str(_TRW_MEMORY_SRC), str(_TRW_MCP_SRC))

# ``-m trw_mcp.server`` (never a PATH lookup of the console script): a PATH
# lookup can select a different, globally installed trw-mcp.
_SERVER_ARGV: tuple[str, ...] = ("-m", "trw_mcp.server")

_PROTOCOL_VERSION = "2025-06-18"
_READ_TIMEOUT_S = 100.0
_TERMINATE_GRACE_S = 10.0
_KILL_GRACE_S = 5.0

# Imports whose absence is the ONE sanctioned skip (NFR03). Everything else --
# an unresolvable store path, a non-zero child exit, a census that disagrees
# with N -- fails.
_REQUIRED_MODULE_SPECS: tuple[str, ...] = (
    "trw_mcp.server",
    "trw_mcp.server.__main__",
    "trw_mcp.state.memory_pressure",
)

# CORE262-09: checked by ACTUALLY importing the module and resolving the class,
# never by ``find_spec`` alone -- a spec check proves the module NAME resolves,
# not that ``StdioTransport`` still exists inside it. This is the one place the
# stdio contract this benchmark defends (raw JSON-RPC framing, not this class)
# is cross-checked against the library's own transport shape.
_TRANSPORT_MODULE = "fastmcp.client.transports"
_TRANSPORT_CLASS = "StdioTransport"


class HarnessError(RuntimeError):
    """Base for every harness failure. Never swallowed, never downgraded."""


class StdioTimeout(HarnessError):
    """A framed reply did not arrive inside the read deadline."""


class StdioClosed(HarnessError):
    """The child closed stdout (or died) before replying."""


class ChildLeak(HarnessError):
    """Teardown could not prove every spawned pid is gone."""


def stdio_import_skip_reason() -> str | None:
    """Return a reason naming the failed import, or ``None`` when all resolve.

    The transport check is deliberately stronger than the module checks: it
    actually imports ``fastmcp.client.transports`` and resolves
    ``StdioTransport`` off the module object, rather than stopping at
    ``find_spec``. A module whose spec resolves but that raises during import,
    or that imports fine but no longer defines the class, is exactly the
    "stdio contract moved" signal NFR03 exists to surface (CORE262-09).
    """
    for name in _REQUIRED_MODULE_SPECS:
        try:
            spec = importlib.util.find_spec(name)
        except Exception as exc:  # justified: a broken parent package raises here
            return f"stdio benchmark unavailable: import of {name!r} failed ({type(exc).__name__}: {exc})"
        if spec is None:
            return f"stdio benchmark unavailable: no importable spec for {name!r}"
    try:
        transports = importlib.import_module(_TRANSPORT_MODULE)
    except Exception as exc:  # justified: a broken/renamed dependency raises here
        return f"stdio benchmark unavailable: import of {_TRANSPORT_MODULE!r} failed ({type(exc).__name__}: {exc})"
    if not hasattr(transports, _TRANSPORT_CLASS):
        return f"stdio benchmark unavailable: {_TRANSPORT_MODULE!r} has no {_TRANSPORT_CLASS} attribute"
    return None


class _LineReader:
    """Deadline-bounded line reader over a raw pipe fd."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._buf = bytearray()

    def readline(self, deadline: float) -> bytes:
        while True:
            newline = self._buf.find(b"\n")
            if newline >= 0:
                line = bytes(self._buf[:newline])
                del self._buf[: newline + 1]
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StdioTimeout(f"no framed line within the read deadline (buffered {len(self._buf)} bytes)")
            ready, _, _ = select.select([self._fd], [], [], min(remaining, 0.25))
            if not ready:
                continue
            chunk = os.read(self._fd, 1 << 16)
            if not chunk:
                raise StdioClosed("child closed stdout before replying")
            self._buf.extend(chunk)


@dataclass
class ServerProcess:
    """One spawned trw-mcp stdio server plus its framing state."""

    label: str
    proc: subprocess.Popen[bytes]
    reader: _LineReader
    stderr_path: Path
    _ids: itertools.count[int] = field(default_factory=lambda: itertools.count(1))

    @property
    def pid(self) -> int:
        return self.proc.pid

    def next_id(self) -> int:
        return next(self._ids)


def pid_is_live(pid: int) -> bool:
    """OS-level liveness for a pid this process spawned.

    Only meaningful AFTER the child has been reaped (``Popen.poll``/``wait``):
    an unreaped zombie still answers signal 0. Teardown reaps first, then asks.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class StdioServerHarness:
    """Spawn, drive and reap trw-mcp stdio servers against one temporary store."""

    def __init__(self, project_root: Path, user_dir: Path, stderr_dir: Path) -> None:
        self.project_root = project_root
        self.user_dir = user_dir
        self.stderr_dir = stderr_dir
        self.stderr_dir.mkdir(parents=True, exist_ok=True)
        self._children: list[ServerProcess] = []
        self._spawned_pids: list[int] = []

    # ── environment ──────────────────────────────────────────────────────

    def child_env(self, session_id: str) -> dict[str, str]:
        """Build the child environment: no inherited ``TRW_*`` reaches the child.

        Dropping the whole ``TRW_*`` namespace first is what makes the "never
        touches a live store" claim checkable -- the test session's own
        isolation variables cannot leak a second root in behind our two.
        """
        env = {k: v for k, v in os.environ.items() if not k.startswith("TRW_")}
        env["PYTHONPATH"] = os.pathsep.join(_SRC_ROOTS)
        env["PYTHONUNBUFFERED"] = "1"
        env["TRW_PROJECT_ROOT"] = str(self.project_root)
        env["TRW_USER_DIR"] = str(self.user_dir)
        env["TRW_SESSION_ID"] = session_id
        env["TRW_HOT_PATH_STRICT"] = "0"
        return env

    # ── spawn / drive ────────────────────────────────────────────────────

    def spawn(self, label: str) -> ServerProcess:
        stderr_path = self.stderr_dir / f"{label}.stderr"
        handle = stderr_path.open("wb")
        try:
            # Fixed argv, no shell, no test-controlled interpolation (NFR03).
            proc = subprocess.Popen(
                [sys.executable, *_SERVER_ARGV],
                cwd=str(self.project_root),
                env=self.child_env(label),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=handle,
            )
        finally:
            handle.close()
        assert proc.stdout is not None and proc.stdin is not None
        server = ServerProcess(
            label=label, proc=proc, reader=_LineReader(proc.stdout.fileno()), stderr_path=stderr_path
        )
        self._children.append(server)
        self._spawned_pids.append(proc.pid)
        return server

    def _send(self, server: ServerProcess, message: Mapping[str, Any]) -> None:
        stdin = server.proc.stdin
        if stdin is None:  # pragma: no cover - Popen always gives us one
            raise HarnessError(f"{server.label}: stdin is closed")
        stdin.write(json.dumps(message).encode("utf-8") + b"\n")
        stdin.flush()

    def _await_id(self, server: ServerProcess, want: int, deadline: float) -> dict[str, Any]:
        while True:
            raw = server.reader.readline(deadline).strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except ValueError as exc:
                # CORE262-03: non-JSON on stdout IS the PRD-CORE-248 regression
                # this benchmark exists to catch -- a real stdio client cannot
                # parse it either. Skipping it here would let a server that
                # prints garbage before a valid reply pass silently.
                raise HarnessError(
                    f"{server.label}: non-JSON line on stdout (PRD-CORE-248 regression signal): "
                    f"{raw!r}; stderr captured at {server.stderr_path}"
                ) from exc
            if isinstance(message, dict) and message.get("id") == want:
                return message

    def initialize(self, server: ServerProcess, *, started_at: float) -> float:
        """Complete the MCP handshake; return ms since *started_at*."""
        request_id = server.next_id()
        self._send(
            server,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "trw-core-262-benchmark", "version": "0"},
                },
            },
        )
        reply = self._await_id(server, request_id, time.monotonic() + _READ_TIMEOUT_S)
        if "error" in reply:
            raise HarnessError(f"{server.label}: initialize failed: {reply['error']}")
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        self._send(server, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        return elapsed_ms

    def cold_initialize(self, label: str) -> tuple[ServerProcess, float]:
        """Spawn a fresh server and time spawn -> initialize reply, in ms."""
        started_at = time.monotonic()
        server = self.spawn(label)
        return server, self.initialize(server, started_at=started_at)

    def call(self, server: ServerProcess, tool: str, arguments: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
        """Call *tool* and return (elapsed ms, structured payload)."""
        request_id = server.next_id()
        started_at = time.monotonic()
        self._send(
            server,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": dict(arguments)},
            },
        )
        reply = self._await_id(server, request_id, time.monotonic() + _READ_TIMEOUT_S)
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        if "error" in reply:
            raise HarnessError(f"{server.label}: {tool} failed: {reply['error']}")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise HarnessError(f"{server.label}: {tool} returned no result object")
        if result.get("isError"):
            raise HarnessError(f"{server.label}: {tool} reported isError with {result.get('content')!r}")
        payload = result.get("structuredContent")
        return elapsed_ms, payload if isinstance(payload, dict) else {}

    # ── teardown ─────────────────────────────────────────────────────────

    def live_children(self) -> list[int]:
        """Reap what has exited, then return the pids still alive at the OS level."""
        for server in self._children:
            server.proc.poll()
        return [pid for pid in self._spawned_pids if pid_is_live(pid)]

    def reap_one(self, server: ServerProcess) -> None:
        """Terminate and reap a SINGLE child immediately, verifying it is gone.

        CORE262-01: a measured client that calls ``trw_session_start`` becomes
        a writer itself. Left alive until the arm-level ``teardown()`` (the
        pre-fix behaviour), repeat 2 of a 3-repeat arm ran against N+1
        writers and repeat 3 against N+2 -- the arm never measured the fixed
        writer count its own record claims. Reaping the measured client right
        after its call, before the next repeat's pre-repeat census check,
        keeps every repeat's writer population equal to *n_background*.
        """
        errors = self._stop(server)
        server.proc.poll()  # WNOHANG reap of what the OS has already finished
        if server in self._children:
            self._children.remove(server)
        if pid_is_live(server.pid):
            raise ChildLeak(f"{server.label}: pid {server.pid} still alive after reap_one; errors={errors}")
        if server.pid in self._spawned_pids:
            errors.extend(self._prune_writer_lock_for(server.pid))
            self._spawned_pids.remove(server.pid)
        if errors:
            raise HarnessError("; ".join(errors))

    def writers_dir(self) -> Path:
        """The writer-lock registry directory for this harness's store.

        Matches ``trw_memory.storage._writer_registry.WriterRegistry``'s own
        naming (``db_path.parent / f"{db_path.name}.writers"``) rather than an
        independently-typed literal, so a rename there and here cannot drift
        apart silently. There is currently no PUBLIC production accessor for
        this directory (only the pid-level census at
        ``trw_mcp.state.memory_pressure.live_memory_writer_pids``, which this
        harness already uses via :func:`writer_lock_pids`); this is the single
        place in the test tree that assembles the path, so a future production
        accessor has exactly one call site to redirect.
        """
        return self.project_root / ".trw" / "memory" / "memory.db.writers"

    def _prune_writer_lock_for(self, pid: int) -> list[str]:
        """Unlink one dead child's lock file and PROVE it is gone (CORE262-04).

        Failures are collected and returned rather than swallowed: a lock file
        that fails to unlink, or that is still present after ``unlink()``
        reports success, is a physical leak the writer CENSUS cannot see --
        ``live_memory_writer_pids`` (``trw_mcp.state.memory_pressure``)
        excludes any lock whose pid fails its liveness check, so a stale-but-
        undeleted lock reads as "no writer" even though the file is still on
        disk and a later PID reuse could resurrect it as a ghost writer.
        Verifying physical removal is the only way to catch this leak.
        """
        lock_path = self.writers_dir() / f"{pid}.lock"
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as exc:
            return [f"unlink {lock_path} failed: {type(exc).__name__}: {exc}"]
        if lock_path.exists():
            return [f"{lock_path} still present after unlink() reported success"]
        return []

    def _prune_our_writer_locks(self) -> list[str]:
        """Unlink the writer locks of the children we just killed.

        ``trw-memory``'s ``WriterRegistry`` removes its lock from an ``atexit``
        handler, and CPython does not run ``atexit`` on SIGTERM or SIGKILL --
        so every child this harness reaps leaves its ``<pid>.lock`` behind. Left
        in place across arms those files accumulate, and a dead pid the kernel
        later RECYCLES makes a stale lock read as a live writer, silently
        inflating the next arm's census past the writer-pressure threshold. Only
        locks for pids this harness spawned, and only after they are proven
        dead, are removed: this restores what a graceful exit would have left,
        it does not touch a peer's registry entry.
        """
        writers_dir = self.writers_dir()
        if not writers_dir.is_dir():
            return []
        errors: list[str] = []
        for pid in self._spawned_pids:
            if pid_is_live(pid):
                continue
            errors.extend(self._prune_writer_lock_for(pid))
        return errors

    def teardown(self) -> None:
        """Terminate every child, escalate to SIGKILL, then prove nothing survives.

        Every step is fault-tolerant on the way down and fault-INTOLERANT at the
        end: ``terminate()``/``wait()`` raising must not abandon the remaining
        children, so errors are collected rather than propagated immediately --
        but a collected error is re-raised once the sweep is complete, and a pid
        still alive after SIGKILL raises regardless. A benchmark that reported
        green over a leaked child would be reporting on a host it had polluted.
        """
        errors: list[str] = []
        for server in reversed(self._children):
            errors.extend(self._stop(server))
        leaked = self.live_children()
        # CORE262-04: collected, not suppressed -- a failed or incomplete
        # unlink is a physical leak the census-based leak check below cannot
        # detect on its own.
        errors.extend(self._prune_our_writer_locks())
        self._children.clear()
        if leaked:
            raise ChildLeak(f"{len(leaked)} spawned child(ren) still alive after teardown: {leaked}; errors={errors}")
        if errors:
            raise HarnessError("; ".join(errors))

    def _stop(self, server: ServerProcess) -> list[str]:
        errors: list[str] = []
        for closer in (server.proc.stdin, server.proc.stdout):
            if closer is not None:
                try:
                    closer.close()
                except Exception as exc:  # justified: collected, re-raised by teardown
                    errors.append(f"{server.label}: close failed ({type(exc).__name__}: {exc})")
        try:
            server.proc.terminate()
        except Exception as exc:  # justified: collected; SIGKILL below is the fallback
            errors.append(f"{server.label}: terminate failed ({type(exc).__name__}: {exc})")
        try:
            server.proc.wait(timeout=_TERMINATE_GRACE_S)
            return errors
        except Exception as exc:  # justified: collected; SIGKILL below is the fallback
            errors.append(f"{server.label}: wait after terminate failed ({type(exc).__name__}: {exc})")
        try:
            os.kill(server.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except Exception as exc:  # justified: collected, re-raised by teardown
            errors.append(f"{server.label}: SIGKILL failed ({type(exc).__name__}: {exc})")
        try:
            server.proc.wait(timeout=_KILL_GRACE_S)
        except Exception as exc:  # justified: collected; the leak check below is authoritative
            errors.append(f"{server.label}: wait after SIGKILL failed ({type(exc).__name__}: {exc})")
        return errors


def writer_lock_pids(trw_dir: Path) -> Sequence[int]:
    """Writer census through the PRODUCTION resolver, never a hardcoded glob.

    Going through ``live_memory_writer_pids`` is deliberate: it is the same
    function the doctor row uses, so a store relocation (PRD-CORE-253) makes the
    central assertion FAIL loudly instead of quietly becoming a no-op.
    """
    from trw_mcp.state.memory_pressure import live_memory_writer_pids

    return live_memory_writer_pids(trw_dir)
