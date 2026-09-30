"""E2E-INC-091/092 (swarm-e2e S12): a self-addressed request, and a next_read that names nothing.

INC-091: ``trw_send`` admitted a request addressed to its own sender, but ``complete`` refuses
the owner of a handoff, so the request stayed pending forever. It is now refused at send.
INC-092: the sender "completes after checking the pointer", but nothing checked it: a report
naming a missing path completed silently. ``complete`` still records the fact (the sender
decides), and now names every id whose path-shaped pointer does not resolve.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

from tests._formation_test_support import (  # noqa: F401
    FormationFixture,
    formation_env,
    make_run_dir,
    open_slot,
    write_pin,
)
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_handoff_actions import accepted, facts, step
from tests.comms.test_policy import SendScene, scene  # noqa: F401


async def test_a_request_addressed_to_its_sender_is_refused_at_send(transport_scene: SendScene) -> None:
    s = transport_scene
    s.actor("impl-1")
    async with Client(s.server) as client:
        refused = await invoke(
            client, "trw_send", recipient_member_id="impl-1", request_key="self", body="task", kind="request"
        )

    assert (refused["status"], refused["reason"]) == ("refused", "handoff_to_self")
    assert s.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


async def reported_with(client: Client[Any], s: SendScene, pointer: str) -> str:
    message_id = await accepted(client, s)
    assert (await step(client, "report", message_id, next_read=pointer))["status"] == "ok"
    s.actor("impl-1")
    return message_id


async def test_complete_names_a_pointer_path_that_does_not_exist(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported_with(client, s, "docs/nonexistent/HANDOFF.md")
        completed = await step(client, "complete", message_id)

    assert completed["status"] == "ok" and "completed" in facts(s, message_id)
    assert completed["unresolved_next_read"] == [message_id]


@pytest.mark.parametrize("pointer", ["worker-3/core-322-s2@76a2010", "PRD-CORE-322", "README.md"])
async def test_complete_is_silent_for_a_resolving_or_non_path_pointer(transport_scene: SendScene, pointer: str) -> None:
    s = transport_scene
    (s.formation.project_root / "README.md").write_text("x", encoding="utf-8")
    async with Client(s.server) as client:
        message_id = await reported_with(client, s, pointer)
        completed = await step(client, "complete", message_id)

    assert completed["status"] == "ok"
    assert "unresolved_next_read" not in completed


def _completion_state(status: dict[str, Any], message_id: str) -> str:
    item = next(item for item in status["items"] if item["message_id"] == message_id)
    return str(item["handoff"]["completion"]["state"])


async def test_a_completed_handoff_whose_pointer_names_nothing_does_not_read_back_as_verified(
    transport_scene: SendScene,
) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported_with(client, s, "docs/nonexistent/HANDOFF.md")
        completed = await step(client, "complete", message_id)
        unresolved = _completion_state(await invoke(client, "trw_inbox", action="status"), message_id)
        # The pointer appears later (the report was written after all): the same completion now reads verified.
        target = s.formation.project_root / "docs" / "nonexistent" / "HANDOFF.md"
        target.parent.mkdir(parents=True)
        target.write_text("handoff", encoding="utf-8")
        resolved = _completion_state(await invoke(client, "trw_inbox", action="status"), message_id)

    assert completed["unresolved_next_read"] == [message_id]
    assert (unresolved, resolved) == ("unresolved", "verified")


async def test_a_completed_handoff_with_a_non_path_pointer_still_reads_verified(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await reported_with(client, s, "worker-3/core-322-s2@76a2010")
        await step(client, "complete", message_id)
        state = _completion_state(await invoke(client, "trw_inbox", action="status"), message_id)

    assert state == "verified"


@pytest.mark.parametrize(
    ("pointer", "resolves"),
    [
        ("worker-3/core-322-s2@76a2010", True),  # branch@SHA names a commit, not a file
        ("PRD-CORE-322", True),  # an id is not a path
        ("docs/missing@notes.md", False),  # a file NAME containing '@' is still a path and is checked
        ("~nosuchuser_w5/report.md", False),  # expanduser raises RuntimeError for an unknown user: no crash
        ("/definitely/not/there/report.md", False),
    ],
)
def test_pointer_resolves_never_raises_and_only_spares_commits_and_ids(pointer: str, resolves: bool) -> None:
    from trw_mcp.comms._handoff import pointer_resolves

    assert pointer_resolves(pointer) is resolves
