"""CORE274 factual preparation/ACK records, byte packing and transaction rollback."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.conftest import core
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms._envelope import canonical_bytes


@pytest.mark.parametrize(
    "scene",
    [
        {"comms_body_max_bytes": 1024, "comms_response_max_bytes": 10240},
        {"comms_body_max_bytes": 43008, "comms_response_max_bytes": 262144},
    ],
    indirect=True,
)
async def test_escaped_byte_pages_prepare_only_included_and_lost_response_replays(transport_scene: SendScene) -> None:
    s = transport_scene
    expected = []
    async with Client(s.server) as client:
        for index in range(3):
            sent = await invoke(
                client,
                "trw_send",
                recipient_member_id="impl-2",
                request_key=str(index),
                body="\x00" * s.config.comms_body_max_bytes,
            )
            assert sent["status"] == "ok"
            expected.append({**sent["receipt"], "body": "\x00" * s.config.comms_body_max_bytes})
        s.actor("impl-2")
        first = await invoke(client, "trw_inbox")  # simulate consumer losing this result
        assert first["items"] == expected[:1]
        assert first["next_cursor"] is not None
        assert len(canonical_bytes(first)) <= s.config.comms_response_max_bytes
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(1,)]
        facts = s.rows("SELECT * FROM milestones WHERE fact='fetch_prepared'")
        assert await invoke(client, "trw_inbox") == first
        assert s.rows("SELECT * FROM milestones WHERE fact='fetch_prepared'") == facts
        assert s.rows("SELECT COUNT(*) FROM admissions WHERE state='pending'") == [(3,)]
        second = await invoke(client, "trw_inbox", cursor=first["next_cursor"])
        third = await invoke(client, "trw_inbox", cursor=second["next_cursor"])
        assert second["items"] == expected[1:2]
        assert third["items"] == expected[2:3]
        assert "next_cursor" not in third
        assert (
            len(canonical_bytes(second)) <= s.config.comms_response_max_bytes
            and len(canonical_bytes(third)) <= s.config.comms_response_max_bytes
        )
        assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='fetch_prepared'") == [(3,)]


async def test_fetch_policy_compatibility_checked_even_when_empty(transport_scene: SendScene) -> None:
    s = transport_scene
    stored = s.config.comms_body_max_bytes
    s.config.comms_body_max_bytes = 1
    s.config.comms_response_max_bytes = 6 * stored + 4095
    s.actor("impl-2")
    async with Client(s.server) as client:
        assert (await invoke(client, "trw_inbox"))["reason"] == "response_body_policy_incompatible"
        assert (await invoke(client, "trw_inbox", action="status"))["items"] == []
        s.config.comms_response_max_bytes += 1
        assert core(await invoke(client, "trw_inbox")) == {
            "status": "ok",
            "items": [],
        }


@pytest.mark.parametrize(
    "failure_prefix", ["INSERT OR IGNORE INTO milestones", "INSERT INTO milestones", "UPDATE admissions"]
)
async def test_exception_after_each_fetch_or_ack_write_rolls_back(
    transport_scene: SendScene, failure_prefix: str
) -> None:
    from trw_mcp.comms import _store

    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="a", body="payload")
        s.actor("impl-2")
        before = {
            table: s.rows(f"SELECT * FROM {table}")
            for table in ("groups", "admissions", "milestones", "refusal_counts")
        }
        original = _store.sqlite3.connect

        class Broken(_store.sqlite3.Connection):
            def execute(self, sql: str, parameters: Any = ()) -> Any:
                result = super().execute(sql, parameters)
                if sql.startswith(failure_prefix):
                    raise _store.sqlite3.OperationalError("synthetic after-write failure")
                return result

        with s.monkeypatch.context() as local:
            local.setattr(_store.sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Broken))
            args = (
                {}
                if failure_prefix.startswith("INSERT OR IGNORE")
                else {"action": "ack", "message_ids": [sent["receipt"]["message_id"]]}
            )
            assert (await invoke(client, "trw_inbox", **args))["reason"] == "storage_corrupt"
        assert {table: s.rows(f"SELECT * FROM {table}") for table in before} == before


@pytest.mark.parametrize("action", ["status", "ack"])
async def test_actual_small_response_bound_refuses_before_ack_mutation(transport_scene: SendScene, action: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="a", body="payload")
        s.actor("impl-2")
        s.config.comms_response_max_bytes = 20  # process-local invalid-config control of the runtime bound
        args = {"message_ids": [sent["receipt"]["message_id"]]} if action == "ack" else {}
        assert (await invoke(client, "trw_inbox", action=action, **args))["reason"] == "response_too_small"
        assert s.rows("SELECT state FROM admissions") == [("pending",)]


@pytest.mark.parametrize("scene", [{"comms_body_max_bytes": 1024, "comms_response_max_bytes": 10240}], indirect=True)
async def test_process_local_byte_guard_control_demonstrates_oversized_page(transport_scene: SendScene) -> None:
    from trw_mcp.comms import _paging

    s = transport_scene
    async with Client(s.server) as client:
        for index in range(3):
            await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=str(index), body="\x00" * 1024)
        s.actor("impl-2")
        s.monkeypatch.setattr(_paging, "fits", lambda payload, max_bytes: True)
        result = await invoke(client, "trw_inbox")
        assert len(result["items"]) == 3
        assert len(canonical_bytes(result)) > s.config.comms_response_max_bytes
