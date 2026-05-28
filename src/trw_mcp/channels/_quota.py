"""Tier-down quota enforcement for channel distill segments.

Implements the canonical tier-down ladder and one-pass quota enforcement
loop per PRD-DIST-2400 FR13 (R-05 oscillation prevention).

PRD-DIST-2400 Phase C.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = [
    "TIER_DOWN_LADDER",
    "check_quota",
    "enforce_quota_with_tier_down",
    "tier_down",
    "tier_index",
]

# Canonical tier ladder — highest fidelity to lowest.
TIER_DOWN_LADDER: tuple[str, ...] = ("T4", "T3", "T2", "T1", "T0")


def tier_index(tier: str) -> int:
    """Return the position of *tier* in TIER_DOWN_LADDER.

    Args:
        tier: Tier string, e.g. ``"T2"``.

    Returns:
        Zero-based index; returns len(TIER_DOWN_LADDER) - 1 (floor position)
        if *tier* is not recognised, so unknown tiers are treated as T0.
    """
    try:
        return TIER_DOWN_LADDER.index(tier)
    except ValueError:
        return len(TIER_DOWN_LADDER) - 1


def tier_down(current_tier: str, *, tier_min: str | None = None) -> str:
    """Return the next lower tier in the ladder, respecting *tier_min* floor.

    Args:
        current_tier: The current tier string (e.g. ``"T3"``).
        tier_min: Optional floor tier below which we must not descend.

    Returns:
        Next lower tier string, or *current_tier* if already at floor
        (T0 or *tier_min*).
    """
    current_idx = tier_index(current_tier)
    floor_idx = tier_index(tier_min) if tier_min is not None else len(TIER_DOWN_LADDER) - 1

    next_idx = current_idx + 1
    # Cannot exceed the lesser of floor_idx and ladder end.
    capped = min(next_idx, floor_idx, len(TIER_DOWN_LADDER) - 1)
    return TIER_DOWN_LADDER[capped]


def check_quota(*, content_bytes: int, quota_total_bytes: int | None) -> bool:
    """Return True when *content_bytes* is within quota (or quota is uncapped).

    Args:
        content_bytes: Byte count of the content to test.
        quota_total_bytes: Maximum allowed bytes, or None for uncapped.

    Returns:
        True if the content fits; False if it exceeds the quota.
    """
    if quota_total_bytes is None:
        return True
    return content_bytes <= quota_total_bytes


def enforce_quota_with_tier_down(
    *,
    content: str,
    current_tier: str,
    quota_total_bytes: int | None,
    tier_min: str | None,
    render_at_tier: Callable[[str], str],
) -> tuple[str, str]:
    """One-pass full-ladder quota enforcement (R-05 oscillation prevention).

    Renders content at *current_tier*; if over quota, steps down once per
    iteration and re-renders until quota is satisfied or the floor is reached.
    Never oscillates — each tier is tried at most once.

    Args:
        content: Initial content string (used as seed; render_at_tier
            produces tier-appropriate renditions).
        current_tier: Starting tier for the first render attempt.
        quota_total_bytes: Maximum allowed byte count, or None for uncapped.
        tier_min: Optional floor tier.
        render_at_tier: Callable accepting a tier string and returning the
            rendered content at that tier.

    Returns:
        Tuple of ``(final_content, final_tier)`` where ``final_tier`` is the
        tier actually used.
    """
    tier = current_tier
    rendered = render_at_tier(tier)

    while not check_quota(
        content_bytes=len(rendered.encode("utf-8")),
        quota_total_bytes=quota_total_bytes,
    ):
        next_tier = tier_down(tier, tier_min=tier_min)
        if next_tier == tier:
            # Already at floor — cannot descend further
            break
        tier = next_tier
        rendered = render_at_tier(tier)

    return rendered, tier
