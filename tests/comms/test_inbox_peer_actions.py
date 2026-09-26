"""PRD-CORE-300 FR10: trw_inbox's peer actions replay trw_peers exactly.

``trw_peers`` was removed (TOOL-CUT-DEPRECATION-MAP S8); all seven peer
actions are reachable through ``trw_inbox``'s ``action`` parameter instead.
``fixtures/trw_peers_golden.json`` holds the responses the pre-cut
``trw_peers`` tool returned for this exact action sequence and formation
state, recorded at 1b94ddc0c by running that commit's tool under the same
world and frozen clock. This test replays the sequence through
``trw_inbox`` and compares, so it needs no git history.

Per-call random fields (``candidate_id``, the ``next_cursor`` that encodes
it, ``pause_id``), the environment-derived ``client`` label, the two
wall-clock-derived timers and the tmp root are normalized on both sides.
Everything else -- status, reason, shape, guidance, peer lists -- must match
exactly.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import make_run_dir, open_slot
from trw_mcp import formation
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import pin_active_run
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools

_GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "trw_peers_golden.json"
_FIXED_TIME = 1_700_000_000.0
_PLACEHOLDERS = {
    "candidate_id": "<id>",
    "next_cursor": "<cursor>",
    "pause_id": "<pause>",
    "client": "<client>",
    "last_seen_seconds_ago": "<t>",
    "lease_expires_in_seconds": "<t>",
}


def _normalize(node: object) -> object:
    """Replace the per-call-random and environment-derived fields with placeholders."""
    if isinstance(node, dict):
        return {k: _PLACEHOLDERS[k] if k in _PLACEHOLDERS and v is not None else _normalize(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    return node


@dataclass
class World:
    root: Path
    server: FastMCP
    monkeypatch: pytest.MonkeyPatch

    def at(self, pin: str) -> None:
        from trw_mcp.state import _paths, _pin_store

        trw_dir = self.root / ".trw"
        self.monkeypatch.setattr(_paths, "resolve_project_root", lambda: self.root)
        self.monkeypatch.setattr(_paths, "resolve_trw_dir", lambda: trw_dir)
        self.monkeypatch.setattr(_pin_store, "pin_store_path", lambda: trw_dir / "runtime" / "pins.json")
        self.monkeypatch.setenv("TRW_SESSION_ID", pin)
        _pin_store.invalidate_pin_store_cache()

    def call(self, tool: str, action: str, **kw: Any) -> dict[str, Any]:
        result = asyncio.run(self.server.call_tool(tool, {"action": action, **kw}))
        payload = result.structured_content
        assert isinstance(payload, dict), f"{tool}({action}) did not return a structured payload"
        return payload


def _build_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, register: Any, name: str) -> World:
    server = FastMCP(f"trw-peers-replay-{name}")
    register(server)
    root = tmp_path / name
    trw_dir = root / ".trw"
    runs_root = trw_dir / "runs"
    runs_root.mkdir(parents=True)
    orchestrator = make_run_dir(runs_root, "lead")
    member = make_run_dir(runs_root, "impl-2")
    candidate = make_run_dir(runs_root, "candidate")
    world = World(root, server, monkeypatch)

    formation.create(
        orchestrator,
        {"formation_id": "replay", "members": [open_slot("lead", "codex"), open_slot("impl-2")]},
        trw_dir=trw_dir,
        prds_dir=root / "prds",
    )
    for member_id, run in (("lead", orchestrator), ("impl-2", member)):
        world.at(f"pin-{name}-{member_id}")
        context = build_call_context(None)
        formation.join("replay", member_id, run, pin_key=context.session_id, trw_dir=trw_dir)
        pin_active_run(run, context=context)
    world.at(f"pin-{name}-candidate")
    pin_active_run(candidate, context=build_call_context(None))
    return world


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single frozen instant so lease/age timers are comparable across worlds."""
    monkeypatch.setattr("trw_mcp.comms._store.time.time", lambda: _FIXED_TIME)
    monkeypatch.setattr("trw_mcp.comms._bootstrap.time.time", lambda: _FIXED_TIME)


def test_seven_peer_actions_replay_the_recorded_trw_peers_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.comms.conftest import enable_comms

    enable_comms(monkeypatch, comms_candidate_ttl_seconds=300)
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert {step["action"] for step in golden} == {
        "enroll", "list", "heartbeat", "announce", "withdraw", "discover", "ack_pause"
    }  # fmt: skip
    world = _build_world(tmp_path, monkeypatch, register_swarm_comms_tools, "old")
    root = str(world.root)

    for step in golden:
        kw: dict[str, Any] = {}
        if step["action"] == "ack_pause":
            # The pause record is native (not a tool call); its id is random per run.
            record = formation.pause("replay", world.root / ".trw" / "runs" / "lead", "release cut",
                                     trw_dir=world.root / ".trw")  # fmt: skip
            kw["pause_id"] = record.pause_id
        world.at(f"pin-old-{step['member']}")
        payload = world.call("trw_inbox", step["action"], **kw)
        actual = json.loads(json.dumps(_normalize(payload)).replace(root, "<root>"))
        assert actual == step["response"], f"action={step['action']!r} member={step['member']!r}"


def test_trw_inbox_accepts_exactly_the_inbox_and_peer_actions() -> None:
    """The combined Literal is written out for the schema; it must not drift from its sources."""
    from typing import get_args

    from trw_mcp.comms import InboxAction, PeerAction
    from trw_mcp.tools.swarm_comms import InboxOrPeerAction

    assert set(get_args(InboxOrPeerAction)) == set(get_args(InboxAction)) | set(get_args(PeerAction))
