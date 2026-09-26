"""PRD-CORE-274 FR13 hint clause: the lane-B managed-wake seam (lead board 661, msg 0ce25cd9).

Proves the parent-binding authority (a pin alone is not enough), the body-free
{count, watermark, member_status} answer, the monotonic watermark contract lane B's
driver depends on, and that the lean ``python -m trw_mcp.comms._hint`` entry writes
nothing under ``.trw`` and never raises.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms import _hint
from trw_mcp.state._process_identity import process_start_time

_SRC = Path(__file__).resolve().parents[2] / "src"


def _own_pin(scene: SendScene, pin_key: str = "pin-b", member: str = "impl-2", **overrides: Any) -> None:
    """Record THIS process as the client owning *pin_key*, so its parent is the observer."""
    pins_path = scene.formation.trw_dir / "runtime" / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pins[pin_key] = {
        "run_path": str(scene.formation.member_runs[member]),
        "pid": os.getpid(),
        "client_pid": os.getpid(),
        "client_start": process_start_time(os.getpid()),
        **overrides,
    }
    pins_path.write_text(json.dumps(pins), encoding="utf-8")


def _hint_for(scene: SendScene, **overrides: Any) -> _hint.PendingHint | None:
    arguments: dict[str, Any] = {
        "pin_key": "pin-b",
        "formation_id": "release-train",
        "member_id": "impl-2",
        "trw_dir": scene.formation.trw_dir,
        "project_root": scene.formation.project_root,
        "caller_parent_pid": os.getppid(),
    }
    return _hint.pending_hint(**{**arguments, **overrides})


def _ack_all(scene: SendScene) -> None:
    scene.actor("impl-2")
    items = asyncio.run(scene.server.call_tool("trw_inbox", {})).structured_content["items"]
    asyncio.run(scene.server.call_tool("trw_inbox", {"action": "ack", "message_ids": [i["message_id"] for i in items]}))
    scene.actor("impl-1")


def test_count_watermark_and_the_monotonic_admission_contract(scene: SendScene) -> None:
    _own_pin(scene)
    empty = _hint_for(scene)
    assert empty == _hint.PendingHint(count=0, watermark="", member_status="joined")
    assert scene.send("one")["status"] == scene.send("two")["status"] == "ok"
    two = _hint_for(scene)
    assert two is not None and two.count == 2 and two.watermark
    assert _hint.hint_advanced("", two.watermark) and not _hint.hint_advanced(two.watermark, two.watermark)

    _ack_all(scene)  # the queue empties
    drained = _hint_for(scene)
    assert drained is not None and (drained.count, drained.watermark) == (0, "")
    assert scene.send("three")["status"] == "ok"
    newest = _hint_for(scene)
    assert newest is not None and newest.count == 1
    assert _hint.hint_advanced(two.watermark, newest.watermark), "a new admission always advances"


@pytest.mark.parametrize(
    "mutation",
    ["wrong_pin", "wrong_parent", "recycled_client", "foreign_run", "no_client_pid", "unknown_formation"],
)
def test_the_pin_alone_is_not_authority(scene: SendScene, mutation: str) -> None:
    _own_pin(scene)
    assert _hint_for(scene) is not None, "positive control"
    if mutation == "wrong_pin":
        assert _hint_for(scene, pin_key="pin-a") is None
    elif mutation == "wrong_parent":
        assert _hint_for(scene, caller_parent_pid=1) is None
    elif mutation == "recycled_client":
        _own_pin(scene, client_start="0")
        assert _hint_for(scene) is None
    elif mutation == "foreign_run":
        _own_pin(scene, run_path=str(scene.formation.member_runs["impl-1"]))
        assert _hint_for(scene) is None
    elif mutation == "no_client_pid":
        _own_pin(scene, client_pid=None)
        assert _hint_for(scene) is None
    else:
        assert _hint_for(scene, formation_id="no-such-formation") is None


def test_a_terminal_member_reports_its_status_with_nothing_pending(scene: SendScene) -> None:
    from trw_mcp import formation

    _own_pin(scene)
    assert scene.send()["status"] == "ok"
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"status": "abandoned"}},
        trw_dir=scene.formation.trw_dir,
    )
    assert _hint_for(scene) == _hint.PendingHint(count=0, watermark="", member_status="abandoned")


def _tree_state(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def test_the_lean_module_entry_answers_writes_nothing_and_is_cheap(scene: SendScene) -> None:
    """Lane B's real topology: the driver (this test process) spawns the client AND the hint."""
    assert scene.send()["status"] == "ok"
    client = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _own_pin(scene, client_pid=client.pid, client_start=process_start_time(client.pid))
        env = {**os.environ, "PYTHONPATH": str(_SRC), "TRW_PROJECT_ROOT": str(scene.formation.project_root)}
        argv = [sys.executable, "-m", "trw_mcp.comms._hint", "--pin-key", "pin-b", "--formation", "release-train"]
        argv += ["--member", "impl-2", "--since", "", "--json"]
        before = _tree_state(scene.formation.trw_dir)
        started = time.perf_counter()
        done = subprocess.run(
            argv, cwd=scene.formation.project_root, env=env, capture_output=True, text=True, timeout=120
        )
        elapsed = time.perf_counter() - started
        after = _tree_state(scene.formation.trw_dir)
    finally:
        client.kill()
        client.wait()
    assert done.returncode == 0, done.stderr
    hint = json.loads(done.stdout.strip().splitlines()[-1])["hint"]
    assert hint is not None and (hint["count"], hint["advanced"], hint["member_status"]) == (1, True, "joined")
    assert after == before, "the lean hint wrote under .trw"
    print(f"lean_hint_wall_seconds={elapsed:.3f}")


