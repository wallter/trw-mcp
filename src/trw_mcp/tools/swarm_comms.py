"""Public peer tool registration; behavior belongs to the comms facade."""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from fastmcp import Context, FastMCP
from pydantic import Field

from trw_mcp.comms import DeliveryClass, InboxAction, MessageKind, PeerAction, inbox, peers, send

#: Every ``trw_inbox`` action that is actually a peer operation (PRD-CORE-300
#: FR10): dispatch to the ``peers`` facade instead of ``inbox`` for these.
#: Derived from PeerAction rather than re-listed, so the two cannot drift.
_PEER_ACTIONS: frozenset[str] = frozenset(get_args(PeerAction))

InboxOrPeerAction = Literal[
    "fetch", "ack", "status", "enroll", "list", "heartbeat", "announce", "withdraw", "discover", "ack_pause"
]


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

        Address exactly one of recipient_member_id or scope (repo-relative
        path to declared peers). Reuse request_key only for exact retries.
        Output: durable receipt/refusal; pull-only — never a wake, grant, or
        completion ack.
        """
        # A request for unsupported push must explicitly report the downgrade.
        return _tool_response(
            send(recipient_member_id, request_key, body, kind, delivery_class, ctx, scope=scope),
            report_pull_only=delivery_class != "on_demand",
        )

    @server.tool()
    def trw_inbox(
        action: InboxOrPeerAction = "fetch",
        message_ids: list[str] | None = None,
        cursor: str | None = None,
        wait_seconds: Annotated[int, Field(strict=True)] = 0,
        pause_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when fetching messages, ACKing IDs, reading body-free status, or
        running a peer action (enroll, list, heartbeat, announce, withdraw,
        discover, ack_pause).

        Fetch/status/list return items or peers; next_cursor pages either.
        ACK takes message_ids only; ack_pause takes pause_id. Fresh fetch
        recovers pending traffic (pull-only; ACK is not completion).
        wait_seconds>0 retries an empty fetch in-process until the deadline.
        """
        if action in _PEER_ACTIONS:
            peer_action: PeerAction = action  # type: ignore[assignment]
            return _tool_response(peers(peer_action, ctx, cursor=cursor, pause_id=pause_id))
        # strict=True: the transport rejects bool/float/str before the handler (FR11).
        inbox_action: InboxAction = action  # type: ignore[assignment]
        return _tool_response(inbox(inbox_action, message_ids, cursor, ctx, wait_seconds))


__all__ = ["register_swarm_comms_tools"]
