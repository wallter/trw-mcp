"""CORE274 real killed-process transaction boundaries, not power-loss proof."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms, joined_member
from tests.comms.test_policy import SendScene

_WORKER = r"""
import asyncio,json,os,sys,time
from contextlib import contextmanager
from pathlib import Path
from fastmcp import Client,FastMCP
from trw_mcp import comms
from trw_mcp.comms import _store
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools
server=FastMCP('crash-proof');register_swarm_comms_tools(server)
manifest=Path(sys.argv[1]);armed=None

def emit(event, **data):
    print('FRAME='+json.dumps({'event':event,**data}),flush=True)

def view(conn):
    return {table:[list(row) for row in conn.execute('SELECT * FROM '+table+' ORDER BY rowid')]
            for table in ('groups','endpoints','admissions','milestones','refusal_counts')}

def paused(phase, state):
    emit('STAGE',phase=phase,view=state)
    while True: time.sleep(1)

original=comms._operation
@contextmanager
def operation(*args,**kwargs):
    with original(*args,**kwargs) as state:
        yield state
        snapshot=view(state[0])
        if armed=='precommit':
            assert state[0].in_transaction
            paused(armed,snapshot)
    if armed=='postcommit': paused(armed,snapshot)
comms._operation=operation

async def main():
    global armed
    async with Client(server) as client:
        emit('READY')
        for raw in sys.stdin:
            command=json.loads(raw)
            if command.get('verify'):
                with _store.connect(manifest,busy_timeout_ms=1000) as conn:
                    snapshot=view(conn)
                status=await client.call_tool('trw_inbox',{'action':'status'})
                emit('VERIFIED',view=snapshot,status=status.structured_content)
                continue
            armed=command.get('crash')
            if command.get('mutation')=='early_exit': os._exit(23)
            if command.get('mutation')=='noop_marker':
                with _store.connect(manifest,busy_timeout_ms=1000) as conn: snapshot=view(conn)
                paused(armed,snapshot)
            result=await client.call_tool(command['tool'],command.get('args',{}))
            emit('RESULT',payload=result.structured_content)
asyncio.run(main())
"""


@pytest.fixture
def crash_scene(formation_env: FormationFixture, comms_server: Any, monkeypatch: pytest.MonkeyPatch) -> SendScene:
    config = enable_comms(monkeypatch)
    joined_member(formation_env, "impl-1", "pin-a")
    joined_member(formation_env, "impl-2", "pin-b")
    return SendScene(formation_env, comms_server, config, monkeypatch)


class Driver:
    """Own exactly one child; deadline partial-line reads and guaranteed reaping."""

    def __init__(self, scene: SendScene, pin: str, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        repo = Path(__file__).resolve().parents[3]
        env = {
            "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
            "HOME": str(directory),
            "PYTHONPATH": os.pathsep.join((str(repo / "trw-mcp/src"), str(repo / "trw-memory/src"))),
            "TRW_PROJECT_ROOT": str(scene.formation.project_root),
            "TRW_SESSION_ID": pin,
            "TRW_COMMS_ENABLED": "true",
            "TRW_CTX_ISOLATION_ENABLED": "true",
            "TRW_AUTO_UPGRADE": "false",
            "TRW_CLEANUP_ON_BOOT": "false",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
        }
        self.errors = (directory / "stderr.log").open("w")
        self.buffer = b""
        self.paused = False
        self.expected_exit = 0
        self.child = subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(scene.formation.manifest_path())],
            env=env,
            cwd=scene.formation.project_root,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.errors,
        )

    def send(self, **command: Any) -> None:
        if command.get("mutation") == "early_exit":
            self.expected_exit = 23
        assert self.child.stdin is not None
        self.child.stdin.write(json.dumps(command).encode() + b"\n")
        self.child.stdin.flush()

    def receive(self, expected: str, timeout: float = 20) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        assert self.child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(self.child.stdout, selectors.EVENT_READ)
            while True:
                while b"\n" in self.buffer:
                    line, self.buffer = self.buffer.split(b"\n", 1)
                    if line.startswith(b"FRAME="):
                        frame: dict[str, Any] = json.loads(line[6:])
                        assert frame["event"] == expected, frame
                        self.paused = expected == "STAGE"
                        return frame
                remaining = deadline - time.monotonic()
                assert remaining > 0 and selector.select(remaining), f"deadline waiting for {expected}"
                chunk = os.read(self.child.stdout.fileno(), 65536)
                assert chunk, f"child exited before {expected}: {self.child.poll()}"
                self.buffer += chunk
                assert len(self.buffer) <= 1048576, "unbounded child frame"

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        self.send(tool=tool, args=args)
        payload = self.receive("RESULT")["payload"]
        assert isinstance(payload, dict)
        return payload

    def kill(self) -> None:
        assert self.child.poll() is None, "child exited before controlled kill"
        self.expected_exit = -signal.SIGKILL
        self.child.kill()
        assert self.child.wait(timeout=5) == self.expected_exit

    def close(self) -> None:
        if self.paused and self.child.poll() is None:
            self.kill()
        if self.child.poll() is None:
            if self.child.stdin is not None:
                self.child.stdin.close()
            try:
                self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=5)
        self.errors.close()


class CleanupFailures(AssertionError):
    """Collected cleanup failures, compatible with the package's Python3.10 floor."""

    def __init__(self, failures: list[Exception]) -> None:
        self.failures = tuple(failures)
        super().__init__(f"{len(failures)} owned child cleanup failures: {self.failures!r}")


