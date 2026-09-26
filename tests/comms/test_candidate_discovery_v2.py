"""CORE-296 FR06: two stdio processes announce, one lead discovers live state."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import enable_comms
from tests.comms.test_candidate_admission import _lead_formation, _peers, bootstrap_scene  # noqa: F401
from trw_mcp import formation

_SRC = Path(__file__).resolve().parents[2] / "src"
_ANNOUNCE = """
import asyncio, json
from fastmcp import FastMCP
from trw_mcp.models import config as cfg
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools
cfg.get_config = lambda: TRWConfig(comms_enabled=True)
server = FastMCP('candidate-process')
register_swarm_comms_tools(server)
result = asyncio.run(server.call_tool('trw_inbox', {'action': 'announce'}))
print(json.dumps(result.structured_content))
"""


def _announce_in_process(fixture: FormationFixture) -> dict[str, object]:
    env = {**os.environ, "TRW_SESSION_ID": "pin-b", "PYTHONPATH": str(_SRC)}
    completed = subprocess.run(
        [sys.executable, "-c", _ANNOUNCE],
        cwd=fixture.project_root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout.splitlines()[-1])


def test_two_process_retry_and_lead_discovery_filter_live_states(
    bootstrap_scene, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = formation_env
    first = _announce_in_process(fixture)
    second = _announce_in_process(fixture)
    assert first["status"] == second["status"] == "ok"
    assert first["candidate_id"] == second["candidate_id"], "same pin/run has one durable handle"
    handle = str(first["candidate_id"])

    _lead_formation(fixture)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    discovered = _peers(bootstrap_scene, "discover")
    assert [(item["candidate_id"], item["state"]) for item in discovered["candidates"]] == [(handle, "active")]
    assert discovered["candidates"][0]["age_seconds"] >= 0
    assert "pin-b" not in repr(discovered)
    assert str(fixture.member_runs["impl-2"]) not in repr(discovered)

    formation.set_candidate_state(fixture.trw_dir, handle, "admitted", admitted_formation="release-train")
    assert [(item["candidate_id"], item["state"]) for item in _peers(bootstrap_scene, "discover")["candidates"]] == [
        (handle, "admitted")
    ]
    formation.set_candidate_state(fixture.trw_dir, handle, "active", admitted_formation=None)
    expired = formation.announce_candidate(
        fixture.trw_dir,
        pin_key="pin-b",
        run_path=fixture.member_runs["impl-2"],
        worktree=None,
        client="codex",
        ttl_seconds=-1,
        now=time.time(),
    )
    assert expired.candidate_id == handle
    assert _peers(bootstrap_scene, "discover")["candidates"] == []

    renewed = formation.announce_candidate(
        fixture.trw_dir,
        pin_key="pin-b",
        run_path=fixture.member_runs["impl-2"],
        worktree=None,
        client="codex",
        ttl_seconds=600,
    )
    formation.set_candidate_state(fixture.trw_dir, renewed.candidate_id, "admitted", admitted_formation="release-train")
    formation.set_candidate_state(fixture.trw_dir, renewed.candidate_id, "joining")
    assert [(item["candidate_id"], item["state"]) for item in _peers(bootstrap_scene, "discover")["candidates"]] == [
        (renewed.candidate_id, "joining")
    ]
    formation.set_candidate_state(fixture.trw_dir, renewed.candidate_id, "revoked")
    assert _peers(bootstrap_scene, "discover")["candidates"] == []


def test_discover_caps_candidate_page_and_reports_truncation(
    bootstrap_scene, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = formation_env
    _lead_formation(fixture)
    config = enable_comms(monkeypatch, comms_body_max_bytes=512, comms_response_max_bytes=8192)
    for index in range(formation.CANDIDATE_CAP):
        formation.announce_candidate(
            fixture.trw_dir,
            pin_key=f"pin-{index}",
            run_path=fixture.project_root / f"candidate-{index}",
            worktree=fixture.project_root / ("w" * 80),
            client="C" * 80,
            ttl_seconds=600,
        )
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    discovered = _peers(bootstrap_scene, "discover")
    assert discovered["candidate_total"] == formation.CANDIDATE_CAP
    assert discovered["candidates_truncated"] is True
    assert 0 < len(discovered["candidates"]) < formation.CANDIDATE_CAP
    seen = {item["candidate_id"] for item in discovered["candidates"]}
    cursor = discovered["next_cursor"]
    assert isinstance(cursor, str)
    import asyncio

    invalid = asyncio.run(bootstrap_scene.call_tool("trw_inbox", {"action": "discover", "cursor": cursor + "x"}))
    assert invalid.structured_content["reason"] == "invalid_cursor"
    while cursor is not None:
        result = asyncio.run(bootstrap_scene.call_tool("trw_inbox", {"action": "discover", "cursor": cursor}))
        page = result.structured_content
        assert isinstance(page, dict) and page["status"] == "ok", page
        ids = {item["candidate_id"] for item in page["candidates"]}
        assert not (ids & seen), "cursor pages do not repeat candidates"
        seen |= ids
        cursor = page.get("next_cursor")
    assert len(seen) == formation.CANDIDATE_CAP
    newest = max(formation.live_candidates(fixture.trw_dir), key=lambda c: (c.announced_at, c.candidate_id))
    assert newest.candidate_id in seen
    admitted = formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"impl-2": {"admitted_candidate": newest.candidate_id}},
        trw_dir=fixture.trw_dir,
    )
    assert admitted.member("impl-2").admitted_candidate == newest.candidate_id
    assert (
        len(json.dumps(discovered, separators=(",", ":"), ensure_ascii=False).encode())
        <= config.comms_response_max_bytes
    )


def test_terminal_orchestrator_cannot_see_private_discovery(
    bootstrap_scene, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = formation_env
    _lead_formation(fixture)
    formation.announce_candidate(
        fixture.trw_dir,
        pin_key="candidate-pin",
        run_path=fixture.member_runs["impl-2"],
        worktree=None,
        client="codex",
        ttl_seconds=600,
    )
    formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"lead": {"status": "abandoned", "note": "operator ended slot"}},
        trw_dir=fixture.trw_dir,
    )
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    discovered = _peers(bootstrap_scene, "discover")
    assert discovered["status"] == "ok"
    assert "candidates" not in discovered
    assert "lead_pending" not in discovered


def test_terminal_shared_lead_cannot_fall_back_to_eligible_own_root(
    bootstrap_scene, formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shared worktree record selects one authority root, not the first eligible one."""
    from trw_mcp.comms import _bootstrap, _identity
    from trw_mcp.comms._identity import CallerBinding, CallerSnapshot
    from trw_mcp.formation import CoordinationRoot, WorktreeRecord

    fixture = formation_env
    _lead_formation(fixture)
    formation.announce_candidate(
        fixture.trw_dir,
        pin_key="candidate-pin",
        run_path=fixture.member_runs["impl-2"],
        worktree=None,
        client="codex",
        ttl_seconds=600,
    )
    formation.revise(
        "release-train",
        fixture.orchestrator_run,
        {"lead": {"status": "abandoned", "note": "terminal"}},
        trw_dir=fixture.trw_dir,
    )
    shared = CoordinationRoot(fixture.project_root, fixture.trw_dir)
    own = CoordinationRoot(fixture.project_root / "linked", fixture.project_root / "linked" / ".trw")
    record = WorktreeRecord(str(own.project_root), "release-train", "lead", 2, str(fixture.orchestrator_run), 1)
    binding = CallerBinding(
        "a" * 32,
        "release-train",
        "lead",
        "pin-orch",
        fixture.orchestrator_run,
        fixture.manifest_path(),
        True,
    )
    shared_terminal = CallerSnapshot(binding, "abandoned", False, ())
    own_eligible = CallerSnapshot(binding, "joined", False, ())
    visits: list[Path] = []

    def snapshot(_ctx, *, trw_dir: Path, project_root: Path) -> CallerSnapshot:
        visits.append(project_root)
        return shared_terminal if trw_dir == shared.trw_dir else own_eligible

    monkeypatch.setattr(_identity, "shared_authority_root", lambda _own: (shared, record))
    monkeypatch.setattr(_identity, "resolve_snapshot", snapshot)
    monkeypatch.setattr(_bootstrap, "own_root", lambda: own)
    monkeypatch.setattr(_bootstrap, "bootstrap_root", lambda: shared)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-orch")
    discovered = _peers(bootstrap_scene, "discover")
    assert discovered["status"] == "ok"
    assert "candidates" not in discovered and "lead_pending" not in discovered
    assert visits == [shared.project_root], "own-root eligible binding must never be consulted"
