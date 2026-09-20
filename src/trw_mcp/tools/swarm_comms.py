"""Public peer tool registration; behavior belongs to the comms facade."""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field

from trw_mcp.comms import DeliveryClass, InboxAction, MessageKind, PeerAction, inbox, peers, send


def _tool_response(payload: dict[str, Any], *, report_pull_only: bool = False) -> dict[str, Any]:
    """Omit invariant transport labels and absent continuation, never peer content.

    Internal facades keep their full contract. At the MCP boundary an absent
    next_cursor means the page is complete; an empty items list remains explicit.
    Never recursively compact: bodies, receipts and exact timestamps are evidence.
    """
    return {
        key: value
        for key, value in payload.items()
        if not (key == "delivery" and value == "pull_only" and not report_pull_only)
        and not (key == "next_cursor" and value is None)
    }


def register_swarm_comms_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_peers(action: PeerAction = "list", cursor: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
        """Use when announcing presence, renewing a lease, or listing formation peers.

        Output: peer liveness; next_cursor is present only when another page exists.
        Pass it with action="list" to continue.
        Pull-only; never wakes peers.
        """
        return _tool_response(peers(action, ctx, cursor=cursor))

    @server.tool()
    def trw_send(
        request_key: str,
        body: str,
        recipient_member_id: str | None = None,
        scope: str | None = None,
        kind: MessageKind = "request",
        delivery_class: DeliveryClass = "on_demand",
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when sending a request, reply or status to a formation peer.

        Address exactly one of recipient_member_id, or scope: a repo-relative path,
        reaching the peers that declared it. Reuse request_key only for an exact retry.
        Output: durable receipt or refusal; pull-only, never a wake, permission grant
        or completion acknowledgment.
        """
        # A request for unsupported push must explicitly report the downgrade.
        return _tool_response(
            send(recipient_member_id, request_key, body, kind, delivery_class, ctx, scope=scope),
            report_pull_only=delivery_class != "on_demand",
        )

    @server.tool()
    def trw_inbox(
        action: InboxAction = "fetch",
        message_ids: list[str] | None = None,
        cursor: str | None = None,
        wait_seconds: Annotated[int, Field(strict=True)] = 0,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when fetching messages, ACKing received IDs, or reading body-free status.

        Fetch/status return items; next_cursor is present only when another page exists.
        ACK takes message_ids only.
        Fresh fetch recovers pending traffic. Pull-only; ACK is not work completion.
        wait_seconds>0 retries an empty fresh fetch in-process until the deadline.
        """
        # strict=True: the transport rejects bool/float/str before the handler (FR11).
        return _tool_response(inbox(action, message_ids, cursor, ctx, wait_seconds))


__all__ = ["register_swarm_comms_tools"]
