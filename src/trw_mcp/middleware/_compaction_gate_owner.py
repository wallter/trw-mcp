"""Who owns the on-disk pre-compact marker, relative to the caller.

Belongs to the ``middleware/ceremony.py`` facade (PRD-CORE-258-FR10); split out
so the gate module stays under the 350 effective-LOC ratchet.

The marker names its owner as a *pin key* (``resolve_pin_key``) — the one
identifier an MCP server and a shell hook can both observe — never as a
FastMCP ``session_id`` no hook can see. Ownership is a FIVE-state result, not a
boolean, because the two consumers fail in OPPOSITE directions on the states
that are not a positive answer:

* the ARM site exempts a session only on ``foreign`` (a LIVE other owner);
  ``unowned``, ``orphaned`` and ``unknown`` all keep blanket arming — not
  knowing is never a licence to skip recovery;
* the CLEAR site deletes only on ``mine``, ``unowned`` or ``orphaned``;
  ``foreign`` and ``unknown`` leave the marker on disk — deleting is the
  irreversible act, so an owner we could not resolve is treated as somebody
  else's obligation.

``orphaned`` exists because ``resolve_pin_key`` is not reconnect-stable for every
client (codex audit 2026-09-05 row 5): after an MCP restart the same logical
client can resolve a fresh key, and a marker it wrote minutes earlier would
otherwise read as ``foreign`` and exempt it from its own recovery — bypassing
even the terminal-tool block. A foreign owner is therefore honoured only while
its pin is LIVE in the pin store (present and not expired per ``_pin_ttl``);
an absent or expired owner pin is an orphan and arms the whole generation
exactly like an ownerless marker.
"""

from __future__ import annotations

from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

MarkerOwnership = Literal["mine", "foreign", "orphaned", "unowned", "unknown"]


def _owner_pin_is_live(owner_pin_key: str) -> bool:
    """True when *owner_pin_key* is present in the pin store and not expired."""

    from trw_mcp.state._pin_store import get_pin_entry
    from trw_mcp.state._pin_ttl import pin_entry_is_expired, resolve_pin_ttl_hours

    entry = get_pin_entry(owner_pin_key)
    if entry is None:
        return False
    expired, _age = pin_entry_is_expired(entry, pin_ttl_hours=resolve_pin_ttl_hours())
    return not expired


def marker_ownership(ctx: object | None) -> MarkerOwnership:
    """Resolve the marker's owner against the caller's pin key (see module doc).

    The bundled PreCompact hook writes no owner field, so ``unowned`` is the
    ordinary Claude Code compaction path and keeps today's behaviour at both
    call sites.
    """

    try:
        from trw_mcp.state._paths import resolve_pin_key
        from trw_mcp.state.pre_compact_marker import read_pre_compact_marker

        marker = read_pre_compact_marker()
        if marker is None or not marker.owner_pin_key:
            return "unowned"
        if marker.owner_pin_key == resolve_pin_key(ctx):
            return "mine"
        if _owner_pin_is_live(marker.owner_pin_key):
            return "foreign"
        logger.info(
            "compaction_gate_marker_owner_orphaned",
            component="ceremony",
            op="check_marker_owner",
            owner_pin_key=marker.owner_pin_key,
            outcome="blanket_arming",
        )
        return "orphaned"
    except Exception:  # justified: the caller decides the fail direction; "unknown" is reported, never guessed
        logger.debug(
            "compaction_gate_owner_check_failed",
            component="ceremony",
            op="check_marker_owner",
            outcome="owner_unknown",
            exc_info=True,
        )
        return "unknown"


def marker_owner_exempts(ctx: object | None) -> bool:
    """True only when we POSITIVELY know the marker belongs to another LIVE session."""

    return marker_ownership(ctx) == "foreign"


__all__ = ["MarkerOwnership", "marker_owner_exempts", "marker_ownership"]