def close_all(children: list[Driver]) -> None:
    """Attempt every owned child before reporting any cleanup or exit failure."""
    failures: list[Exception] = []
    for driver in children:
        try:
            driver.close()
            assert driver.child.returncode == driver.expected_exit, "unexpected child exit"
        except Exception as exc:  # justified: aggregate only after attempting every child's cleanup
            failures.append(exc)
    if failures:
        raise CleanupFailures(failures)


@pytest.fixture
def driver_factory(crash_scene: SendScene, tmp_path: Path) -> Iterator[Any]:
    children: list[Driver] = []

    def make(pin: str) -> Driver:
        driver = Driver(crash_scene, pin, tmp_path / f"child-{len(children)}")
        children.append(driver)  # cleanup owns it even if initialization fails
        driver.receive("READY")
        return driver

    try:
        yield make
    finally:
        close_all(children)


def logical(scene: SendScene) -> dict[str, Any]:
    return {
        table: [list(row) for row in scene.rows(f"SELECT * FROM {table} ORDER BY rowid")]
        for table in ("groups", "endpoints", "admissions", "milestones", "refusal_counts")
    }


def assert_transition(state: dict[str, Any], operation: str) -> None:
    assert len(state["admissions"]) == 1, "missing admitted message"
    assert state["groups"][0][10] == 1, "missing/excess lifetime charge (column 11: charge)"
    row = state["admissions"][0]
    facts = {fact[1] for fact in state["milestones"]}
    assert row[10] == ("acked" if operation == "ack" else "pending"), "wrong delivery state (column 11: state)"
    assert facts == (
        {"admitted", "fetch_prepared"}
        if operation == "fetch"
        else {"admitted", "acked"}
        if operation == "ack"
        else {"admitted"}
    ), "missing intended operation fact"


