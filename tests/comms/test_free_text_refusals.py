"""E2E INC-088/089/090: one strip-then-require-non-empty rule for comms free text, and refusals that name the condition."""

from __future__ import annotations

from typing import Any

import pytest
from fastmcp import Client

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_fetch_ack import invoke, transport_scene  # noqa: F401
from tests.comms.test_handoff_actions import accepted, request, step
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms._envelope import valid_next_read

BLANKS = ["   ", "\t", "   ", "　"]


@pytest.mark.parametrize("blank", BLANKS)
def test_a_blank_pointer_cleans_to_an_invalid_one(blank: str) -> None:
    from trw_mcp.comms._envelope import clean_text

    assert not valid_next_read(clean_text(blank))


def test_clean_text_strips_and_passes_non_strings_through() -> None:
    from trw_mcp.comms._envelope import clean_text

    assert clean_text("  wt@abc \n") == "wt@abc"
    assert clean_text(None) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("blank", ["   ", "\t\n"])
async def test_report_refuses_a_blank_pointer_and_the_report_stays_correctable(
    transport_scene: SendScene, blank: str
) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        refused = await step(client, "report", message_id, next_read=blank)
        assert (refused["status"], refused["reason"]) == ("refused", "invalid_next_read")
        assert s.rows("SELECT * FROM handoff_reports") == []
        assert (await step(client, "report", message_id, next_read="wt@abc"))["status"] == "ok"


async def test_report_strips_the_pointer_before_storing(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await accepted(client, s)
        assert (await step(client, "report", message_id, next_read="  wt@abc\n"))["status"] == "ok"
        assert [row[0] for row in s.rows("SELECT next_read FROM handoff_reports")] == ["wt@abc"]
        # An identical retry with different padding is still the same report.
        assert (await step(client, "report", message_id, next_read="wt@abc "))["status"] == "ok"


@pytest.mark.parametrize(
    ("field", "reason"), [("body", "invalid_message_body"), ("request_key", "invalid_request_key")]
)
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
async def test_send_refuses_blank_body_and_key(transport_scene: SendScene, field: str, reason: str, blank: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        args: dict[str, Any] = {"recipient_member_id": "impl-2", "request_key": "k", "body": "hi"}
        args[field] = blank
        refused = await invoke(client, "trw_send", **args)
        assert (refused["status"], refused["reason"]) == ("refused", reason)
        assert s.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


async def test_send_keeps_a_padded_body_and_key_verbatim(transport_scene: SendScene) -> None:
    """Strip is how blankness is JUDGED, never a rewrite of caller data: a body's edge whitespace is content."""
    s = transport_scene
    async with Client(s.server) as client:
        sent = await invoke(client, "trw_send", recipient_member_id="impl-2", request_key=" k ", body=" hi\n")
        assert sent["status"] == "ok", sent
        assert s.rows("SELECT request_key, body FROM admissions") == [(" k ", " hi\n")]


@pytest.mark.parametrize("action", ["ack", "accept", "complete"])
async def test_unknown_message_id_is_named_not_misattributed(transport_scene: SendScene, action: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        message_id = await request(client, s)
        if action == "complete":
            s.actor("impl-1")
        refused = await invoke(client, "trw_inbox", action=action, message_ids=["a" * 32])
        assert (refused["status"], refused["reason"]) == ("refused", "unknown_message_id")
        assert refused["detail"].startswith("no such message")
        # A real id the caller may not touch keeps its own refusal.
        assert message_id


@pytest.mark.parametrize("action", ["ack", "accept", "report", "complete"])
@pytest.mark.parametrize("bad", ["deadbeef", "A" * 32, "g" * 32, "a" * 33])
async def test_non_id_shape_says_so(transport_scene: SendScene, action: str, bad: str) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        await request(client, s)
        extra = {"next_read": "x"} if action == "report" else {}
        refused = await invoke(client, "trw_inbox", action=action, message_ids=[bad], **extra)
        assert (refused["status"], refused["reason"]) == ("refused", "invalid_message_id")
        assert refused["detail"].startswith("message id must be 32 hex characters")
        assert "comms_fetch_max_items" not in refused["detail"]


async def test_report_argument_errors_name_the_argument(transport_scene: SendScene) -> None:
    s = transport_scene
    async with Client(s.server) as client:
        first = await accepted(client, s)
        missing = await step(client, "report", first)
        assert missing["reason"] == "report_needs_next_read"
        assert missing["detail"].startswith("report needs next_read")
        two = await invoke(client, "trw_inbox", action="report", message_ids=[first, "b" * 32], next_read="x")
        assert two["reason"] == "report_takes_one_message_id"
        assert "exactly one message id" in two["detail"]
