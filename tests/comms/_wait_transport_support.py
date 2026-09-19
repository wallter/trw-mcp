"""T11 test-only stdio/barrier adapter; public comms handlers remain real.

Two inherited pipes observe worker boundaries and release a deterministic sleep.
Only scheduling/observation is injected: identity, transactions, cancellation
checks and the MCP notifications/cancelled handler are never replaced. Native
sleep mode observes the actual time.sleep C call without replacing its default.
Every child installs the production EOF adapter; it is not a test-side repair.
"""

from __future__ import annotations

import json
import os
import select
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests._stdio_harness import ServerProcess, _LineReader
from tests.comms._two_member_support import MemberHarness


class WaitPeer:
    """An initialized real stdio process with an out-of-band scheduling barrier."""

    def __init__(self, root: Path, scratch: Path, session: str) -> None:
        self.harness = MemberHarness(root, scratch / "user", scratch / session)
        events_r, events_w = os.pipe()
        release_r, self.release_w = os.pipe()
        self.events_r = events_r
        self.events = _LineReader(events_r)
        stderr_path = scratch / session / "server.stderr"
        env = {
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "HOME": str(scratch),
            "PYTHONPATH": os.pathsep.join(
                str(p)
                for p in (
                    Path(__file__).resolve().parents[2],
                    Path(__file__).resolve().parents[2] / "src",
                    Path(__file__).resolve().parents[3] / "trw-memory/src",
                )
            ),
            "TRW_PROJECT_ROOT": str(root),
            "TRW_SESSION_ID": session,
            "TRW_COMMS_ENABLED": "true",
            "TRW_CTX_ISOLATION_ENABLED": "true",
            "TRW_COMMS_WAIT_MAX_SECONDS": "30",
            "TRW_COMMS_SQLITE_BUSY_TIMEOUT_MS": "5000",
            "TRW_COMMS_LEASE_TTL_SECONDS": "300",
            "TRW_AUTO_UPGRADE": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        try:
            with stderr_path.open("wb") as stderr:
                proc = subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), str(events_w), str(release_r)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=stderr,
                    env=env,
                    cwd=root,
                    pass_fds=(events_w, release_r),
                )
        except BaseException:
            os.close(events_r)
            os.close(self.release_w)
            raise
        finally:
            os.close(events_w)
            os.close(release_r)
        assert proc.stdout is not None
        self.server = ServerProcess(session, proc, _LineReader(proc.stdout.fileno()), stderr_path)
        self.frames: list[dict[str, Any]] = []
        try:
            self.harness.initialize(self.server, started_at=time.monotonic())
        except BaseException:
            self.close()
            raise

    def start(self, tool: str, **arguments: Any) -> int:
        request = self.server.next_id()
        self.harness._send(
            self.server,
            {"jsonrpc": "2.0", "id": request, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
        )
        return request

    def reply(self, request: int) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while True:
            frame = json.loads(self.server.reader.readline(deadline))
            self.frames.append(frame)
            if frame.get("id") == request:
                assert "error" not in frame, frame
                assert not frame["result"].get("isError"), frame
                payload = frame["result"]["structuredContent"]
                assert isinstance(payload, dict), frame
                return payload

    def call(self, tool: str, **arguments: Any) -> dict[str, Any]:
        return self.reply(self.start(tool, **arguments))

    def event(self, kind: str) -> dict[str, Any]:
        result = json.loads(self.events.readline(time.monotonic() + 30))
        assert isinstance(result, dict), result
        assert result["event"] == kind, result
        return result

    def release(self) -> None:
        os.write(self.release_w, b"x")

    def cancel(self, request: int) -> None:
        self.harness._send(
            self.server,
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": request, "reason": "T11 cancellation proof"},
            },
        )

    def close(self) -> None:
        proc = self.server.proc
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                stream.close()
        os.close(self.events_r)
        os.close(self.release_w)


