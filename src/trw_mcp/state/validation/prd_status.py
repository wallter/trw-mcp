"""PRD status state machine — allowed transitions and validation.

Implements FR03 of PRD-FIX-056: defines allowed status transitions for the
simplified caller-facing state machine and provides a validate_status_transition()
helper.

The canonical transition table is VALID_TRANSITIONS in prd_utils.py (uses the
PRDStatus enum). This module derives ALLOWED_TRANSITIONS from that source so
there is exactly one state machine definition.
"""

from __future__ import annotations

from trw_mcp.state.prd_utils import VALID_TRANSITIONS

# Derive ALLOWED_TRANSITIONS from the canonical prd_utils.py VALID_TRANSITIONS.
# Converts enum keys/values to lowercase strings for caller convenience.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    status.value: {t.value for t in targets}
    for status, targets in VALID_TRANSITIONS.items()
}

# Terminal statuses — no outgoing transitions allowed (derived from VALID_TRANSITIONS)
TERMINAL_STATUSES: frozenset[str] = frozenset(
    status.value for status, targets in VALID_TRANSITIONS.items() if not targets
)

# Valid FR-level status values (FR04 — PRD-FIX-056)
VALID_FR_STATUSES: frozenset[str] = frozenset({"active", "deferred", "superseded", "done"})


def validate_status_transition(current: str, target: str) -> bool:
    """Check whether a PRD status transition is permitted.

    Delegates to the canonical VALID_TRANSITIONS table in prd_utils.py
    (expressed as string values via ALLOWED_TRANSITIONS above).

    Identity transitions (current == target) are always valid.
    Unknown current statuses are treated as having no valid transitions
    (returns False for any non-identity target).

    Args:
        current: Current PRD status string (e.g. ``"draft"``).
        target: Desired PRD status string (e.g. ``"review"``).

    Returns:
        True if the transition is allowed by ALLOWED_TRANSITIONS.
    """
    current_lower = current.lower()
    target_lower = target.lower()

    if current_lower == target_lower:
        return True

    return target_lower in ALLOWED_TRANSITIONS.get(current_lower, set())
