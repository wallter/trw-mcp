"""Independent FR01/02/03/07 public sends using native formations and pins.

Synthetic in-process FastMCP clients; not native harness or crash proof.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import open_slot
from tests.comms.conftest import core
from trw_mcp import formation
from trw_mcp.models import config as config_module
from trw_mcp.models.config import TRWConfig
from trw_mcp.state import _paths
from trw_mcp.state._call_context import build_call_context
from trw_mcp.state._paths import pin_active_run
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools


@dataclass
class SendScene:
    root: Path
    owner: Path
    server: FastMCP
    config: TRWConfig
    monkeypatch: pytest.MonkeyPatch

    def call(self, member: str, name: str, **arguments: Any) -> dict[str, Any]:
        self.monkeypatch.setenv("TRW_SESSION_ID", "send-contract-" + member)
        result = asyncio.run(self.server.call_tool(name, arguments))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def send(self, key: str, body: str = "payload") -> dict[str, Any]:
        return self.call("sender", "trw_send", recipient_member_id="receiver", request_key=key, body=body)


@pytest.fixture
def send_scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SendScene:
    from tests import _path_isolation

    root = tmp_path / "project"
    _path_isolation.set_current_root(root)
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(root))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = TRWConfig(
        comms_enabled=True,
        cleanup_on_boot=False,
        comms_group_row_limit=2,
        comms_body_max_bytes=8,
        comms_recipient_outstanding_limit=2,
        comms_sender_admissions_per_minute=2,
    )
    monkeypatch.setattr(config_module, "get_config", lambda: config)
    monkeypatch.setattr(_paths, "get_config", lambda: config)
    members = ("sender", "receiver")
    runs = {member: root / ".trw" / "runs" / member for member in members}
    for run in runs.values():
        (run / "meta").mkdir(parents=True)
        (run / "meta" / "run.yaml").write_text("run_id: send-contract\nstatus: active\n")
    formation.create(
        runs["sender"],
        {
            "formation_id": "send-contract",
            "members": [open_slot(m) for m in members],
        },
        trw_dir=root / ".trw",
        prds_dir=root / "prds",
    )
    for member in members:
        monkeypatch.setenv("TRW_SESSION_ID", "send-contract-" + member)
        context = build_call_context(None)
        formation.join("send-contract", member, runs[member], pin_key=context.session_id, trw_dir=root / ".trw")
        pin_active_run(runs[member], context=context)
    server = FastMCP("independent-send-contract")
    register_swarm_comms_tools(server)
    scene = SendScene(root, runs["sender"], server, config, monkeypatch)
    # Group policy must be born HERE, not later when the sender first posts.
    assert scene.call("receiver", "trw_peers", action="enroll")["status"] == "ok"
    return scene


def test_unenrolled_sender_receipt_is_stable_after_recipient_binding_changes(send_scene: SendScene) -> None:
    first = send_scene.send("stable")
    assert first["status"] == "ok"
    receipt = first["receipt"]
    assert set(receipt) == {
        "message_id",
        "sender_member_id",
        "recipient_member_id",
        "kind",
        "delivery_class",
        "admitted_at",
    }
    assert receipt["sender_member_id"] == "sender"
    assert receipt["recipient_member_id"] == "receiver"
    assert "delivery" not in first
    # Native authority changes while the old receiver endpoint is still leased.
    formation.revise(
        "send-contract",
        send_scene.owner,
        {"receiver": {"pin_key": "new-receiver-pin"}},
        trw_dir=send_scene.root / ".trw",
    )
    assert send_scene.send("stable")["receipt"] == receipt
    changed = send_scene.send("stable", "altered")
    assert changed["status"] == "refused"
    # PRD-CORE-274 FR13: the member is addressed, not its (now stale) endpoint binding.
    assert send_scene.send("new-key")["status"] == "ok"
    peers = send_scene.call("sender", "trw_peers", action="list")
    assert {peer["member_id"] for peer in peers["peers"]} == {"receiver"}, "send implicitly enrolled sender"


def test_enrollment_birth_policy_survives_all_admission_config_changes(send_scene: SendScene) -> None:
    send_scene.config.comms_group_row_limit = 1
    send_scene.config.comms_body_max_bytes = 1
    send_scene.config.comms_recipient_outstanding_limit = 1
    send_scene.config.comms_sender_admissions_per_minute = 1
    first = send_scene.send("first", "12345678")
    second = send_scene.send("second", "abcdefgh")
    assert first["status"] == second["status"] == "ok"
    assert first["receipt"]["message_id"] != second["receipt"]["message_id"]
    assert send_scene.send("third", "z")["status"] == "refused"
    assert core(send_scene.send("first", "12345678")) == core(first)


def test_terminal_send_observation_closes_before_byte_refusal_and_retains_receipt(send_scene: SendScene) -> None:
    first = send_scene.send("retained")
    assert first["status"] == "ok"
    formation.revise(
        "send-contract",
        send_scene.owner,
        {"sender": {"status": "abandoned"}, "receiver": {"status": "abandoned"}},
        trw_dir=send_scene.root / ".trw",
    )
    # Semantic payload validation must not skip a trusted terminal observation.
    terminal = send_scene.send("oversized", "x" * 65537)
    assert terminal["status"] == "refused"
    formation.revise(
        "send-contract", send_scene.owner, {"sender": {"status": "active"}}, trw_dir=send_scene.root / ".trw"
    )
    retried = send_scene.send("retained")
    assert retried["receipt"] == first["receipt"], "eligible sender cannot reconcile a retained closed-group receipt"
    assert retried["message_state"] == "expired", "FR15: the retry reports the message's current state"
    assert send_scene.send("new")["reason"] == "group_closed", "terminal observation was not committed"


def test_rebound_pin_takes_over_with_a_new_incarnation_never_the_old_one(send_scene: SendScene) -> None:
    """PRD-CORE-274 FR12: an orchestrator-authorized rebind takes over at once, as a NEW
    incarnation and generation -- never by borrowing the old process's token."""
    first = send_scene.send("before-rebind")
    assert first["status"] == "ok"
    receiver_run = send_scene.owner.parent / "receiver"
    formation.revise(
        "send-contract",
        send_scene.owner,
        {"receiver": {"pin_key": "replacement-session"}},
        trw_dir=send_scene.root / ".trw",
    )
    send_scene.monkeypatch.setenv("TRW_SESSION_ID", "replacement-session")
    pin_active_run(receiver_run, context=build_call_context(None))
    path = next(send_scene.root.rglob("comms.sqlite3"))
    before = send_scene_rows(path, "SELECT incarnation, generation FROM endpoints WHERE member_id='receiver'")
    result = asyncio.run(send_scene.server.call_tool("trw_peers", {"action": "enroll"})).structured_content
    assert result is not None
    assert result["status"] == "ok", result
    after = send_scene_rows(path, "SELECT incarnation, generation FROM endpoints WHERE member_id='receiver'")
    assert after[0][0] != before[0][0] and after[0][1] == before[0][1] + 1


