"""`trw-mcp formation watch` (WATCH-WAKE-DESIGN rev 3): ancestor authority, re-checked every poll.

The real topology is a harness tailer that DESCENDS from the client (claude ->
shell -> watch). In-process tests stand in with the test process as the watch
and its parent as the client; the subprocess test runs the real module under
the test process as the client.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from typing import Any

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_pending_hint import _SRC, _own_pin, _tree_state
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms import _watch
from trw_mcp.formation._stall import StallFinding, clear_call, start_call
from trw_mcp.state._process_identity import process_start_time


def _client_is_my_parent(scene: SendScene, **overrides: Any) -> None:
    parent = os.getppid()
    _own_pin(scene, client_pid=parent, client_start=process_start_time(parent), **overrides)


def _observe(scene: SendScene, **overrides: Any) -> _watch.Observation:
    arguments: dict[str, Any] = {
        "pin_key": "pin-b",
        "formation_id": "release-train",
        "member_id": "impl-2",
        "caller_pid": os.getpid(),
        "trw_dir": scene.formation.trw_dir,
        "project_root": scene.formation.project_root,
    }
    return _watch.observe(**{**arguments, **overrides})


def test_a_descendant_of_the_pinned_client_sees_body_free_pending_facts(scene: SendScene) -> None:
    _client_is_my_parent(scene)
    empty = _observe(scene)
    assert (empty.refused, empty.count, empty.status) == (None, 0, "joined")
    assert scene.send("one")["status"] == "ok"
    one = _observe(scene)
    assert one.refused is None and one.count == 1 and one.watermark > empty.watermark


def test_invalid_joined_time_is_not_measured_not_a_watch_crash(scene: SendScene) -> None:
    import yaml

    from trw_mcp import formation

    _client_is_my_parent(scene)
    path = scene.formation.manifest_path()
    raw = yaml.safe_load(path.read_text())
    for member in raw["members"]:
        if member["member_id"] == "impl-2":
            member["joined_utc"] = "not-a-time"
    path.write_text(yaml.safe_dump(raw))
    board = formation.status(run_path=scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert board is not None and board.stall_measurement == "not_measured"
    observed = _observe(scene)
    assert observed.refused is None
    assert observed.stall_scope == ("not_measured", "measured")


def test_missing_mailbox_does_not_hide_overdue_call(scene: SendScene, monkeypatch, tmp_path) -> None:
    from trw_mcp import formation

    _client_is_my_parent(scene)
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    started = float(scene.rows("SELECT last_seen_at FROM endpoints WHERE member_id='impl-2'")[0][0])
    marker = start_call(context.manifest_path, "impl-2", started)
    monkeypatch.setattr("trw_mcp.comms._store.database_path", lambda _path: tmp_path / "missing.sqlite")
    monkeypatch.setattr("trw_mcp.comms._watch.time.time", lambda: started + 601)
    monkeypatch.setattr("trw_mcp.formation._views.time.time", lambda: started + 601)
    try:
        board = formation.status(run_path=scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
        assert board is not None
        assert board.stall_scope["mailbox"] == "not_measured"
        assert board.stall_scope["mcp_tool_calls"] == "measured"
        assert StallFinding("impl-2", "call_in_flight", 601) in board.stalls
        observed = _observe(scene)
        assert observed.refused is None
        assert observed.stall_scope == ("not_measured", "measured")
        assert StallFinding("impl-2", "call_in_flight", 601) in observed.stalls
    finally:
        clear_call(marker)


def test_real_watch_reports_mail_heartbeat_and_call_together(scene: SendScene, monkeypatch) -> None:
    from trw_mcp import formation

    _client_is_my_parent(scene)
    assert scene.send("overdue")["status"] == "ok"
    admitted = float(scene.rows("SELECT admitted_at FROM admissions")[0][0])
    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (admitted,))
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    marker = start_call(context.manifest_path, "impl-2", admitted)
    monkeypatch.setattr(_watch, "_lean_roots", lambda _f, _m: (scene.formation.project_root, scene.formation.trw_dir))
    monkeypatch.setattr(_watch.time, "time", lambda: admitted + 601)
    lines: list[str] = []
    try:
        assert (
            _watch.watch(
                pin_key="pin-b",
                formation_id="release-train",
                member_id="impl-2",
                caller_pid=os.getpid(),
                once=True,
                emit=lines.append,
            )
            == 0
        )
    finally:
        clear_call(marker)
    assert any("reason=mail_unfetched" in line for line in lines)
    assert any("reason=heartbeat_lost" in line for line in lines)
    assert any("reason=call_in_flight" in line for line in lines)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("sibling", "not_ancestor"),
        ("recycled", "client_gone"),
        ("wrong_pin", "pin_mismatch"),
        ("moved_run", "lineage_mismatch"),
        ("rebound", "pin_rebound"),
        ("unknown_formation", "formation_unavailable"),
    ],
)
def test_each_authority_failure_has_its_closed_reason(scene: SendScene, setup: str, reason: str) -> None:
    _client_is_my_parent(scene)
    overrides: dict[str, Any] = {}
    if setup == "sibling":  # a live process that is NOT this watch's ancestor
        sibling = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            _own_pin(scene, client_pid=sibling.pid, client_start=process_start_time(sibling.pid))
            assert _observe(scene).refused == reason
        finally:
            sibling.kill()
            sibling.wait()
        return
    if setup == "recycled":
        _own_pin(scene, client_pid=os.getppid(), client_start="0")
    elif setup == "wrong_pin":
        overrides["pin_key"] = "pin-a"
    elif setup == "moved_run":
        _client_is_my_parent(scene, run_path=str(scene.formation.member_runs["impl-1"]))
    elif setup == "rebound":
        overrides["captured"] = (os.getppid(), "some-earlier-client-start")
    else:
        overrides["formation_id"] = "no-such-formation"
    assert _observe(scene, **overrides).refused == reason


def test_one_client_pinning_two_runs_is_ambiguous_but_reconnect_siblings_are_not(scene: SendScene) -> None:
    _client_is_my_parent(scene)
    pins_path = scene.formation.trw_dir / "runtime" / "pins.json"
    pins = json.loads(pins_path.read_text(encoding="utf-8"))
    pins["pin-b-reconnected"] = {**pins["pin-b"]}  # a /mcp reconnect: same client, same run
    pins_path.write_text(json.dumps(pins), encoding="utf-8")
    assert _observe(scene).refused is None
    pins["other-chat"] = {**pins["pin-b"], "run_path": str(scene.formation.member_runs["impl-1"])}
    pins_path.write_text(json.dumps(pins), encoding="utf-8")
    assert _observe(scene).refused == "ambiguous_client"


def test_a_v3_mailbox_asks_for_the_upgrade_with_its_own_exit_code(scene: SendScene) -> None:
    _client_is_my_parent(scene)
    with sqlite3.connect(scene.formation.orchestrator_run / "comms.sqlite3") as conn:
        conn.execute("UPDATE schema_meta SET value='3' WHERE key='schema_version'")
    assert _observe(scene).refused == "mailbox_upgrade_required"
    assert _watch.EXIT_CODES["mailbox_upgrade_required"] != _watch.EXIT_CODES["not_ancestor"]


def _run_loop(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch, actions: list[Any], polls: int
) -> tuple[int, list[str]]:
    """Drive the real loop in-process; *actions* run one per sleep, between polls."""
    lines: list[str] = []
    pending = list(actions)

    def sleep(_seconds: float) -> None:
        if pending:
            pending.pop(0)()

    real_observe = _watch.observe
    monkeypatch.setattr(
        _watch,
        "observe",
        lambda **kw: real_observe(**kw, trw_dir=scene.formation.trw_dir, project_root=scene.formation.project_root),
    )
    code = _watch.watch(
        pin_key="pin-b",
        formation_id="release-train",
        member_id="impl-2",
        emit=lines.append,
        wait=sleep,
        caller_pid=os.getpid(),
        max_polls=polls,
    )
    return code, lines


def test_lines_print_only_on_new_mail_for_this_member_or_a_status_change(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp import formation

    _client_is_my_parent(scene)

    def to_impl_1() -> None:  # another member's traffic: must not print or move seq
        scene.actor("impl-2")
        assert scene.send("to-one", recipient_member_id="impl-1")["status"] == "ok"
        scene.actor("impl-1")

    def to_impl_2() -> None:
        assert scene.send("to-two")["status"] == "ok"

    def retire() -> None:
        formation.revise(
            "release-train",
            scene.formation.orchestrator_run,
            {"impl-2": {"status": "abandoned"}},
            trw_dir=scene.formation.trw_dir,
        )

    code, lines = _run_loop(scene, monkeypatch, [lambda: None, to_impl_1, to_impl_2, retire], polls=10)
    assert code == 0, "a terminal member ends the watch cleanly"
    assert lines == ["pending count=1 seq=1 status=joined", "pending count=0 seq=2 status=abandoned"]
    assert not any("to-" in line or "impl-1" in line for line in lines), "a line is a wake, never content"


def test_a_client_that_exits_mid_watch_ends_it_with_client_gone(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        _own_pin(scene, client_pid=client.pid, client_start=process_start_time(client.pid))
        # Stand in for ancestry: the watch is a "descendant" of this client until it dies.
        monkeypatch.setattr(_watch, "_is_ancestor", lambda client_pid, caller_pid: client_pid == client.pid)

        def kill_client() -> None:
            client.kill()
            client.wait()

        code, lines = _run_loop(scene, monkeypatch, [kill_client], polls=5)
    finally:
        if client.poll() is None:
            client.kill()
            client.wait()
    assert (code, lines) == (2, ["refused client_gone"])


def test_the_module_entry_runs_under_a_real_client_and_writes_nothing(scene: SendScene) -> None:
    """Real topology: this test process is the client; the watch is its child."""
    me = os.getpid()
    _own_pin(scene, client_pid=me, client_start=process_start_time(me))
    assert scene.send()["status"] == "ok"
    env = {**os.environ, "PYTHONPATH": str(_SRC), "TRW_PROJECT_ROOT": str(scene.formation.project_root)}
    argv = [sys.executable, "-m", "trw_mcp.comms._watch", "--formation", "release-train", "--member", "impl-2"]
    argv += ["--pin-key", "pin-b", "--once"]
    before = _tree_state(scene.formation.trw_dir)
    done = subprocess.run(argv, cwd=scene.formation.project_root, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.strip().splitlines()[-1] == "pending count=1 seq=1 status=joined"
    assert _tree_state(scene.formation.trw_dir) == before, "the watch wrote under .trw"


def test_live_client_sees_a_real_client_exit_that_the_cached_reader_would_hide() -> None:
    """C review SF1: the watch's identity read is the named uncached one, pinned by a real kill."""
    from trw_mcp.comms._hint import live_client
    from trw_mcp.state._process_identity import read_process_start_time

    client = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        entry: dict[str, object] = {"client_pid": client.pid, "client_start": read_process_start_time(client.pid)}
        process_start_time.cache_clear()
        assert process_start_time(client.pid) == entry["client_start"]  # now cached for this pid
        assert live_client(entry) == client.pid
        client.kill()
        client.wait()
        assert live_client(entry) is None, "an exited client must not stay live"
        assert process_start_time(client.pid) == entry["client_start"], "the cached reader would have hidden it"
    finally:
        if client.poll() is None:
            client.kill()
            client.wait()
        process_start_time.cache_clear()


