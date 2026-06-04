"""Meta-tune auto-throttle engine.

Evaluates per-channel throttle decisions based on adjusted correlation
rate vs. per-client thresholds.  When triggered, updates the tier in
manifest.yaml and emits a telemetry event.

Consumes CLIENT_THROTTLE_THRESHOLDS, COPILOT_THROTTLE_MIN_N, and
DEFAULT_THROTTLE_MIN_N from _manifest_models.py.

PRD-DIST-2400 §meta-tune.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.channels._manifest_models import (
    CLIENT_THROTTLE_THRESHOLDS,
    COPILOT_THROTTLE_MIN_N,
    DEFAULT_THROTTLE_MIN_N,
)
from trw_mcp.channels._quota import TIER_DOWN_LADDER as _QUOTA_TIER_DOWN_LADDER

log = structlog.get_logger(__name__)

__all__ = [
    "ThrottleDecision",
    "ThrottleVerdict",
    "apply_throttle",
    "evaluate_throttle",
]

# ---------------------------------------------------------------------------
# Tier ladder (ascending = better; derived from canonical _quota.TIER_DOWN_LADDER)
#
# _quota.TIER_DOWN_LADDER = ("T4", "T3", "T2", "T1", "T0") — descending (best→worst).
# _TIER_LADDER reverses it to ascending (worst→best) so index 0=T0, index 4=T4.
# Using the same canonical source avoids T4 omission and future drift (HIGH-2 fix).
# ---------------------------------------------------------------------------

_TIER_LADDER: list[str] = list(reversed(_QUOTA_TIER_DOWN_LADDER))


def _tier_index(tier: str) -> int:
    try:
        return _TIER_LADDER.index(tier)
    except ValueError:
        return len(_TIER_LADDER) - 1  # unknown → treat as top (T4)


def _tier_down(tier: str) -> str:
    idx = _tier_index(tier)
    return _TIER_LADDER[max(0, idx - 1)]


def _tier_up(tier: str, tier_default: str) -> str:
    """Recover tier up by one step, bounded by tier_default."""
    idx = _tier_index(tier)
    default_idx = _tier_index(tier_default)
    return _TIER_LADDER[min(default_idx, idx + 1)]


# ---------------------------------------------------------------------------
# Verdict enum
# ---------------------------------------------------------------------------


class ThrottleVerdict(str, Enum):
    HOLD = "ok"
    THROTTLE_DOWN = "throttle_down"
    THROTTLE_CLEAR = "throttle_clear"
    INSUFFICIENT_DATA = "insufficient_data"


# ---------------------------------------------------------------------------
# ThrottleDecision model
# ---------------------------------------------------------------------------


class ThrottleDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    channel_id: str
    client: str
    verdict: ThrottleVerdict
    adjusted_rate: float
    threshold: float
    n_events: int
    min_n: int
    reason: str


# ---------------------------------------------------------------------------
# evaluate_throttle
# ---------------------------------------------------------------------------


def evaluate_throttle(
    channel_id: str,
    client: str,
    stats: dict[str, Any],
    *,
    min_n_override: int | None = None,
) -> ThrottleDecision:
    """Evaluate throttle decision for one channel/client pair.

    *stats* must contain:
    - ``adjusted_rate`` (float 0-1)
    - ``total_pushes`` (int)

    Returns a ThrottleDecision.  Never raises.
    """
    try:
        return _evaluate(channel_id, client, stats, min_n_override=min_n_override)
    except Exception as exc:
        log.debug(
            "meta_tune_throttle_evaluate_error",
            channel_id=channel_id,
            client=client,
            error=str(exc),
            outcome="evaluate_error",
        )
        return ThrottleDecision(
            channel_id=channel_id,
            client=client,
            verdict=ThrottleVerdict.HOLD,
            adjusted_rate=0.0,
            threshold=0.0,
            n_events=0,
            min_n=DEFAULT_THROTTLE_MIN_N,
            reason=f"evaluate_error: {exc}",
        )


def _evaluate(
    channel_id: str,
    client: str,
    stats: dict[str, Any],
    *,
    min_n_override: int | None,
) -> ThrottleDecision:
    threshold_tuple = CLIENT_THROTTLE_THRESHOLDS.get(client, (0.20, 3))
    threshold, _window_count = threshold_tuple

    if client == "copilot":
        min_n = COPILOT_THROTTLE_MIN_N
    else:
        min_n = DEFAULT_THROTTLE_MIN_N
    if min_n_override is not None:
        min_n = min_n_override

    adj = float(stats.get("adjusted_rate", 0.0))
    n = int(stats.get("total_pushes", 0))

    if n < min_n:
        return ThrottleDecision(
            channel_id=channel_id,
            client=client,
            verdict=ThrottleVerdict.INSUFFICIENT_DATA,
            adjusted_rate=adj,
            threshold=threshold,
            n_events=n,
            min_n=min_n,
            reason=f"n={n} < min_n={min_n}; insufficient data",
        )

    if adj < threshold:
        return ThrottleDecision(
            channel_id=channel_id,
            client=client,
            verdict=ThrottleVerdict.THROTTLE_DOWN,
            adjusted_rate=adj,
            threshold=threshold,
            n_events=n,
            min_n=min_n,
            reason=f"adj_rate={adj:.3f} < threshold={threshold}; throttle_down",
        )

    # adj >= threshold: if currently throttled, recover
    return ThrottleDecision(
        channel_id=channel_id,
        client=client,
        verdict=ThrottleVerdict.THROTTLE_CLEAR,
        adjusted_rate=adj,
        threshold=threshold,
        n_events=n,
        min_n=min_n,
        reason=f"adj_rate={adj:.3f} >= threshold={threshold}; ok",
    )


# ---------------------------------------------------------------------------
# apply_throttle
# ---------------------------------------------------------------------------


def apply_throttle(
    channel_id: str,
    decision: ThrottleDecision,
    manifest_path: Path,
) -> bool:
    """Apply THROTTLE_DOWN or THROTTLE_CLEAR to the channel tier in manifest.

    Updates ChannelEntry.tier_default in manifest.yaml (via _manifest_loader)
    and emits a telemetry event.  Returns True on success, False on failure.
    Fail-open: never raises.
    """
    if decision.verdict not in (
        ThrottleVerdict.THROTTLE_DOWN,
        ThrottleVerdict.THROTTLE_CLEAR,
    ):
        return False

    try:
        return _apply(channel_id, decision, manifest_path)
    except Exception as exc:
        log.debug(
            "meta_tune_apply_throttle_error",
            channel_id=channel_id,
            verdict=decision.verdict.value,
            error=str(exc),
            outcome="apply_failed",
        )
        return False


def _apply(
    channel_id: str,
    decision: ThrottleDecision,
    manifest_path: Path,
) -> bool:
    from trw_mcp.channels._manifest_loader import load, write
    from trw_mcp.channels._telemetry import append_channel_event

    if not manifest_path.exists():
        log.debug(
            "meta_tune_apply_throttle_no_manifest",
            path=str(manifest_path),
            outcome="manifest_missing",
        )
        return False

    manifest = load(manifest_path)

    # Find the matching channel entry
    target = None
    for entry in manifest.channels:
        if entry.id == channel_id and entry.client == decision.client:
            target = entry
            break

    if target is None:
        log.debug(
            "meta_tune_apply_throttle_channel_not_found",
            channel_id=channel_id,
            client=decision.client,
            outcome="channel_not_found",
        )
        return False

    old_tier = str(target.tier_default)
    tier_default = str(target.tier_default)

    if decision.verdict == ThrottleVerdict.THROTTLE_DOWN:
        new_tier = _tier_down(old_tier)
        event_type = "throttle_applied"
    else:
        new_tier = _tier_up(old_tier, tier_default)
        event_type = "throttle_cleared"

    if new_tier == old_tier:
        # Already at floor/ceiling — no-op
        log.debug(
            "meta_tune_apply_throttle_no_change",
            channel_id=channel_id,
            tier=old_tier,
            outcome="tier_unchanged",
        )
        return True

    # HIGH-5 fix: use model_copy(update=...) instead of __dict__ mutation so
    # Pydantic v2 validation runs and the model remains consistent.  Then
    # replace the entry in the channels list to propagate the change.
    updated_entry = target.model_copy(update={"tier_default": new_tier})
    manifest.channels = [
        updated_entry if (e.id == channel_id and e.client == decision.client) else e
        for e in manifest.channels
    ]

    write(manifest, manifest_path)

    # Emit telemetry
    append_channel_event(
        channel_id=channel_id,
        client=decision.client,
        event_type=event_type,
        log_path=manifest_path.parent.parent / "telemetry" / "channel-events.jsonl",
        old_tier=old_tier,
        new_tier=new_tier,
        adjusted_rate=decision.adjusted_rate,
        threshold=decision.threshold,
    )

    log.info(
        "meta_tune_throttle_applied",
        channel_id=channel_id,
        client=decision.client,
        verdict=decision.verdict.value,
        old_tier=old_tier,
        new_tier=new_tier,
        outcome="tier_updated",
    )
    return True
