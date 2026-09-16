"""Public peer tool registration; behavior belongs to the comms facade."""

from __future__ import annotations

from typing import Any

from fastmcp import Context, FastMCP

from trw_mcp.comms import DeliveryClass, InboxAction, MessageKind, PeerAction, inbox, peers, send


def register_swarm_comms_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_peers(action: PeerAction = "list", cursor: str | None = None, ctx: Context | None = None) -> dict[str, Any]:
        """Use when announcing presence, renewing a lease, or listing formation peers.

        Output: peer liveness and next_cursor; pass it with action="list" to continue.
        Pull-only; never wakes peers.
        """
        return peers(action, ctx, cursor=cursor)

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
        return send(recipient_member_id, request_key, body, kind, delivery_class, ctx, scope=scope)

    @server.tool()
    def trw_inbox(
        action: InboxAction = "fetch",
        message_ids: list[str] | None = None,
        cursor: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Use when fetching messages, ACKing received IDs, or reading body-free status.

        Fetch/status return items and next_cursor; ACK takes message_ids only.
        Fresh fetch recovers pending traffic. Pull-only; ACK is not work completion.
        """
        return inbox(action, message_ids, cursor, ctx)


__all__ = ["register_swarm_comms_tools"]