def test_large_finite_clock_does_not_empty_same_timestamp_rate_window(send_scene: SendScene) -> None:
    from trw_mcp.comms import _store

    assert send_scene.send("first")["status"] == "ok"
    path = next(send_scene.root.rglob("comms.sqlite3"))
    now = 1e20
    assert now - 60 == now, "fixture must exercise subtraction precision loss"
    # Coherent synthetic persisted history, not a claim about present UTC time.
    with _store.sqlite3.connect(path) as connection:
        connection.execute("UPDATE groups SET group_time=?, rate_limit=1", (now,))
        connection.execute("UPDATE admissions SET admitted_at=?, expires_at=?", (now, now + 86400))
        connection.execute("UPDATE milestones SET at=?", (now,))
        connection.execute("UPDATE endpoints SET last_seen_at=?, lease_expires_at=?", (now, now + 16384))
    with _store.connect(formation.manifest_path_for_run(send_scene.owner), busy_timeout_ms=20):
        pass
    result = send_scene.send("second")
    assert result["status"] == "refused", "same-time prior admission vanished from the 60-second window"
    assert result["reason"] == "sender_rate_limit"


def send_scene_rows(path: object, sql: str) -> list[tuple[object, ...]]:
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        return [tuple(row) for row in conn.execute(sql)]
    finally:
        conn.close()
