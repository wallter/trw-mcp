"""Bounded peer pages for the comms facade; cursors are not authority.

A cursor carries a version, derived group, caller and last member id. Every
call still binds the current trusted caller. Stable member-id ordering avoids
repeating a peer merely because its enrollment time changes on replacement.
The byte ceiling covers the complete canonical JSON result, not JSON-RPC
framing or the SDK's duplicate text/structured-content wrappers.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from trw_mcp.comms._endpoints import Endpoint
from trw_mcp.comms._envelope import canonical_bytes
from trw_mcp.comms._identity import CallerBinding

_CURSOR_MAX_CHARS = 512
_MEMBER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class PageError(ValueError):
    """Closed user-actionable page refusals, never opaque decoder details."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


"""Canonical serialization lives in `_envelope`; this module reuses it.

It used to carry a byte-identical copy. Two definitions of "the canonical form"
is one definition too many: the cursor encoding and the response size bound both
depend on it, so a divergence would desynchronize a cursor from the pages it
indexes without failing anything loudly. `_inbox_page` already imported the
`_envelope` one, which is what made the duplicate visible.
"""


def encode_cursor(binding: CallerBinding, after: str) -> str:
    raw = canonical_bytes([1, binding.group_id, binding.member_id, after])
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None, binding: CallerBinding, *, action: str) -> str:
    """Validate bounded scope before opening or mutating mailbox state."""
    if cursor is None:
        return ""
    if action != "list" or not cursor or len(cursor) > _CURSOR_MAX_CHARS:
        raise PageError("invalid_cursor")
    try:
        raw = base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True)
        decoded = json.loads(raw)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise PageError("invalid_cursor") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 4
        or type(decoded[0]) is not int
        or decoded[0] != 1
        or decoded[1] != binding.group_id
        or decoded[2] != binding.member_id
        or not isinstance(decoded[3], str)
        or not _MEMBER_ID.fullmatch(decoded[3])
    ):
        raise PageError("invalid_cursor")
    after = str(decoded[3])
    if encode_cursor(binding, after) != cursor:
        raise PageError("invalid_cursor")
    return after


def pack_page(
    binding: CallerBinding,
    rows: list[Endpoint],
    *,
    now: float,
    own_endpoint: Endpoint | None,
    poll_seconds: int,
    limit: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Pack up to limit rows with explicit continuation within the byte bound.

    Caller fetched limit+1 rows in its operation transaction. Each candidate
    includes its actual continuation; no cursor is appended after measuring.
    """
    result: dict[str, Any] = {
        "status": "ok",
        "member_id": binding.member_id,
        "lease_expires_in_seconds": round(own_endpoint.lease_expires_at - now, 3) if own_endpoint else None,
        "poll_interval_seconds": poll_seconds,
        "delivery": "pull_only",
        "peers": [],
        "next_cursor": None,
    }
    if len(canonical_bytes(result)) > max_bytes:
        raise PageError("response_too_small")
    for index, peer in enumerate(rows[:limit]):
        more = index + 1 < len(rows)
        candidate = dict(result)
        candidate["peers"] = [
            *result["peers"],
            {
                "member_id": peer.member_id,
                "live": peer.is_live(now),
                "lease_expires_in_seconds": round(peer.lease_expires_at - now, 3),
            },
        ]
        candidate["next_cursor"] = encode_cursor(binding, peer.member_id) if more else None
        if len(canonical_bytes(candidate)) > max_bytes:
            if not result["peers"]:
                raise PageError("response_too_small")
            return result
        result = candidate
    return result