def test_sigterm_ends_a_sleeping_watch_promptly(scene: SendScene) -> None:
    """C review SF2: a 60 s interval must not delay SIGTERM (time.sleep resumes after a handled signal)."""
    import signal
    import time

    me = os.getpid()
    _own_pin(scene, client_pid=me, client_start=process_start_time(me))
    assert scene.send()["status"] == "ok"  # mail waiting, so the first poll prints: the watch is now in its wait
    env = {**os.environ, "PYTHONPATH": str(_SRC), "TRW_PROJECT_ROOT": str(scene.formation.project_root)}
    argv = [sys.executable, "-m", "trw_mcp.comms._watch", "--formation", "release-train", "--member", "impl-2"]
    argv += ["--pin-key", "pin-b", "--interval-seconds", "60"]
    watch = subprocess.Popen(
        argv, cwd=scene.formation.project_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert watch.stdout is not None
        assert watch.stdout.readline().strip() == "pending count=1 seq=1 status=joined"
        started = time.monotonic()
        watch.send_signal(signal.SIGTERM)
        assert watch.wait(timeout=10) == 0
        assert time.monotonic() - started < 5, "SIGTERM waited out the poll interval"
    finally:
        if watch.poll() is None:
            watch.kill()
            watch.wait()


def test_a_pause_and_resume_each_print_one_status_line(scene: SendScene, monkeypatch: pytest.MonkeyPatch) -> None:
    """PAUSE-RESUME rev 2: the tailer wakes on status=paused, then status=resumed."""
    from trw_mcp.formation import pause, resume

    _client_is_my_parent(scene)
    f = scene.formation

    def do_pause() -> None:
        pause("release-train", f.orchestrator_run, "cut", trw_dir=f.trw_dir)

    def do_resume() -> None:
        resume("release-train", f.orchestrator_run, trw_dir=f.trw_dir)

    code, lines = _run_loop(scene, monkeypatch, [do_pause, lambda: None, do_resume, lambda: None], polls=5)
    assert code == 0
    assert lines == ["pending count=0 seq=1 status=paused", "pending count=0 seq=2 status=resumed"]
