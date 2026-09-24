"""PRD-CORE-296 FR05: old unfetched mail and missing heartbeat are visible stalls."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp import formation
from trw_mcp.comms import _watch
from trw_mcp.formation._stall import StallFinding, clear_call, stall_scan, start_call


def _findings(scene: SendScene, now: float) -> list[StallFinding]:
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    return stall_scan(context.manifest, context.manifest_path, scene.formation.project_root, now=now).findings


def test_old_unfetched_mail_stalls_then_fetch_clears_it(scene: SendScene) -> None:
    assert scene.send()["status"] == "ok"
    admitted = float(scene.rows("SELECT admitted_at FROM admissions")[0][0])
    now = admitted + 601
    # Keep the endpoint fresh: this case must be caused by unfetched mail.
    original_seen = float(scene.rows("SELECT last_seen_at FROM endpoints WHERE member_id='impl-2'")[0][0])
    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (now,))
    finding = next(item for item in _findings(scene, now) if item.member_id == "impl-2")
    assert finding == StallFinding("impl-2", "mail_unfetched", 601, 1)
    assert "hello" not in str(finding.as_dict()) + finding.line()

    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (original_seen,))
    scene.actor("impl-2")
    result = asyncio.run(scene.server.call_tool("trw_inbox", {"action": "fetch"}))
    assert result.structured_content["items"]
    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (now,))
    assert not [item for item in _findings(scene, now) if item.member_id == "impl-2"]


def test_missing_heartbeat_stalls_with_zero_pending(scene: SendScene) -> None:
    seen = float(scene.rows("SELECT last_seen_at FROM endpoints WHERE member_id='impl-2'")[0][0])
    finding = next(item for item in _findings(scene, seen + 601) if item.member_id == "impl-2")
    assert finding.reason == "heartbeat_lost" and finding.pending == 0
    assert finding.line() == "stalled member=impl-2 reason=heartbeat_lost age=10"


def test_status_and_watch_surface_the_same_typed_finding(scene: SendScene, monkeypatch) -> None:
    assert scene.send()["status"] == "ok"
    admitted = float(scene.rows("SELECT admitted_at FROM admissions")[0][0])
    now = admitted + 601
    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (now,))
    monkeypatch.setattr("trw_mcp.formation._views.time.time", lambda: now)
    board = formation.status(run_path=scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert board is not None and board.stall_measurement == "measured"
    finding = next(item for item in board.stalls if item.member_id == "impl-2")
    in_flight = StallFinding("impl-2", "call_in_flight", 601)
    monkeypatch.setattr(
        _watch,
        "observe",
        lambda **_kw: _watch.Observation(client=(1, "start"), count=1, status="joined", stalls=(finding, in_flight)),
    )
    lines: list[str] = []
    assert _watch.watch(pin_key="p", formation_id="f", member_id="impl-2", once=True, emit=lines.append) == 0
    assert lines[-2:] == [finding.line(), in_flight.line()]


def test_in_flight_call_survives_read_restart_and_clears_on_exit(scene: SendScene, monkeypatch) -> None:
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    started = float(scene.rows("SELECT last_seen_at FROM endpoints WHERE member_id='impl-2'")[0][0])
    marker = start_call(context.manifest_path, "impl-2", started)
    try:
        now = started + 601
        scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (now,))
        # A new read (including a restarted process) sees the durable marker.
        findings = _findings(scene, now)
        assert StallFinding("impl-2", "call_in_flight", 601) in findings
        monkeypatch.setattr("trw_mcp.formation._views.time.time", lambda: now)
        board = formation.status(run_path=scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
        assert board is not None
        assert StallFinding("impl-2", "call_in_flight", 601) in board.stalls
    finally:
        clear_call(marker)
    assert StallFinding("impl-2", "call_in_flight", 601) not in _findings(scene, started + 601)


def test_completed_marker_disappearing_during_scan_is_ignored(scene: SendScene, monkeypatch) -> None:
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    started = float(scene.rows("SELECT last_seen_at FROM endpoints WHERE member_id='impl-2'")[0][0])
    gone = start_call(context.manifest_path, "impl-2", started)
    live = start_call(context.manifest_path, "impl-2", started)
    original_read = Path.read_text

    def racing_read(path: Path, *args, **kwargs):
        if path == gone:
            gone.unlink()
            raise FileNotFoundError(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", racing_read)
    try:
        scan = stall_scan(context.manifest, context.manifest_path, scene.formation.project_root, now=started + 601)
        assert scan.call_measurement == "measured"
        assert StallFinding("impl-2", "call_in_flight", 601) in scan.findings
    finally:
        clear_call(live)


def test_subprocess_crash_leaves_durable_call_marker(scene: SendScene) -> None:
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    started = float(scene.rows("SELECT last_seen_at FROM endpoints WHERE member_id='impl-2'")[0][0])
    script = "from pathlib import Path; import os,sys; from trw_mcp.formation._stall import start_call; start_call(Path(sys.argv[1]), 'impl-2', float(sys.argv[2])); os._exit(0)"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    subprocess.run([sys.executable, "-c", script, str(context.manifest_path), str(started)], env=env, check=True)
    call_dir = context.manifest_path.parent / "call-in-flight"
    try:
        scan = stall_scan(context.manifest, context.manifest_path, scene.formation.project_root, now=started + 601)
        assert StallFinding("impl-2", "call_in_flight", 601) in scan.findings
    finally:
        for marker in call_dir.glob("*.json"):
            clear_call(marker)


def test_formed_ctxless_fastmcp_tool_gets_call_marker(scene: SendScene) -> None:
    from trw_mcp.telemetry.tool_call_timing import wrap_tool

    scene.actor("impl-2")
    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    call_dir = context.manifest_path.parent / "call-in-flight"

    async def ctxless_probe() -> str:
        markers = list(call_dir.glob("*.json"))
        assert len(markers) == 1, "formed ctx-less tool must retain a marker while executing"
        blocked_since = float(json.loads(markers[0].read_text())["blocked_since"])
        assert (
            StallFinding("impl-2", "call_in_flight", 601)
            in stall_scan(
                context.manifest, context.manifest_path, scene.formation.project_root, now=blocked_since + 601
            ).findings
        )
        return "done"

    scene.server.tool()(wrap_tool(ctxless_probe))
    result = asyncio.run(scene.server.call_tool("ctxless_probe", {}))
    assert result.structured_content == {"result": "done"}
    assert not list(call_dir.glob("*.json")), "completed tool clears its marker"


def test_text_status_names_only_unmeasured_call_source(scene: SendScene, capsys) -> None:
    from trw_mcp.tools._formation_cli import _run_status

    context = formation.load(scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert context is not None
    call_dir = context.manifest_path.parent / "call-in-flight"
    call_dir.mkdir(exist_ok=True)
    bad = call_dir / "bad.json"
    bad.write_text("not-json")
    try:
        _run_status(argparse.Namespace(run_path=str(scene.formation.orchestrator_run), as_json=False))
        output = capsys.readouterr().out
        assert "stalls not_measured: mcp_tool_calls unavailable" in output
        assert "stalls not_measured: mailbox unavailable" not in output
    finally:
        bad.unlink()


def test_cli_and_mcp_status_project_real_unfetched_mail(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from trw_mcp.tools._formation_cli import _run_status
    from trw_mcp.tools.orchestration import register_orchestration_tools

    assert scene.send()["status"] == "ok"
    admitted = float(scene.rows("SELECT admitted_at FROM admissions")[0][0])
    now = admitted + 601
    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (now,))
    monkeypatch.setattr("trw_mcp.formation._views.time.time", lambda: now)

    _run_status(argparse.Namespace(run_path=str(scene.formation.orchestrator_run), as_json=True))
    cli = json.loads(capsys.readouterr().out)
    assert {"member_id": "impl-2", "reason": "mail_unfetched", "age_seconds": 601, "pending": 1} in cli["stalls"]
    assert cli["stall_scope"]["external_calls"] == "not_measured"

    register_orchestration_tools(scene.server)
    result = asyncio.run(scene.server.call_tool("trw_status", {}))
    assert isinstance(result.structured_content, dict)
    assert cli["stalls"][0] in result.structured_content["formation"]["stalls"]
    assert result.structured_content["formation"]["stall_scope"]["paging"] == "not_measured"


def test_explicit_cross_project_status_uses_resolved_formation_root(
    scene: SendScene, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from trw_mcp.state import _paths

    assert scene.send()["status"] == "ok"
    admitted = float(scene.rows("SELECT admitted_at FROM admissions")[0][0])
    now = admitted + 601
    scene.rows("UPDATE endpoints SET last_seen_at=? WHERE member_id='impl-2'", (now,))
    unrelated = tmp_path / "unrelated"
    monkeypatch.setattr(_paths, "resolve_project_root", lambda: unrelated)
    monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: unrelated / ".trw")
    monkeypatch.setattr("trw_mcp.formation._views.time.time", lambda: now)
    board = formation.status(run_path=scene.formation.orchestrator_run, trw_dir=scene.formation.trw_dir)
    assert board is not None and board.stall_measurement == "measured"
    assert StallFinding("impl-2", "mail_unfetched", 601, 1) in board.stalls
