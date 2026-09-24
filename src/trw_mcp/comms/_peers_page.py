"""Bounded peer pages for the comms facade; cursors are not authority.

A cursor carries a version, derived group, caller and last member id. Every
call still binds the current trusted caller. Stable member-id ordering avoids
repeating a peer merely because its enrollment time changes on replacement.
The byte ceiling covers the complete canonical JSON result, not JSON-RPC
framing or the SDK's duplicate text/structured-content wrappers.
"""

from __future__ import annotations

from typing import Any

from trw_mcp.comms import _paging
from trw_mcp.comms._admission import WAKE_UNOBSERVED, availability_of
from trw_mcp.comms._endpoints import Endpoint
from trw_mcp.comms._envelope import MEMBER_ID
from trw_mcp.comms._identity import CallerBinding

_CURSOR_MAX_CHARS = 512


class PageError(ValueError):
    """Closed user-actionable page refusals, never opaque decoder details."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def encode_cursor(binding: CallerBinding, after: str) -> str:
    return _paging.encode_cursor(binding.group_id, binding.member_id, after)


def decode_cursor(cursor: str | None, binding: CallerBinding, *, action: str) -> str:
    """Validate bounded scope before opening or mutating mailbox state."""
    if cursor is None:
        return ""
    fields = _paging.decode_cursor(cursor, max_chars=_CURSOR_MAX_CHARS, arity=3) if action == "list" else None
    if (
        fields is None
        or fields[0] != binding.group_id
        or fields[1] != binding.member_id
        or not isinstance(fields[2], str)
        or not MEMBER_ID.fullmatch(fields[2])
    ):
        raise PageError("invalid_cursor")
    return str(fields[2])


def pack_page(
    binding: CallerBinding,
    rows: list[Endpoint],
    *,
    now: float,
    own_endpoint: Endpoint | None,
    poll_seconds: int,
    limit: int,
    max_bytes: int,
    idle_horizon_seconds: int = 86400,
) -> dict[str, Any]:
    """Pack up to limit rows with explicit continuation within the byte bound.

    Caller fetched limit+1 rows in its operation transaction. Each candidate
    includes its actual continuation; no cursor is appended after measuring.
    """
    base: dict[str, Any] = {
        "status": "ok",
        "member_id": binding.member_id,
        "lease_expires_in_seconds": round(own_endpoint.lease_expires_at - now, 3) if own_endpoint else None,
        "poll_interval_seconds": poll_seconds,
        "delivery": "pull_only",
    }
    entries = (
        (
            {
                "member_id": peer.member_id,
                "live": peer.is_live(now),
                "lease_expires_in_seconds": round(peer.lease_expires_at - now, 3),
                # FR13: advisory activity (never attention) and the generation lane B keys on.
                "availability": availability_of(peer.lease_expires_at, peer.last_seen_at, now, idle_horizon_seconds),
                "generation": peer.generation,
                "last_seen_seconds_ago": round(now - peer.last_seen_at, 3),
                "wake": WAKE_UNOBSERVED,
            },
            encode_cursor(binding, peer.member_id) if index + 1 < len(rows) else None,
        )
        for index, peer in enumerate(rows[:limit])
    )
    result, _count = _paging.pack(base, "peers", entries, max_bytes=max_bytes, refuse=PageError)
    return result
