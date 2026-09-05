"""The post-compaction gate's blocked-call payload, built in one place.

PRD-CORE-258-FR01/FR02/NFR01/NFR02/NFR03. Belongs to the ``ceremony.py``
middleware facade.

The gate used to answer a blocked caller with three keys and a diagnosis that
was not the gate's own condition — it named session start, on a branch whose
only test is whether a pre-compaction marker exists on disk. An agent that has
just lost its context has the least material with which to check a claim, and
the claim it was handed was false — while the middleware already held the
marker, and the marker already held the instant of the compaction.

Two rules govern everything here:

* **Never substitute a plausible value.** ``compaction_marker_ts`` is the
  marker's own instant or ``None``; ``marker_state`` says ``read`` or
  ``unreadable``. No branch may emit the current time, an empty string, a zero
  instant, or any other reassuring default (``wiring-defect-patterns.md`` §1).
* **Never echo untrusted text.** The marker is attacker-influenceable, so its
  timestamp reaches the response only as the re-serialization of a parsed
  instant. Anything that does not parse yields ``None`` (NFR03).

``marker_unreadable_reason`` is deliberately log-only (NFR02): the caller needs
to know the timestamp is untrustworthy and what to do next, while which parse
step failed is an operator diagnostic that would widen a response contract for
no caller benefit.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

__all__ = ["CompactionBlock", "build_compaction_block"]

logger = structlog.get_logger(__name__)

#: The gate's own condition, named. Replaces the retired key that named session
#: start — a check this branch has not performed since 2026-04-11. No alias is
#: emitted alongside it (PRD-CORE-258-NFR04).
COMPACTION_GATE_ERROR = "post_compaction_recovery_required"

#: The one action that discharges the obligation.
COMPACTION_GATE_REMEDY = "trw_session_start"

MARKER_STATE_READ = "read"
MARKER_STATE_UNREADABLE = "unreadable"

#: Outside the four parse reasons the marker module names: the read itself
#: raised. Recorded honestly rather than folded into ``invalid_json``, which
#: would assert a parse step that never ran.
REASON_READ_RAISED = "read_raised"


@dataclass(frozen=True, slots=True)
class CompactionBlock:
    """A blocked call's response payload plus the fields its log line repeats."""

    payload: dict[str, object]
    message: str
    marker_state: str
    marker_ts: str | None
    unreadable_reason: str | None


def _read_marker_instant() -> tuple[str | None, str | None]:
    """Return ``(iso_instant, unreadable_reason)`` — never raising (NFR01).

    This runs AFTER the block has been decided, so a failure here must not turn
    a block into a pass; it can only change what the block reports.
    """

    try:
        from trw_mcp.state.pre_compact_marker import read_pre_compact_marker_detail

        result = read_pre_compact_marker_detail()
    except Exception:  # justified: the block is already decided -- reporting must not raise
        logger.debug("compaction_marker_read_raised", component="ceremony", exc_info=True)
        return None, REASON_READ_RAISED
    if result.marker is None:
        return None, result.unreadable_reason
    return result.marker.timestamp, None


def build_compaction_block(tool_name: str, blocked_count: int, max_blocks: int) -> CompactionBlock:
    """Build the blocked-call payload and its matching message."""

    marker_ts, unreadable_reason = _read_marker_instant()
    marker_state = MARKER_STATE_READ if marker_ts is not None else MARKER_STATE_UNREADABLE

    when = (
        f"Your context was compacted at {marker_ts}."
        if marker_ts is not None
        else "Your context was compacted (the recovery marker's timestamp could not be read)."
    )
    # The delegated-sub-agent sentence is preserved in substance: ten of eleven
    # bundled agents hold no trw_session_start, so naming only the first remedy
    # stranded every delegate — observed 2026-07-26, blocks arriving in exact
    # pairs against a MAX_BLOCKS of 2, one call short of the escape built for it.
    message = (
        f"{when} Call trw_session_start() to complete post-compaction recovery:"
        " it reloads your prior learnings and active run state and clears this gate,"
        " so you don't repeat solved problems or miss known gotchas."
        " If you do NOT hold trw_session_start (you are a delegated sub-agent"
        f" sharing your dispatcher's session), retry this call — after {max_blocks}"
        " blocks the gate passes you through, and post-compaction recovery is your"
        " dispatcher's obligation, not yours."
    )

    payload: dict[str, object] = {
        "error": COMPACTION_GATE_ERROR,
        "message": message,
        "tool_attempted": tool_name,
        "compaction_marker_ts": marker_ts,
        "marker_state": marker_state,
        "blocked_count": blocked_count,
        "max_blocks": max_blocks,
        "remedy": COMPACTION_GATE_REMEDY,
    }
    return CompactionBlock(
        payload=payload,
        message=message,
        marker_state=marker_state,
        marker_ts=marker_ts,
        unreadable_reason=unreadable_reason,
    )
