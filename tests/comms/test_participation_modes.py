"""CORE274 fresh membership versus live receiver authority and scoped traversal."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client, FastMCP

from tests._formation_test_support import FormationFixture, formation_env, make_run_dir, open_slot  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms, joined_member
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401


async def test_fetch_ack_renew_a_lapsed_lease_status_needs_no_endpoint(transport_scene: SendScene) -> None:
    """PRD-CORE-274 FR12: a lapsed lease is advisory; the member's own fetch renews it."""
    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="a", body="hi")
        assert (await invoke(client, "trw_inbox"))["reason"] == "no_endpoint_for_member"
        assert len((await invoke(client, "trw_inbox", action="status"))["items"]) == 1
        s.actor("impl-2")
        s.rows("UPDATE groups SET group_time=(SELECT lease_expires_at FROM endpoints LIMIT 1)")
        lapsed = s.rows("SELECT lease_expires_at FROM endpoints WHERE member_id='impl-2'")[0][0]
        assert len((await invoke(client, "trw_inbox"))["items"]) == 1
        assert s.rows("SELECT lease_expires_at FROM endpoints WHERE member_id='impl-2'")[0][0] > lapsed
        acked = await invoke(client, "trw_inbox", action="ack", message_ids=[sent["receipt"]["message_id"]])
        assert acked["status"] == "ok"
        assert (await invoke(client, "trw_peers", action="heartbeat"))["status"] == "ok"
        assert (await invoke(client, "trw_inbox"))["items"] == []


@pytest.mark.parametrize("scene", [{"comms_fetch_max_items": 1}], indirect=True)
async def test_old_incarnation_cursors_refuse_and_the_queue_survives_replacement(transport_scene: SendScene) -> None:
    """PRD-CORE-274 FR12/FR13: a replacement fences the old process and invalidates its cursors,
    but the member's queue survives and the new generation may ACK the member's rows."""
    from trw_mcp.comms import _endpoints

    s = transport_scene
    async with Client(s.server) as client:
        sent = []
        for key in ("acked", "pending", "later"):
            sent.append(
                (await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=key, body=key))["receipt"]
            )
        s.actor("impl-2")
        await invoke(client, "trw_inbox", action="ack", message_ids=[sent[0]["message_id"]])
        cursor = (await invoke(client, "trw_inbox"))["next_cursor"]
        assert cursor
        assert (await invoke(client, "trw_inbox", action="status", cursor=cursor))["reason"] == "invalid_cursor"
        old = dict(_endpoints._PROCESS_INCARNATIONS)
        s.rows("UPDATE groups SET group_time=group_time+1000")
        _endpoints._reset_process_incarnations_for_test()
        assert (await invoke(client, "trw_peers", action="enroll"))["status"] == "ok"
        assert (await invoke(client, "trw_inbox", cursor=cursor))["reason"] == "invalid_cursor"
        acked_again = await invoke(client, "trw_inbox", action="ack", message_ids=[sent[0]["message_id"]])
        assert acked_again["status"] == "ok", "a repeated ACK of the member's row is idempotent"
        assert [item["message_id"] for item in (await invoke(client, "trw_inbox"))["items"]] == [sent[1]["message_id"]]
        new = dict(_endpoints._PROCESS_INCARNATIONS)
        _endpoints._PROCESS_INCARNATIONS.clear()
        _endpoints._PROCESS_INCARNATIONS.update(old)
        assert (await invoke(client, "trw_inbox"))["reason"] == "endpoint_replaced_by_newer_incarnation"
        assert (await invoke(client, "trw_inbox", action="status"))["status"] == "ok", "status is not fenced"
        _endpoints._reset_process_incarnations_for_test()
        _endpoints._PROCESS_INCARNATIONS.update(new)
        assert s.rows("SELECT charge FROM groups") == [(3,)]
        assert s.rows("SELECT state FROM admissions ORDER BY rowid") == [("acked",), ("pending",), ("pending",)]


async def test_terminal_closure_precedes_bad_args_and_status_only_after_eligibility_restored(
    transport_scene: SendScene,
) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="q", body="hi")
        from trw_mcp import formation

        formation.revise(
            "release-train",
            s.formation.orchestrator_run,
            {member: {"status": "abandoned"} for member in s.formation.member_runs},
            trw_dir=s.formation.trw_dir,
        )
        result = await invoke(client, "trw_inbox", action="ack", message_ids=[], cursor="bad")
        assert result["reason"] == "group_closed"
        assert s.rows("SELECT closed FROM groups") == [(1,)]
        formation.revise(
            "release-train", s.formation.orchestrator_run, {"impl-1": {"status": "active"}}, trw_dir=s.formation.trw_dir
        )
        status = await invoke(client, "trw_inbox", action="status")
        assert status["items"][0]["message_id"] == sent["receipt"]["message_id"]
        assert "body" not in status["items"][0]
        assert (await invoke(client, "trw_inbox"))["reason"] == "group_closed"


