"""C1 four-step tier-down logic for copilot-instructions-distill.

Belongs to the CopilotInstructionsDistillRenderer facade in _instructions_distill.py.
Re-exported there for back-compat.

Implements FR03 / NFR01: hard 250-token cap with four-step tier-down ladder.
Token counting uses char/4 + 20% overhead estimate.

PRD-DIST-2406.
"""

from __future__ import annotations

from trw_mcp.channels._provenance import render_provenance_comment
from trw_mcp.channels._telemetry import append_channel_event
from trw_mcp.channels.copilot._templates import render_c1_t0_beacon, render_c1_t1_segment

__all__ = [
    "count_tokens_estimate",
    "render_with_tier_down",
]

# Hard cap (BUDGET_TOKENS defined here to avoid circular import)
BUDGET_TOKENS = 250


def count_tokens_estimate(text: str) -> int:
    """Estimate token count: char/4 with 20% overhead buffer.

    Never claims exact count — always an estimate.

    Args:
        text: Text to estimate.

    Returns:
        Integer estimate with overhead applied.
    """
    raw = len(text) // 4
    return int(raw * 1.2)


def render_with_tier_down(
    sidecar_data: dict[str, object],
    *,
    ts: str,
    budget_tokens: int = BUDGET_TOKENS,
) -> tuple[str, str]:
    """Render T1 content with four-step tier-down ladder if over budget.

    Token budget applies to the full wrapped interior (provenance + content).
    This ensures the 250-token hard cap is enforced on the complete segment.

    Steps:
    1. Full T1 (top-3 conventions + top-3 hotspots with reason)
    2. Compact T1 (top-2 each)
    3. Minimal hotspots (filename+score only), top-2 conventions
    4. Conventions only (top-2)
    5. Floor: T0 beacon

    Args:
        sidecar_data: Parsed sidecar payload.
        ts: ISO-8601 UTC timestamp string.
        budget_tokens: Maximum token count (default 250).

    Returns:
        (rendered_content, tier_used) tuple.
    """
    _convs = sidecar_data.get("conventions")
    conventions_raw: list[object] = list(_convs) if isinstance(_convs, list) else []
    _spots = sidecar_data.get("hotspots")
    hotspots_raw: list[object] = list(_spots) if isinstance(_spots, list) else []

    conventions = [_to_str(c) for c in conventions_raw]
    hotspots_full = [_hotspot_str_full(h) for h in hotspots_raw]
    hotspots_minimal = [_hotspot_str_minimal(h) for h in hotspots_raw]

    # Estimate provenance overhead to stay within budget
    prov_sample = render_provenance_comment(
        channel_id="copilot-instructions-distill",
        sha="sample",
        ts=ts,
        tier="T1",
        regenerate="trw-distill self-improve risk-report --repo . --persist-sidecar",
    )
    prov_tokens = count_tokens_estimate(prov_sample)
    content_budget = max(budget_tokens - prov_tokens, 50)

    # Attempt 1: full T1 (top-3 each)
    content = render_c1_t1_segment(
        conventions=conventions,
        hotspots=hotspots_full,
        max_conventions=3,
        max_hotspots=3,
    )
    if count_tokens_estimate(content) <= content_budget:
        return content, "T1"

    # Attempt 2: reduce to top-2 each
    content = render_c1_t1_segment(
        conventions=conventions,
        hotspots=hotspots_full,
        max_conventions=2,
        max_hotspots=2,
    )
    if count_tokens_estimate(content) <= content_budget:
        _emit_tier_down("T1", "T1-compact")
        return content, "T1"

    # Attempt 3: minimal hotspots (filename+score only), top-2 conventions
    content = render_c1_t1_segment(
        conventions=conventions,
        hotspots=hotspots_minimal,
        max_conventions=2,
        max_hotspots=2,
    )
    if count_tokens_estimate(content) <= content_budget:
        _emit_tier_down("T1", "T1-minimal")
        return content, "T1"

    # Attempt 4: conventions only, no hotspots
    content = render_c1_t1_segment(
        conventions=conventions[:2],
        hotspots=[],
    )
    if count_tokens_estimate(content) <= content_budget:
        _emit_tier_down("T1", "T1-convs-only")
        return content, "T1"

    # Floor: T0 beacon
    _emit_tier_down("T1", "T0")
    return render_c1_t0_beacon(ts=ts), "T0"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_str(item: object) -> str:
    if isinstance(item, dict):
        return str(item.get("text", item.get("description", str(item))))
    return str(item)


def _hotspot_str_full(item: object) -> str:
    if isinstance(item, dict):
        path = item.get("file", item.get("path", "unknown"))
        score = item.get("risk_score", item.get("score", 0.0))
        reason = item.get("reason", item.get("summary", ""))
        score_val = float(score) if score is not None else 0.0
        score_str = f"{score_val:.2f}"
        reason_part = f" — {reason}" if reason else ""
        return f"`{path}` (risk: {score_str}){reason_part}"
    return str(item)


def _hotspot_str_minimal(item: object) -> str:
    if isinstance(item, dict):
        path = item.get("file", item.get("path", "unknown"))
        score_raw = item.get("risk_score", item.get("score", 0.0))
        score_val = float(score_raw) if score_raw is not None else 0.0
        return f"`{path}` (risk: {score_val:.2f})"
    return str(item)


def _emit_tier_down(tier_attempted: str, tier_actual: str) -> None:
    """Fail-open tier-down telemetry."""
    try:
        append_channel_event(
            channel_id="copilot-instructions-distill",
            client="copilot",
            event_type="push_ephemeral",
            tier=tier_attempted,
            extra={"outcome": "tier_down", "tier_attempted": tier_attempted, "tier_actual": tier_actual},
        )
    except Exception:
        pass