def test_probe_answers_null_without_reading_anything() -> None:
    assert _hint.hint_payload("k", "f", "m", since=None, probe=True) == {"hint": None}


def _lineage(scene: SendScene, **overrides: Any) -> _hint.PinLineage | None:
    arguments: dict[str, Any] = {
        "pin_key": "pin-b",
        "formation_id": "release-train",
        "member_id": "impl-2",
        "trw_dir": scene.formation.trw_dir,
    }
    return _hint.pin_lineage(**{**arguments, **overrides})


def test_lineage_authenticates_the_persisted_pin_before_any_child_exists(scene: SendScene) -> None:
    """Lane B restart: recover the run only when manifest pin AND pin store agree (no parent binding)."""
    run = str(scene.formation.member_runs["impl-2"])
    _own_pin(scene, client_pid=None)  # the old child is gone: no live client is needed
    assert _lineage(scene) == _hint.PinLineage("joined", run, True, True, False)

    wrong_pin = _lineage(scene, pin_key="pin-a")
    assert wrong_pin is not None and (wrong_pin.pin_matches, wrong_pin.member_run) == (False, None)
    _own_pin(scene, run_path=str(scene.formation.member_runs["impl-1"]))
    moved = _lineage(scene)
    assert moved is not None and (moved.pin_matches, moved.pin_run_matches) == (True, False)
    assert _lineage(scene, formation_id="no-such-formation") is None
    assert _lineage(scene, member_id="nobody") is None


def test_lineage_reports_a_terminal_member(scene: SendScene) -> None:
    from trw_mcp import formation

    _own_pin(scene)
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"status": "abandoned"}},
        trw_dir=scene.formation.trw_dir,
    )
    found = _lineage(scene)
    assert found is not None and (found.terminal, found.member_status) == (True, "abandoned")


def test_lineage_module_entry_writes_nothing(scene: SendScene) -> None:
    _own_pin(scene, client_pid=None)
    env = {**os.environ, "PYTHONPATH": str(_SRC), "TRW_PROJECT_ROOT": str(scene.formation.project_root)}
    argv = [sys.executable, "-m", "trw_mcp.comms._hint", "--lineage", "--pin-key", "pin-b"]
    argv += ["--formation", "release-train", "--member", "impl-2", "--json"]
    before = _tree_state(scene.formation.trw_dir)
    done = subprocess.run(argv, cwd=scene.formation.project_root, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    lineage = json.loads(done.stdout.strip().splitlines()[-1])["lineage"]
    assert lineage == {
        "member_status": "joined",
        "member_run": str(scene.formation.member_runs["impl-2"]),
        "pin_matches": True,
        "pin_run_matches": True,
        "terminal": False,
    }
    assert _tree_state(scene.formation.trw_dir) == before, "the lineage probe wrote under .trw"


def test_an_empty_recorded_run_never_matches_even_from_the_member_run(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Path("")`` resolves to the cwd; standing in the member's run must not make "" match it."""
    monkeypatch.chdir(scene.formation.member_runs["impl-2"])
    _own_pin(scene, run_path="")
    found = _lineage(scene)
    assert found is not None and (found.pin_matches, found.pin_run_matches) == (True, False)
    assert _hint_for(scene) is None


def test_a_relaunched_server_session_start_rebinds_the_same_pin_to_the_new_client(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lane B restart option (c), lead board 727: no run-adoption call on the child surface is needed.

    The prior server AND its client are dead. A new server under the SAME
    TRW_SESSION_ID resolves pins[P] (pins survive restarts until the dead-pid AND
    stale-heartbeat TTL), and trw_session_start's run step re-pins it, rewriting
    client_pid/client_start to the new client, so the parent-bound hint passes.
    """
    from trw_mcp.state import _pin_store
    from trw_mcp.tools._ceremony_session_start_steps import step_run_resolve

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    _own_pin(scene, pid=dead.pid, client_pid=dead.pid, client_start="1.0")  # the old server and client, gone
    _pin_store.invalidate_pin_store_cache()
    assert _hint_for(scene) is None, "control: the dead client's record proves nothing"

    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    results: dict[str, Any] = {}
    run_dir, _ = step_run_resolve(None, results, [])  # type: ignore[arg-type]
    assert run_dir is not None and run_dir.resolve() == scene.formation.member_runs["impl-2"].resolve()
    pins = json.loads((scene.formation.trw_dir / "runtime" / "pins.json").read_text(encoding="utf-8"))
    assert pins["pin-b"]["client_pid"] == os.getppid()
    assert pins["pin-b"]["client_start"] == process_start_time(os.getppid())
    grandparent = _hint._parent_pid(os.getppid())
    assert grandparent is not None
    assert _hint_for(scene, caller_parent_pid=grandparent) is not None, "the new client now passes the hint"
