"""The one cursor codec and byte-bounded page packer for comms pages (ledger RC-001, RC-002).

Belongs to the ``trw_mcp.comms`` facade; used by ``_inbox_page`` and ``_peers_page``.

A cursor is ``urlsafe_base64(canonical_json([1, *fields]))``: a version tag, then
the page's own fields. Decoding refuses anything that does not re-encode to the
exact same string, so a cursor has one spelling and a tampered or re-padded one
is rejected rather than silently normalized. Each page validates its own fields;
this module owns only the envelope. Cursors resume traversal and carry no
authority: every call still binds the current trusted caller.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Iterable
from typing import Any

from trw_mcp.comms._envelope import canonical_bytes

CURSOR_VERSION = 1


def encode_cursor(*fields: object) -> str:
    return base64.urlsafe_b64encode(canonical_bytes([CURSOR_VERSION, *fields])).decode("ascii")


def decode_cursor(cursor: str, *, max_chars: int, arity: int) -> list[Any] | None:
    """The cursor's fields (version stripped), or None when it is not a canonical cursor of *arity* fields."""
    if not cursor or len(cursor) > max_chars:
        return None
    try:
        decoded = json.loads(base64.b64decode(cursor.encode("ascii"), altchars=b"-_", validate=True))
    except (ValueError, UnicodeError, binascii.Error):
        # trw-fail-silent-allow: an undecodable cursor is reported to the caller as invalid_cursor
        return None
    if (
        not isinstance(decoded, list)
        or len(decoded) != arity + 1
        or type(decoded[0]) is not int
        or decoded[0] != CURSOR_VERSION
    ):
        return None
    fields = decoded[1:]
    try:
        canonical = encode_cursor(*fields)
    except (TypeError, ValueError):
        # trw-fail-silent-allow: fields that cannot be re-encoded are not a canonical cursor
        return None
    return fields if canonical == cursor else None


def fits(payload: dict[str, Any], max_bytes: int) -> bool:
    """The response byte bound: the canonical JSON payload, not transport framing."""
    return len(canonical_bytes(payload)) <= max_bytes


def pack(
    base: dict[str, Any],
    key: str,
    entries: Iterable[tuple[dict[str, Any], str | None]],
    *,
    max_bytes: int,
    refuse: Callable[[str], Exception],
) -> tuple[dict[str, Any], int]:
    """Add ``(item, next_cursor)`` entries under *key* while the canonical payload fits.

    *entries* is consumed lazily, so an item past the byte bound is never built.
    Each candidate carries its own continuation, so nothing is appended after
    measuring. Returns the payload and how many entries it holds. Refuses
    ``response_too_small`` when not even the empty page, or its first entry, fits.
    """
    payload = {**base, key: [], "next_cursor": None}
    if not fits(payload, max_bytes):
        raise refuse("response_too_small")
    included = 0
    for item, next_cursor in entries:
        candidate = {**payload, key: [*payload[key], item], "next_cursor": next_cursor}
        if not fits(candidate, max_bytes):
            if not included:
                raise refuse("response_too_small")
            break
        payload = candidate
        included += 1
    return payload, included


__all__ = ["CURSOR_VERSION", "decode_cursor", "encode_cursor", "fits", "pack"]
