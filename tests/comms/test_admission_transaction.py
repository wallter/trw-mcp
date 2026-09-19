"""CORE274 real-process admission races, rollback and correspondence controls."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms._wait_transport_support import held_wait
from tests.comms.test_policy import SendScene, scene  # noqa: F401

_WORKER = """
import asyncio,json,sys
from fastmcp import FastMCP
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools
from trw_mcp.comms import _admission
if sys.argv[2] == 'disable_admission_limit':
    _admission.check_limits = lambda *args: None
server=FastMCP('transaction-race'); register_swarm_comms_tools(server)
print('READY',flush=True)
assert sys.stdin.readline().strip()=='go'
async def run():
    result=await server.call_tool('trw_send',{'recipient_member_id':'impl-2','request_key':sys.argv[1],'body':'race'})
    print('RESULT='+json.dumps(result.structured_content),flush=True)
asyncio.run(run())
"""


def await_ready(children: list[subprocess.Popen[str]], timeout: float) -> None:
    """Bound initialization before releasing any participant, including partial lines."""
    deadline = time.monotonic() + timeout
    buffers: dict[int, bytes] = {}
    with selectors.DefaultSelector() as selector:
        for child in children:
            assert child.stdout is not None
            selector.register(child.stdout, selectors.EVENT_READ)
            buffers[child.stdout.fileno()] = b""
        while selector.get_map():
            remaining = deadline - time.monotonic()
            assert remaining > 0, "child READY deadline exceeded"
            events = selector.select(remaining)
            assert events, "child READY deadline exceeded"
            for key, _events in events:
                chunk = os.read(key.fd, 4096)
                assert chunk, "child exited before READY"
                buffers[key.fd] += chunk
                assert len(buffers[key.fd]) <= 4096, "unexpected child initialization output"
                if b"\n" in buffers[key.fd]:
                    assert buffers[key.fd] == b"READY\n", "child did not reach synchronized start barrier"
                    selector.unregister(key.fd)


def race(
    scene: SendScene, tmp_path: Path, *, same_key: bool = False, disable_guard: bool = False, ready_timeout: float = 45
) -> list[dict[str, Any]]:
    repo = Path(__file__).resolve().parents[3]
    home = tmp_path / "child-home"
    home.mkdir()
    env = {
        "PATH": str(Path(sys.executable).parent) + ":/usr/bin:/bin",
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join((str(repo / "trw-mcp/src"), str(repo / "trw-memory/src"))),
        "TRW_PROJECT_ROOT": str(scene.formation.project_root),
        "TRW_SESSION_ID": "pin-a",
        "TRW_COMMS_ENABLED": "true",
        "TRW_CTX_ISOLATION_ENABLED": "true",
        "TRW_AUTO_UPGRADE": "false",
        "TRW_CLEANUP_ON_BOOT": "false",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    children: list[subprocess.Popen[str]] = []
    try:
        for index in range(16):
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _WORKER,
                        "same" if same_key else f"key-{index}",
                        "disable_admission_limit" if disable_guard else "guarded",
                    ],
                    cwd=scene.formation.project_root,
                    env=env,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )
        await_ready(children, ready_timeout)
        for child in children:
            assert child.stdin is not None
            child.stdin.write("go\n")
            child.stdin.close()
            child.stdin = None
        results = []
        for child in children:
            stdout, stderr = child.communicate(timeout=45)
            assert child.returncode == 0, stderr
            frames = [line.removeprefix("RESULT=") for line in stdout.splitlines() if line.startswith("RESULT=")]
            assert len(frames) == 1, stdout + stderr
            results.append(json.loads(frames[0]))
        return results
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)


@pytest.mark.parametrize("scene", [{"comms_group_admission_limit": 5}], indirect=True)
@pytest.mark.parametrize("disable_guard", [False, True])
@pytest.mark.parametrize("waiter_present", [False, True])
def test_sixteen_process_budget_race_and_process_local_guard_control(
    scene: SendScene, tmp_path: Path, disable_guard: bool, waiter_present: bool
) -> None:
    with held_wait(scene) if waiter_present else nullcontext():
        results = race(scene, tmp_path, disable_guard=disable_guard)
    accepted = sum(result["status"] == "ok" for result in results)
    if disable_guard:
        # Only the admission guard is disabled. Integrity remains active and
        # may refuse subsequent calls once the first overflow corrupts policy.
        assert accepted > 5
        assert scene.send("later")["reason"] == "storage_corrupt"
    else:
        assert accepted == 5
        assert {result.get("reason") for result in results if result["status"] != "ok"} == {"group_admission_limit"}
    assert scene.rows("SELECT charge FROM groups") == [(accepted,)]
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(accepted,)]
    assert scene.rows("SELECT COUNT(*) FROM milestones WHERE fact='admitted'") == [(accepted,)]


def test_sixteen_process_exact_retries_have_one_receipt(scene: SendScene, tmp_path: Path) -> None:
    results = race(scene, tmp_path, same_key=True)
    assert all(result["status"] == "ok" for result in results)
    assert len({result["receipt"]["message_id"] for result in results}) == 1
    assert scene.rows("SELECT charge FROM groups") == [(1,)]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE groups SET charge=charge+1",
        "DELETE FROM milestones WHERE fact='admitted'",
        "UPDATE milestones SET at=at+9999",
        "UPDATE admissions SET recipient_incarnation='00000000000000000000000000000000'",
        "UPDATE groups SET rate_limit=1",
    ],
)
def test_corrupt_correspondence_refuses_without_mutation(scene: SendScene, sql: str) -> None:
    from trw_mcp.comms import _store

    assert scene.send("a")["status"] == "ok"
    assert scene.send("b")["status"] == "ok"
    scene.rows(sql)
    path = _store.database_path(scene.formation.manifest_path())
    before = path.read_bytes()
    assert scene.send("c")["reason"] == "storage_corrupt"
    assert path.read_bytes() == before


def test_corruption_between_open_and_operation_lock_is_detected(scene: SendScene) -> None:
    from contextlib import contextmanager

    import trw_mcp.comms as comms

    original = comms.immediate

    @contextmanager
    def changed(conn: Any) -> Any:
        # Independent committed writer after connect validated, before the
        # operation obtains its lock. Never mutate real production guards.
        scene.rows("UPDATE groups SET charge=99")
        with original(conn):
            yield conn

    scene.monkeypatch.setattr(comms, "immediate", changed)
    assert scene.send()["reason"] == "storage_corrupt"
    assert scene.rows("SELECT charge FROM groups") == [(99,)]
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


@pytest.mark.parametrize("statement", ["INSERT INTO admissions", "UPDATE groups SET charge", "INSERT INTO milestones"])
def test_failure_after_each_admission_write_rolls_back_everything(scene: SendScene, statement: str) -> None:
    from trw_mcp.comms import _store

    real_connect = _store.sqlite3.connect

    class Failing(_store.sqlite3.Connection):
        def execute(self, sql: str, parameters: Any = ()) -> Any:
            result = super().execute(sql, parameters)
            if sql.startswith(statement):
                raise _store.sqlite3.DatabaseError("injected after actual write")
            return result

    def connect(*args: Any, **kwargs: Any) -> Any:
        return real_connect(*args, **kwargs, factory=Failing)

    before = scene.rows("SELECT * FROM groups")
    with scene.monkeypatch.context() as patch:
        patch.setattr(_store.sqlite3, "connect", connect)
        assert scene.send()["reason"] == "storage_corrupt"
    assert scene.rows("SELECT * FROM groups") == before
    for table in ("admissions", "milestones", "refusal_counts"):
        assert scene.rows(f"SELECT COUNT(*) FROM {table}") == [(0,)]


def test_refusal_counter_saturates_without_refused_body_retention(scene: SendScene) -> None:
    from trw_mcp.comms._policy import MAX_COUNTER

    assert scene.send()["status"] == "ok"
    assert scene.send(body="refused-secret")["reason"] == "idempotency_conflict"
    scene.rows("UPDATE refusal_counts SET count=?", (MAX_COUNTER,))
    for _ in range(5):
        assert scene.send(body="refused-secret")["reason"] == "idempotency_conflict"
    assert scene.rows("SELECT count,typeof(count) FROM refusal_counts") == [(MAX_COUNTER, "integer")]
    assert scene.rows("SELECT body FROM admissions") == [("hello",)]


def test_busy_refusal_is_bounded_retryable_and_does_not_count(scene: SendScene) -> None:
    import time

    from trw_mcp.comms import _store

    scene.config.comms_sqlite_busy_timeout_ms = 20
    conn = _store.sqlite3.connect(_store.database_path(scene.formation.manifest_path()), isolation_level=None)
    before = scene.rows("SELECT * FROM groups")
    try:
        conn.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        refused = scene.send()
        elapsed = time.monotonic() - started
        assert refused["reason"] == "storage_contended"
        assert refused["retryable"] is True
        assert 0.015 <= elapsed < 0.75
    finally:
        conn.rollback()
        conn.close()
    assert scene.rows("SELECT * FROM groups") == before
    assert scene.rows("SELECT COUNT(*) FROM refusal_counts") == [(0,)]
    assert scene.send()["status"] == "ok"


def test_invalid_binding_cannot_count_refusal_or_mutate_mailbox(scene: SendScene) -> None:
    from trw_mcp.comms import _store

    path = _store.database_path(scene.formation.manifest_path())
    before = path.read_bytes()
    scene.monkeypatch.setenv("TRW_SESSION_ID", "unbound")
    assert scene.send()["reason"] == "no_pinned_run"
    assert path.read_bytes() == before


@pytest.mark.parametrize("worker", ["import time; time.sleep(60)", "raise SystemExit(2)"])
def test_child_initialization_failure_is_bounded_and_reaps_children(
    scene: SendScene, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, worker: str
) -> None:
    children: list[subprocess.Popen[str]] = []
    original = subprocess.Popen

    def tracked(*args: Any, **kwargs: Any) -> subprocess.Popen[str]:
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(sys.modules[__name__], "_WORKER", worker)
    monkeypatch.setattr(subprocess, "Popen", tracked)
    started = time.monotonic()
    with pytest.raises(AssertionError, match="READY"):
        race(scene, tmp_path, ready_timeout=0.3)
    assert time.monotonic() - started < 5
    assert len(children) == 16
    assert all(child.poll() is not None for child in children)
    assert scene.rows("SELECT charge FROM groups") == [(0,)]


def test_verifier_refuses_a_persisted_history_that_breaks_the_rate_policy(scene: SendScene) -> None:
    """The VERIFIER's rate-history invariant, not the admission-time rate limit.

    The name this test used to carry claimed the admission-time check, and a
    mutation showed it did not discriminate that: deleting the rate branch from
    ``_policy.check_limits`` left it passing. What it actually discriminates is
    ``_schema.verify``'s sliding-window replay over persisted ``admitted_at``
    values -- neutralizing that check makes this fail. Both checks are real and
    independent; the admission-time one is covered by
    ``test_send_contract.py::...same_time...`` and ``test_policy.py``.

    Extreme finite times are the interesting input because ``now - 60 == now``
    there, so a persisted history that violates the policy cannot be hidden by
    subtraction collapsing the window. The refusal is fail-closed at connect and
    writes no byte.
    """
    from trw_mcp.comms import _store

    assert scene.send("a")["status"] == "ok"
    assert scene.send("b")["status"] == "ok"
    huge = 1e20
    scene.rows("UPDATE groups SET group_time=?,rate_limit=1", (huge,))
    scene.rows("UPDATE endpoints SET last_seen_at=?,lease_expires_at=?", (huge, huge + 16384))
    scene.rows("UPDATE admissions SET admitted_at=?", (huge,))
    scene.rows("UPDATE milestones SET at=?", (huge,))
    path = _store.database_path(scene.formation.manifest_path())
    before = path.read_bytes()
    assert scene.send("c")["reason"] == "storage_corrupt"
    assert path.read_bytes() == before


def test_comms_never_reaches_for_an_advisory_file_lock() -> None:
    """The admission race is won by SQLite's own locks, and only by those.

    This replaces a worker stub that no-oped ``fcntl.flock`` before importing
    TRW. That stub could not fail: nothing under ``comms`` calls flock, so it
    proved nothing, while silently disabling advisory locking for every OTHER
    TRW subsystem the worker touched. The claim is structural, so check it
    structurally -- adding an advisory lock to comms fails this test, which is
    exactly what the stub was reaching for.
    """
    import trw_mcp.comms as comms

    package = Path(comms.__file__).parent
    offenders = {
        module.name: line.strip()
        for module in sorted(package.glob("*.py"))
        for line in module.read_text(encoding="utf-8").splitlines()
        if any(token in line for token in ("fcntl", "flock", "lockf", "_locking"))
    }
    assert offenders == {}, offenders