def crash_proof(scene: SendScene, factory: Any, operation: str, phase: str, mutation: str | None = None) -> None:
    command: dict[str, Any]
    if operation == "admission":
        scene.actor("impl-2")
        assert call_peers(scene.server, "enroll")["status"] == "ok"
        child = factory("pin-a")
        command = {
            "tool": "trw_send",
            "args": {"recipient_member_id": "impl-2", "request_key": "crashed", "body": "body"},
        }
    else:
        child = factory("pin-b")
        assert child.call("trw_inbox", action="enroll")["status"] == "ok"
        scene.actor("impl-1")
        receipt = scene.send("seed", "body")["receipt"]
        args = {} if operation == "fetch" else {"action": "ack", "message_ids": [receipt["message_id"]]}
        command = {"tool": "trw_inbox", "args": args}
    baseline = logical(scene)
    child.send(**command, crash=phase, mutation=mutation)
    stage = child.receive("STAGE")
    assert stage["phase"] == phase
    assert_transition(stage["view"], operation)  # unchanged for no-op-marker controls
    child.kill()
    verifier = factory("pin-a")  # fresh process has no receiver token
    verifier.send(verify=True)
    verified = verifier.receive("VERIFIED")
    assert verified["status"]["status"] == "ok"
    if phase == "precommit":
        assert verified["view"] == baseline, "uncommitted writes survived killed process"
    else:
        assert verified["view"] == stage["view"], "committed transition was lost/changed"
        assert_transition(verified["view"], operation)
    expected_count = 0 if operation == "admission" and phase == "precommit" else 1
    assert len(verified["status"]["items"]) == expected_count
    if operation == "admission":
        retry = verifier.call("trw_send", **command["args"])
        assert retry["status"] == "ok"
        assert verifier.call("trw_send", **command["args"]) == retry
        if phase == "postcommit":
            assert retry["receipt"]["message_id"] == stage["view"]["admissions"][0][7]
        assert scene.rows("SELECT charge FROM groups") == [(1,)]
    else:
        assert verified["status"]["items"][0]["message_id"] == receipt["message_id"]
        assert (
            verifier.call("trw_send", recipient_member_id="impl-2", request_key="seed", body="body")["receipt"]
            == receipt
        )
        assert scene.rows("SELECT charge FROM groups") == [(1,)]
        # A new process cannot masquerade as the killed receiver to fetch/ACK.
        receiver = factory("pin-b")
        assert receiver.call("trw_inbox")["reason"] == "endpoint_replaced_by_newer_incarnation"


@pytest.mark.parametrize("operation", ["admission", "fetch", "ack"])
@pytest.mark.parametrize("phase", ["precommit", "postcommit"])
def test_real_killed_operation_boundary(
    crash_scene: SendScene, driver_factory: Any, operation: str, phase: str
) -> None:
    crash_proof(crash_scene, driver_factory, operation, phase)


@pytest.mark.parametrize("mutation", ["early_exit", "noop_marker"])
@pytest.mark.parametrize("operation", ["admission", "fetch", "ack"])
@pytest.mark.parametrize("phase", ["precommit", "postcommit"])
def test_failed_or_noop_child_cannot_pass_unchanged_crash_proof(
    crash_scene: SendScene, driver_factory: Any, operation: str, phase: str, mutation: str
) -> None:
    with pytest.raises(AssertionError):
        crash_proof(crash_scene, driver_factory, operation, phase, mutation)


def test_cleanup_attempts_later_children_and_aggregates_earlier_failures() -> None:
    from types import SimpleNamespace

    attempts: list[str] = []

    class FakeDriver:
        child = SimpleNamespace(returncode=0)
        expected_exit = 0

        def __init__(self, name: str, error: Exception | None) -> None:
            self.name, self.error = name, error

        def close(self) -> None:
            attempts.append(self.name)
            if self.error is not None:
                raise self.error

    timeout = subprocess.TimeoutExpired("synthetic owned child", 5)
    failed_kill = OSError("synthetic kill failure")
    children: list[Any] = [FakeDriver("first", timeout), FakeDriver("second", failed_kill), FakeDriver("last", None)]
    with pytest.raises(CleanupFailures, match="owned child cleanup failures") as captured:
        close_all(children)
    assert attempts == ["first", "second", "last"]
    assert captured.value.failures == (timeout, failed_kill)
