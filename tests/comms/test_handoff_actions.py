"""PRD-CORE-322 FR02-FR04 and NFR02: the handoff write actions, one direct test per arm.

Everything runs through the real ``trw_inbox`` tool dispatch. Each refusal arm
asserts the closed reason, that it was COUNTED like every other comms refusal,
and that no fact, pointer or state changed.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest
from fastmcp import Client, FastMCP

from tests._formation_test_support import (  # noqa: F401
    FormationFixture,
    formation_env,
    make_run_dir,
    open_slot,
    write_pin,
)
from tests.comms.conftest import call_peers, core, enable_comms
from tests.comms.test_dead_peer import advance_group_clock, other_process
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.formation import create, join
from trw_mcp.middleware.response_optimizer import ResponseOptimizerMiddleware

LEDGER = ("SELECT message_id,fact,at FROM milestones", "SELECT * FROM handoff_reports", "SELECT state FROM admissions")


def ledger(s: SendScene) -> list[list[tuple[Any, ...]]]:
    return [sorted(s.rows(sql)) for sql in LEDGER]


async def request(client: Client[Any], s: SendScene, key: str = "h", kind: str = "request") -> str:
    """impl-1 sends *kind* to impl-2; the scene is left acting as impl-2 (the recipient)."""
    s.actor("impl-1")
    sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=key, body="task", kind=kind)
    s.actor("impl-2")
    return str(sent["receipt"]["message_id"])


async def step(client: Client[Any], action: str, message_id: str, **args: Any) -> dict[str, Any]:
    return await invoke(client, "trw_inbox", action=action, message_ids=[message_id], **args)


async def assert_refused(
    client: Client[Any], s: SendScene, reason: str, action: str, ids: list[str], **args: Any
) -> None:
    before = ledger(s)
    refused = await invoke(client, "trw_inbox", action=action, message_ids=ids, **args)
    assert (refused["status"], refused["reason"]) == ("refused", reason)
    assert ledger(s) == before, "a refused handoff action wrote something"
    assert s.rows("SELECT count FROM refusal_counts WHERE reason=?", (reason,)) == [(1,)]


async def enrolled_as(client: Client[Any], s: SendScene, member: str) -> None:
    s.actor(member)
    assert (await invoke(client, "trw_inbox", action="enroll"))["status"] == "ok"


def facts(s: SendScene, message_id: str) -> dict[str, float]:
    return dict(s.rows("SELECT fact,at FROM milestones WHERE message_id=?", (message_id,)))


# --- accept (FR02) ---------------------------------------------------------------


async def test_accept_on_acked_request_adds_accepted_and_keeps_acked(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        await step(client, "ack", message_id)
        acked = facts(s, message_id)["acked"]
        assert core(await step(client, "accept", message_id)) == {"status": "ok", "accepted_ids": [message_id]}
        assert facts(s, message_id)["acked"] == acked
        status = await invoke(client, "trw_inbox", action="status")
        assert set(status["items"][0]["milestones"]) == {"admitted", "acked", "accepted"}


async def test_accept_on_pending_request_records_acked_then_accepted(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        assert (await step(client, "accept", message_id))["status"] == "ok"
        recorded = facts(s, message_id)
        assert {"acked", "accepted"} <= set(recorded) and recorded["acked"] <= recorded["accepted"]
        assert s.rows("SELECT state FROM admissions") == [("acked",)]


async def test_accept_refuses_the_sender(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        await enrolled_as(client, s, "impl-1")  # past the receiver fence, so the ROLE rule is what refuses
        await assert_refused(client, s, "handoff_not_authorized", "accept", [message_id])


@pytest.mark.parametrize("kind", ["reply", "status"])
async def test_accept_refuses_a_message_that_is_not_a_request(transport_scene: SendScene, kind: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s, kind=kind)
        await assert_refused(client, s, "not_a_handoff", "accept", [message_id])


async def test_accept_refuses_the_whole_batch_when_one_row_is_not_a_request(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        good = await request(client, s, key="a")
        reply = await request(client, s, key="b", kind="reply")
        await assert_refused(client, s, "not_a_handoff", "accept", [good, reply])
        assert "accepted" not in facts(s, good)


async def test_accept_refuses_an_expired_request(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        advance_group_clock(s.formation, s.config.comms_message_ttl_seconds + 1)
        await invoke(client, "trw_inbox", action="status")  # the lazy expiry runs in its own call
        await assert_refused(client, s, "handoff_not_authorized", "accept", [message_id])


async def test_duplicate_accept_is_a_no_op(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        first = await step(client, "accept", message_id)
        before = ledger(s)
        assert core(await step(client, "accept", message_id)) == core(first)
        assert ledger(s) == before


async def test_accept_from_a_displaced_recipient_is_refused(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        with other_process():
            assert (await invoke(client, "trw_inbox", action="enroll"))["status"] == "ok"
        before = ledger(s)
        refused = await step(client, "accept", message_id)
        assert refused["reason"] == "endpoint_replaced_by_newer_incarnation"
        assert ledger(s) == before


# --- report (FR03, NFR02) ---------------------------------------------------------


async def accepted(client: Client[Any], s: SendScene) -> str:
    message_id = await request(client, s)
    assert (await step(client, "accept", message_id))["status"] == "ok"
    return message_id


async def test_report_stores_the_pointer_with_the_reported_fact_time(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        reported = await step(client, "report", message_id, next_read="worker-3/core-322-s2@76a2010")
        assert core(reported) == {"status": "ok", "reported_ids": [message_id]}
        [(pointer, at)] = s.rows("SELECT next_read,reported_at FROM handoff_reports")
        assert (pointer, at) == ("worker-3/core-322-s2@76a2010", facts(s, message_id)["reported"])


async def test_report_refuses_the_sender(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        await enrolled_as(client, s, "impl-1")
        await assert_refused(client, s, "handoff_not_authorized", "report", [message_id], next_read="x")


async def test_report_before_acceptance_is_refused(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        await step(client, "ack", message_id)
        await assert_refused(client, s, "handoff_not_accepted", "report", [message_id], next_read="x")


@pytest.mark.parametrize(
    "pointer",
    [
        "",
        "x" * 513,
        "\u00e9" * 256 + "x",
        "run\npath",
        "tab\there",
        "a\x00b",
        "safe\u202eevil",
        "zero\u200bwidth",
        "wt@76a2010\u2028completion: verified",
    ],
    ids=["empty", "513-ascii", "513-multibyte", "newline", "tab", "nul", "bidi", "zero-width", "line-separator"],
)
async def test_report_refuses_an_invalid_pointer(transport_scene: SendScene, pointer: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        await assert_refused(client, s, "invalid_next_read", "report", [message_id], next_read=pointer)


@pytest.mark.parametrize("pointer", ["x", "\u00e9" * 256, "docs/requirements-aare-f/prds/PRD-CORE-322.md"])
async def test_report_accepts_a_pointer_at_the_bounds(transport_scene: SendScene, pointer: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        assert (await step(client, "report", message_id, next_read=pointer))["status"] == "ok"
        assert s.rows("SELECT next_read FROM handoff_reports") == [(pointer,)]


async def test_duplicate_report_is_a_no_op_and_a_changed_pointer_is_refused(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        first = await step(client, "report", message_id, next_read="a@1")
        before = ledger(s)
        assert core(await step(client, "report", message_id, next_read="a@1")) == core(first)
        assert ledger(s) == before
        await assert_refused(client, s, "handoff_already_reported", "report", [message_id], next_read="a@2")


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("report", {}),  # report needs next_read
        ("accept", {"next_read": "x"}),  # next_read is valid only with report
        ("complete", {"next_read": "x"}),
        ("ack", {"next_read": "x"}),
        ("accept", {"cursor": "c"}),
    ],
)
async def test_handoff_argument_errors_refuse_without_writes(
    transport_scene: SendScene, action: str, args: dict[str, Any]
) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        await assert_refused(client, s, "invalid_inbox_arguments", action, [message_id], **args)


async def test_report_takes_exactly_one_id(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        first = await accepted(client, s)
        second = await request(client, s, key="h2")
        await step(client, "accept", second)
        await assert_refused(client, s, "invalid_inbox_arguments", "report", [first, second], next_read="x")


# --- next_read is report-only, even for peer actions (review P2 of this PRD) -----

_NON_REPORT_ARGS: list[tuple[str, dict[str, Any]]] = [
    ("fetch", {}),
    ("status", {}),
    ("enroll", {}),
    ("list", {}),
    ("heartbeat", {}),
    ("announce", {}),
    ("withdraw", {}),
    ("discover", {}),
    ("ack_pause", {"pause_id": "p1"}),
]


@pytest.mark.parametrize(("action", "args"), _NON_REPORT_ARGS)
async def test_next_read_is_refused_before_dispatch_on_every_non_report_action(
    transport_scene: SendScene, action: str, args: dict[str, Any]
) -> None:
    """FR03: next_read is report-only. Before the fix, the peer dispatch in
    swarm_comms.py ran before the inbox argument check, so e.g.
    ``action="enroll", next_read="x"`` proceeded and silently discarded the
    pointer instead of refusing.
    """
    s = transport_scene
    async with Client(s.server) as client:
        s.actor("impl-2")
        refused = await invoke(client, "trw_inbox", action=action, next_read="wt@sha", **args)
        assert (refused["status"], refused["reason"]) == ("refused", "invalid_inbox_arguments")


@pytest.mark.parametrize(("action", "args"), _NON_REPORT_ARGS)
async def test_omitting_next_read_leaves_these_actions_unchanged(
    transport_scene: SendScene, action: str, args: dict[str, Any]
) -> None:
    """The companion negative case: without next_read, none of these actions is
    ever refused for FR03's reason — the new gate only fires when the argument
    is actually present.
    """
    s = transport_scene
    async with Client(s.server) as client:
        s.actor("impl-2")
        result = await invoke(client, "trw_inbox", action=action, **args)
        assert (result.get("status"), result.get("reason")) != ("refused", "invalid_inbox_arguments")


# --- complete (FR04) --------------------------------------------------------------


async def reported(client: Client[Any], s: SendScene) -> str:
    message_id = await accepted(client, s)
    assert (await step(client, "report", message_id, next_read="wt@sha"))["status"] == "ok"
    return message_id


async def test_complete_by_the_requester_records_completed(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported(client, s)
        s.actor("impl-1")
        assert core(await step(client, "complete", message_id)) == {"status": "ok", "completed_ids": [message_id]}
        recorded = facts(s, message_id)
        assert recorded["reported"] <= recorded["completed"]


async def test_complete_by_the_recipient_is_refused(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported(client, s)
        await assert_refused(client, s, "handoff_not_authorized", "complete", [message_id])


async def test_complete_before_a_report_is_refused(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        s.actor("impl-1")
        await assert_refused(client, s, "handoff_not_reported", "complete", [message_id])


async def test_duplicate_complete_and_a_replayed_accept_are_no_ops(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported(client, s)
        s.actor("impl-1")
        first = await step(client, "complete", message_id)
        before = ledger(s)
        assert core(await step(client, "complete", message_id)) == core(first)
        s.actor("impl-2")
        assert (await step(client, "accept", message_id))["status"] == "ok"
        assert ledger(s) == before


async def test_complete_from_a_displaced_sender_is_refused(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported(client, s)
        await enrolled_as(client, s, "impl-1")
        with other_process():
            assert (await invoke(client, "trw_inbox", action="enroll"))["status"] == "ok"
        before = ledger(s)
        refused = await step(client, "complete", message_id)
        assert refused["reason"] == "endpoint_replaced_by_newer_incarnation"
        assert ledger(s) == before


# --- adversarial probes (Q7): forgery, replay, races ------------------------------


@pytest.fixture
def trio(formation_env: FormationFixture, comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> SendScene:
    """impl-1 -> impl-2 handoffs, plus impl-3: a formation member who is party to none of them."""
    config = enable_comms(monkeypatch)
    runs = {**formation_env.member_runs, "impl-3": make_run_dir(formation_env.trw_dir / "runs", "impl-3")}
    payload = formation_env.payload(members=[open_slot(member, role="implementer") for member in runs])
    formation_id = create(formation_env.orchestrator_run, payload, trw_dir=formation_env.trw_dir).formation_id
    for member, pin in (("impl-1", "pin-a"), ("impl-2", "pin-b"), ("impl-3", "pin-c")):
        join(formation_id, member, runs[member], pin_key=pin, trw_dir=formation_env.trw_dir)
        write_pin(formation_env, pin, runs[member])
    comms_server.add_middleware(ResponseOptimizerMiddleware())
    s = SendScene(formation_env, comms_server, config, monkeypatch)
    s.actor("impl-2")
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    return s


async def test_a_third_party_cannot_accept_report_or_complete(trio: SendScene) -> None:
    s = trio
    async with Client(s.server) as client:
        message_id = await reported(client, s)
        s.monkeypatch.setenv("TRW_SESSION_ID", "pin-c")
        assert (await invoke(client, "trw_inbox", action="enroll"))["status"] == "ok"
        for action, args in (("accept", {}), ("report", {"next_read": "forged@0"}), ("complete", {})):
            before = ledger(s)
            refused = await step(client, action, message_id, **args)
            assert (refused["status"], refused["reason"]) == ("refused", "handoff_not_authorized"), action
            assert ledger(s) == before
        assert "completed" not in facts(s, message_id)


async def test_a_self_addressed_request_can_never_be_verified(transport_scene: SendScene) -> None:
    """An owner never verifies its own report, even when it is also the requester (C8)."""
    s = transport_scene
    async with Client(s.server) as client:
        s.actor("impl-2")
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key="self", body="me")
        if sent["status"] != "ok":
            pytest.skip(f"self-addressed sends are refused upstream ({sent['reason']})")  # skip-category: opt-in
        message_id = sent["receipt"]["message_id"]
        await step(client, "accept", message_id)
        await step(client, "report", message_id, next_read="mine@1")
        await assert_refused(client, s, "handoff_not_authorized", "complete", [message_id])


def test_two_racing_completes_record_one_fact(transport_scene: SendScene) -> None:
    from trw_mcp import comms

    s = transport_scene
    message_id = asyncio.run(_reported_out_of_band(s))
    s.actor("impl-1")
    gate = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def racer() -> None:
        gate.wait()
        results.append(comms.inbox("complete", [message_id]))

    threads = [threading.Thread(target=racer) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(r["status"] for r in results) in (["ok", "ok"], ["ok", "refused"]), results
    assert all(r["status"] == "ok" or r.get("retryable") for r in results), results
    assert s.rows("SELECT COUNT(*) FROM milestones WHERE fact='completed'") == [(1,)]
    assert comms.inbox("status")["status"] == "ok", "the store still verifies after the race"


async def _reported_out_of_band(s: SendScene) -> str:
    async with Client(s.server) as client:
        return await reported(client, s)


@pytest.mark.parametrize("action", ["enroll", "list", "heartbeat"])
async def test_a_pointer_on_a_peer_action_never_reaches_pickup(
    transport_scene: SendScene, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """core322-s2 r2: the refusal comes before advance_pickup, which can persist candidate
    state, join membership and write run stamps; an invalid argument must touch none of it."""
    import trw_mcp.comms as comms

    def pickup_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("advance_pickup ran for a refused next_read")

    monkeypatch.setattr(comms, "advance_pickup", pickup_must_not_run)
    s = transport_scene
    async with Client(s.server) as client:
        s.actor("impl-2")
        refused = await invoke(client, "trw_inbox", action=action, next_read="wt@sha")
        assert (refused["status"], refused["reason"]) == ("refused", "invalid_inbox_arguments")