@pytest.fixture
def three_scene(formation_env: FormationFixture, comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> SendScene:
    from trw_mcp import formation

    f = formation_env
    f.member_runs["impl-3"] = make_run_dir(f.trw_dir / "runs", "impl-3")
    payload = f.payload()
    payload["members"].append(open_slot("impl-3"))
    formation.create(f.orchestrator_run, payload, trw_dir=f.trw_dir)
    config = enable_comms(monkeypatch)
    for member, pin in (("impl-1", "pin-a"), ("impl-2", "pin-b"), ("impl-3", "pin-c")):
        joined_member(f, member, pin)
        monkeypatch.setenv("TRW_SESSION_ID", pin)
        assert call_peers(comms_server, "enroll")["status"] == "ok"
    return SendScene(f, comms_server, config, monkeypatch)


@pytest.mark.parametrize("disable_scope", [False, True])
@pytest.mark.parametrize("action", ["fetch", "status"])
async def test_status_caller_scope_and_process_local_filter_control(
    three_scene: SendScene, disable_scope: bool, action: str
) -> None:
    from trw_mcp.comms import _inbox_page

    s = three_scene
    async with Client(s.server) as client:
        s.actor("impl-2")
        await invoke(client, "trw_send", recipient_member_id="impl-3", request_key="private", body="not-for-one")
        s.actor("impl-1")
        await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="mine", body="mine")
        if disable_scope:

            def unscoped(conn: Any, binding: Any, action: Any, incarnation: Any, after: Any, limit: Any) -> Any:
                return list(
                    conn.execute(
                        "SELECT rowid AS append_id,* FROM admissions WHERE group_id=? ORDER BY rowid",
                        (binding.group_id,),
                    )
                )

            s.monkeypatch.setattr(_inbox_page, "_read_rows", unscoped)
        result = await invoke(client, "trw_inbox", action=action)
        assert len(result["items"]) == (2 if disable_scope else (1 if action == "status" else 0))
        if action == "status":
            assert all("body" not in item for item in result["items"])
        if not disable_scope and action == "status":
            assert result["items"][0]["sender_member_id"] == "impl-1"


@pytest.mark.parametrize("scene", [{"comms_fetch_max_items": 1}], indirect=True)
async def test_status_cursor_survives_actual_process_restart_without_enrollment(transport_scene: SendScene) -> None:
    import asyncio
    import json
    import os
    import subprocess
    import sys
    from pathlib import Path

    s = transport_scene
    async with Client(s.server) as client:
        receipts = []
        for key in ("one", "two"):
            receipts.append(
                (await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=key, body=key))["receipt"]
            )
        first = await invoke(client, "trw_inbox", action="status")
        assert first["next_cursor"]
    worker = """
import asyncio,json,sys
from fastmcp import Client,FastMCP
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools
server=FastMCP('restart-status');register_swarm_comms_tools(server)
async def main():
    async with Client(server) as client:
        result=await client.call_tool('trw_inbox',{'action':'status','cursor':sys.argv[1]})
        print('RESULT='+json.dumps(result.structured_content),flush=True)
asyncio.run(main())
"""
    repo = Path(__file__).resolve().parents[3]
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(s.formation.project_root),
        "PYTHONPATH": os.pathsep.join((str(repo / "trw-mcp/src"), str(repo / "trw-memory/src"))),
        "TRW_PROJECT_ROOT": str(s.formation.project_root),
        "TRW_SESSION_ID": "pin-a",
        "TRW_COMMS_ENABLED": "true",
        "TRW_CTX_ISOLATION_ENABLED": "true",
        "TRW_AUTO_UPGRADE": "false",
        "TRW_CLEANUP_ON_BOOT": "false",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    child = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", worker, first["next_cursor"]],
        env=env,
        cwd=s.formation.project_root,
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert child.returncode == 0, child.stderr
    frames = [line.removeprefix("RESULT=") for line in child.stdout.splitlines() if line.startswith("RESULT=")]
    assert len(frames) == 1, child.stdout
    result = json.loads(frames[0])
    assert result["items"][0]["message_id"] == receipts[1]["message_id"]
    assert "next_cursor" not in result
    assert s.rows("SELECT COUNT(*) FROM endpoints WHERE member_id='impl-1'") == [(0,)]
