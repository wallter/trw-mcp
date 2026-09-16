"""PRD-CORE-275-FR10: two separately pinned members exchange a plan and a review
through the REAL public MCP tools.

WHAT THIS PROVES: the protocol and the tool path. Two members of one formation,
each with its own pin, drive `trw_send` / `trw_inbox` through the real dispatch;
the body arrives byte-identical; the reviewer's own check produces the finding;
and a superseding revision renders the earlier review STALE.

WHAT IT DOES NOT PROVE, stated because the distinction is the whole point of the
slice: both peers are driven from ONE process here. This is not two
independently operated harnesses, not native wake, and not unsolicited delivery.
A live cross-harness transcript is a separate demonstration; this is the
automated oracle over the same exchange.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.plan.conftest import Scene
from trw_mcp.models.config import TRWConfig
from trw_mcp.plan import (
    Currency,
    build_proposal,
    classify,
    encode,
    parse_proposal,
    parse_review,
    precheck,
)
from trw_mcp.plan import build_review as build_review_body
from trw_mcp.tools.swarm_comms import register_swarm_comms_tools

PID = "c" * 32


@pytest.fixture
def peers(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Both members enrolled on a comms-enabled config, through the real tools."""
    from trw_mcp.comms import _endpoints

    _endpoints._reset_process_incarnations_for_test()
    config = TRWConfig(comms_enabled=True, cleanup_on_boot=False)
    monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
    server = FastMCP("two-peer")
    register_swarm_comms_tools(server)

    def call(member: str, tool: str, **args: Any) -> dict[str, Any]:
        monkeypatch.setenv("TRW_SESSION_ID", f"pin-{member}")
        result = asyncio.run(server.call_tool(tool, args))
        payload = result.structured_content
        assert isinstance(payload, dict), payload
        return payload

    for member in ("alpha", "beta"):
        assert call(member, "trw_peers", action="enroll")["status"] == "ok"
    yield call
    _endpoints._reset_process_incarnations_for_test()


def _proposal(revision: int = 1, summary: str = "refactor a") -> str:
    return encode(build_proposal(plan_id=PID, revision=revision, paths=["src/a.py"], test_paths=[], summary=summary))


def test_a_plan_survives_the_round_trip_byte_identical(peers: Any) -> None:
    """If the body changed in transit the digest would be worthless."""
    body = _proposal()
    sent = peers(
        "alpha",
        "trw_send",
        recipient_member_id="beta",
        request_key=f"{PID}:1",
        body=body,
        kind="request",
        delivery_class="on_demand",
    )
    assert sent["status"] == "ok", sent

    page = peers("beta", "trw_inbox", action="fetch")

    assert [item["body"] for item in page["items"]] == [body]
    assert parse_proposal(page["items"][0]["body"])["digest"] == parse_proposal(body)["digest"]


def test_the_reviewer_produces_its_own_finding_and_the_proposer_reads_it(peers: Any, scene: Scene) -> None:
    """The full exchange: propose, fetch, review independently, reply, ACK, verify."""
    body = _proposal()
    peers(
        "alpha",
        "trw_send",
        recipient_member_id="beta",
        request_key=f"{PID}:1",
        body=body,
        kind="request",
        delivery_class="on_demand",
    )

    page = peers("beta", "trw_inbox", action="fetch")
    received = parse_proposal(page["items"][0]["body"])
    rows = precheck(scene.manifest, list(received["paths"]), scene.project_root)
    review = encode(
        build_review_body(
            plan_id=str(received["plan_id"]),
            revision=int(str(received["revision"])),
            digest=str(received["digest"]),
            findings=[row.render() for row in rows],
        )
    )
    peers(
        "beta",
        "trw_send",
        recipient_member_id="alpha",
        request_key=f"{PID}:1:review",
        body=review,
        kind="reply",
        delivery_class="on_demand",
    )
    acked = peers("beta", "trw_inbox", action="ack", message_ids=[page["items"][0]["message_id"]])
    assert acked["acknowledged_ids"] == [page["items"][0]["message_id"]]

    back = peers("alpha", "trw_inbox", action="fetch")
    got = back["items"][0]["body"]

    assert classify(parse_review(got), received) is Currency.CURRENT
    assert "alpha" in got  # beta's OWN resolution of the path, not an echo


def test_a_superseding_revision_makes_the_earlier_review_stale(peers: Any) -> None:
    """The load-bearing case: an old opinion must never read as consent."""
    first = parse_proposal(_proposal(revision=1))
    review = build_review_body(
        plan_id=str(first["plan_id"]),
        revision=int(str(first["revision"])),
        digest=str(first["digest"]),
        findings=["fine by me"],
    )
    second = parse_proposal(_proposal(revision=2, summary="refactor a, again"))

    assert classify(review, second) is Currency.STALE


def test_the_exchange_admits_exactly_two_messages(peers: Any) -> None:
    """FR09 conformance, MEASURED on a real exchange rather than enforced."""
    peers(
        "alpha",
        "trw_send",
        recipient_member_id="beta",
        request_key=f"{PID}:1",
        body=_proposal(),
        kind="request",
        delivery_class="on_demand",
    )
    page = peers("beta", "trw_inbox", action="fetch")
    peers(
        "beta",
        "trw_send",
        recipient_member_id="alpha",
        request_key=f"{PID}:1:review",
        body=encode(
            build_review_body(
                plan_id=PID, revision=1, digest=str(parse_proposal(_proposal())["digest"]), findings=["ok"]
            )
        ),
        kind="reply",
        delivery_class="on_demand",
    )
    peers("beta", "trw_inbox", action="ack", message_ids=[page["items"][0]["message_id"]])

    status = peers("alpha", "trw_inbox", action="status")

    assert len(status["items"]) == 2, "one proposal and one review, no more"


def test_an_unrelated_tool_call_carries_no_message(peers: Any) -> None:
    """Pull-only: a plan never arrives inside something the agent asked for."""
    peers(
        "alpha",
        "trw_send",
        recipient_member_id="beta",
        request_key=f"{PID}:1",
        body=_proposal(),
        kind="request",
        delivery_class="on_demand",
    )

    listed = peers("beta", "trw_peers", action="list")

    assert "items" not in listed
    assert not any("plan-review" in str(value) for value in listed.values())