@contextmanager
def held_wait(scene: Any) -> Any:
    """Keep one parent-process waiter asleep while child writers race admission."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    import trw_mcp.comms as comms
    from tests.comms.conftest import call_peers
    from trw_mcp.comms._wait import run_bounded_wait

    scene.config.comms_wait_max_seconds = 30
    assert call_peers(scene.server, "enroll")["status"] == "ok"
    sleeping, release = threading.Event(), threading.Event()
    original = run_bounded_wait

    def sleep(_seconds: float) -> None:
        sleeping.set()
        assert release.wait(90), "admission race failed to release waiter"

    def loop(*args: Any, **kwargs: Any) -> Any:
        return original(*args, **kwargs, sleep=sleep)

    with scene.monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=1) as pool:
        patch.setattr(comms, "run_bounded_wait", loop)
        future = pool.submit(comms.inbox, wait_seconds=30)
        try:
            assert sleeping.wait(10)
            assert comms._WAIT_GUARD.locked()
            yield
        finally:
            # Stop retrying once the race ends; normal refusal/closure rules still apply.
            patch.setattr(comms, "run_bounded_wait", original)
            scene.config.comms_wait_max_seconds = 0
            release.set()
            future.result(timeout=10)
        assert not comms._WAIT_GUARD.locked()


def _serve(events: int, release: int) -> None:
    from anyio._backends._asyncio import CancelScope
    from anyio._core._eventloop import threadlocals
    from fastmcp import FastMCP
    from pytest import MonkeyPatch

    import trw_mcp.comms as comms
    import trw_mcp.tools.swarm_comms as tools
    from trw_mcp.comms._wait import run_bounded_wait
    from trw_mcp.server._eof_cancel import install_eof_cancel

    state: dict[str, Any] = {"armed": False, "connections": 0, "worker": False, "trace": False}
    observed_scopes: list[Any] = []
    original_cancel = CancelScope.cancel

    def cancel(scope: CancelScope, *args: Any, **kwargs: Any) -> None:
        original_cancel(scope, *args, **kwargs)
        if scope in observed_scopes and not state.get("cancel_observed"):
            state["cancel_observed"] = True
            emit("cancelled")

    patch = MonkeyPatch()
    patch.setattr(CancelScope, "cancel", cancel)
    original_loop, original_sqlite, original_inbox = run_bounded_wait, sqlite3.connect, comms.inbox

    def emit(kind: str, **values: Any) -> None:
        os.write(events, (json.dumps({"event": kind, **values}) + "\n").encode())

    class Connection(sqlite3.Connection):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            state["connections"] += 1
            self.set_trace_callback(lambda sql: emit("begin") if state["trace"] and sql == "BEGIN IMMEDIATE" else None)

        def close(self) -> None:
            super().close()
            state["connections"] -= 1

    def connect(*args: Any, **kwargs: Any) -> Any:
        return original_sqlite(*args, **kwargs, factory=Connection)

    def sleep(_seconds: float) -> None:
        emit("sleep", connections=state["connections"], guard=comms._WAIT_GUARD.locked())
        assert select.select([release], [], [], 90)[0], "test did not release sleep"
        assert os.read(release, 1) == b"x"

    def loop(*args: Any, **kwargs: Any) -> Any:
        if state["armed"] and not state.get("native_sleep"):
            return original_loop(*args, **kwargs, sleep=sleep)
        return original_loop(*args, **kwargs)

    def observe_native_sleep(_frame: Any, event: str, function: Any) -> None:
        if event == "c_call" and function is time.sleep and not state.get("native_sleep_seen"):
            state["native_sleep_seen"] = True
            emit("sleep", connections=state["connections"], guard=comms._WAIT_GUARD.locked(), native_sleep=True)

    def inbox(*args: Any, **kwargs: Any) -> Any:
        observed = state["armed"]
        if observed:
            state["worker"] = True
            scope = threadlocals.current_cancel_scope
            while scope is not None:
                observed_scopes.append(scope)
                scope = scope._parent_scope
        previous_profile = sys.getprofile()
        if observed and state.get("native_sleep"):
            sys.setprofile(observe_native_sleep)
        error = None
        try:
            return original_inbox(*args, **kwargs)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            sys.setprofile(previous_profile)
            if observed:
                state["worker"] = False
                emit("exit", guard=comms._WAIT_GUARD.locked(), connections=state["connections"], error=error)

    # Instrument the consuming seams without treating imported aliases as exports.
    patch.setattr(sqlite3, "connect", connect)
    patch.setattr(comms, "run_bounded_wait", loop)
    patch.setattr(tools, "inbox", inbox)
    server = FastMCP("real-wait-cancellation")
    tools.register_swarm_comms_tools(server)
    install_eof_cancel(server)

    @server.tool()
    def probe(arm: bool = False, trace: bool = False, native_sleep: bool = False) -> dict[str, Any]:
        state["native_sleep"] = state.get("native_sleep", False) or native_sleep
        state["armed"] = state["armed"] or arm
        state["trace"] = trace
        return {"worker": state["worker"], "guard": comms._WAIT_GUARD.locked()}

    try:
        server.run(transport="stdio", show_banner=False)
    finally:
        patch.undo()


if __name__ == "__main__":
    _serve(int(sys.argv[1]), int(sys.argv[2]))
