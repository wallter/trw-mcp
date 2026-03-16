"""Adaptive ceremony depth: complexity classification and tier-aware scoring.

PRD-CORE-060: Complexity signals -> tier -> phase requirements -> ceremony score.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.
"""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.run import (
    ComplexityClass,
    ComplexityOverride,
    ComplexitySignals,
    PhaseRequirements,
)
from trw_mcp.models.typed_dicts import TierCeremonyScoreResult
from trw_mcp.scoring._utils import get_config

# --- Adaptive Ceremony Depth (PRD-CORE-060) ---

# High-risk signal names used for hard override detection (FR05)
_HIGH_RISK_SIGNALS: tuple[str, ...] = (
    "security_change",
    "data_migration",
    "unknown_codebase",
)


def classify_complexity(
    signals: ComplexitySignals,
    config: TRWConfig | None = None,
) -> tuple[ComplexityClass, int, ComplexityOverride | None]:
    """Classify task complexity into MINIMAL/STANDARD/COMPREHENSIVE (FR01+FR05).

    Computes a raw score using the 6-signal point formula, then applies
    hard overrides for high-risk signals.

    Args:
        signals: The 6+3 complexity signals from the caller.
        config: Optional config override; uses singleton if None.

    Returns:
        Tuple of (tier, raw_score, override_or_None).
    """
    cfg = config or get_config()

    # FR01: Compute raw score from 6 core signals
    files_capped = min(signals.files_affected, cfg.complexity_weight_files_affected_max)
    raw_score = (
        files_capped
        + (cfg.complexity_weight_novel_patterns if signals.novel_patterns else 0)
        + (cfg.complexity_weight_cross_cutting if signals.cross_cutting else 0)
        + (cfg.complexity_weight_architecture_change if signals.architecture_change else 0)
        + (cfg.complexity_weight_external_integration if signals.external_integration else 0)
        + (cfg.complexity_weight_large_refactoring if signals.large_refactoring else 0)
    )

    # FR01: Tier assignment from raw score
    if raw_score <= cfg.complexity_tier_minimal:
        tier = ComplexityClass.MINIMAL
    elif raw_score >= cfg.complexity_tier_comprehensive + 1:
        tier = ComplexityClass.COMPREHENSIVE
    else:
        tier = ComplexityClass.STANDARD

    # FR05: Hard override for high-risk signals
    active_risk_signals = [name for name in _HIGH_RISK_SIGNALS if getattr(signals, name, False)]
    override: ComplexityOverride | None = None

    if len(active_risk_signals) >= cfg.complexity_hard_override_threshold:
        # 2+ high-risk signals -> force COMPREHENSIVE
        override = ComplexityOverride(
            reason="hard override: multiple high-risk signals",
            signals=active_risk_signals,
            raw_score=raw_score,
        )
        tier = ComplexityClass.COMPREHENSIVE
    elif len(active_risk_signals) == 1 and tier == ComplexityClass.MINIMAL:
        # Single high-risk signal escalates MINIMAL -> STANDARD
        override = ComplexityOverride(
            reason="escalation: single high-risk signal prevents MINIMAL",
            signals=active_risk_signals,
            raw_score=raw_score,
        )
        tier = ComplexityClass.STANDARD

    return tier, raw_score, override


def get_phase_requirements(tier: ComplexityClass) -> PhaseRequirements:
    """Return phase mandatory/optional/skipped classification for a tier (FR04).

    IMPLEMENT and DELIVER are never in skipped regardless of tier.

    Args:
        tier: The complexity tier.

    Returns:
        PhaseRequirements model.
    """
    if tier == ComplexityClass.MINIMAL:
        # MINIMAL is for truly trivial tasks (1 file, typo fix).
        # Still requires IMPLEMENT + VALIDATE + DELIVER — skipping tests is never OK.
        return PhaseRequirements(
            mandatory=["IMPLEMENT", "VALIDATE", "DELIVER"],
            optional=[],
            skipped=["RESEARCH", "PLAN", "REVIEW"],
        )
    if tier == ComplexityClass.STANDARD:
        # STANDARD is the default for most tasks. REVIEW is mandatory —
        # independent verification before delivery prevents false completion.
        return PhaseRequirements(
            mandatory=["PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"],
            optional=[],
            skipped=["RESEARCH"],
        )
    # COMPREHENSIVE — all phases mandatory
    return PhaseRequirements(
        mandatory=["RESEARCH", "PLAN", "IMPLEMENT", "VALIDATE", "REVIEW", "DELIVER"],
        optional=[],
        skipped=[],
    )


# --- Tier-Aware Ceremony Score (PRD-CORE-060-FR03) ---


class _TierExpectation:
    """Expected ceremony events and scoring rules for a complexity tier."""

    __slots__ = (
        "checkpoint_min",
        "events",
        "missing_review_penalty",
        "review_bonus",
        "review_mandatory",
    )

    def __init__(
        self,
        events: frozenset[str],
        checkpoint_min: int,
        review_mandatory: bool,
        review_bonus: int,
        missing_review_penalty: int,
    ) -> None:
        self.events = events
        self.checkpoint_min = checkpoint_min
        self.review_mandatory = review_mandatory
        self.review_bonus = review_bonus
        self.missing_review_penalty = missing_review_penalty


_TIER_EXPECTATIONS: dict[str, _TierExpectation] = {
    "MINIMAL": _TierExpectation(
        # Truly trivial (1-file fix). Still requires build_check + deliver.
        events=frozenset({"trw_recall", "trw_build_check", "trw_deliver"}),
        checkpoint_min=0,
        review_mandatory=False,
        review_bonus=5,
        missing_review_penalty=0,
    ),
    "STANDARD": _TierExpectation(
        # Most tasks. Review is mandatory — skipping it is a 15-point penalty.
        events=frozenset(
            {
                "trw_recall",
                "trw_init",
                "trw_checkpoint",
                "trw_build_check",
                "trw_deliver",
                "trw_review",
            }
        ),
        checkpoint_min=1,
        review_mandatory=True,
        review_bonus=0,
        missing_review_penalty=15,
    ),
    "COMPREHENSIVE": _TierExpectation(
        # Complex multi-file work. All phases mandatory, heavy review penalty.
        events=frozenset(
            {
                "trw_recall",
                "trw_init",
                "trw_checkpoint",
                "trw_build_check",
                "trw_deliver",
                "trw_review",
            }
        ),
        checkpoint_min=1,
        review_mandatory=True,
        review_bonus=0,
        missing_review_penalty=25,
    ),
}


def _normalize_tier_string(complexity_class: ComplexityClass | str | None) -> str:
    """Normalize complexity_class input to a valid tier string.

    Args:
        complexity_class: The tier (enum, string, or None).

    Returns:
        Valid tier string from _TIER_EXPECTATIONS.
    """
    if complexity_class is None:
        tier_str = "STANDARD"
    elif isinstance(complexity_class, ComplexityClass):
        tier_str = complexity_class.value
    else:
        tier_str = str(complexity_class).upper()
    return tier_str if tier_str in _TIER_EXPECTATIONS else "STANDARD"


def _detect_ceremony_events(
    events: list[dict[str, object]],
) -> tuple[bool, bool, int, bool, bool, bool, bool]:
    """Scan event list and detect presence of key ceremony events.

    Returns tuple of (has_recall, has_init, checkpoint_count, has_learn,
    has_build_check, has_deliver, has_review).
    """
    has_recall = False
    has_init = False
    checkpoint_count = 0
    has_learn = False
    has_build_check = False
    has_deliver = False
    has_review = False

    for evt in events:
        event_type = str(evt.get("event", ""))
        tool_name = str(evt.get("tool_name", ""))
        is_tool = event_type == "tool_invocation"

        if event_type == "session_start" or (is_tool and tool_name == "trw_session_start"):
            has_recall = True
        elif event_type == "run_init" or (is_tool and tool_name == "trw_init"):
            has_init = True
        elif event_type == "checkpoint" or (is_tool and tool_name == "trw_checkpoint"):
            checkpoint_count += 1
        elif "learn" in event_type or (is_tool and tool_name == "trw_learn"):
            has_learn = True
        elif event_type == "build_check_complete" or (is_tool and tool_name == "trw_build_check"):
            has_build_check = True
        elif event_type in ("reflection_complete", "claude_md_synced", "trw_deliver_complete") or (
            is_tool and tool_name in ("trw_deliver", "trw_reflect")
        ):
            has_deliver = True
        elif event_type == "review_complete" or (is_tool and tool_name == "trw_review"):
            has_review = True

    return has_recall, has_init, checkpoint_count, has_learn, has_build_check, has_deliver, has_review


def _count_matched_events(
    tier_exp: _TierExpectation,
    has_recall: bool,
    has_init: bool,
    checkpoint_count: int,
    has_learn: bool,
    has_build_check: bool,
    has_deliver: bool,
    has_review: bool,
) -> int:
    """Count how many expected events are present."""
    expected = tier_exp.events
    matched = 0

    if "trw_recall" in expected and has_recall:
        matched += 1
    if "trw_init" in expected and has_init:
        matched += 1
    if "trw_checkpoint" in expected and checkpoint_count >= max(tier_exp.checkpoint_min, 1):
        matched += 1
    if "trw_learn" in expected and has_learn:
        matched += 1
    if "trw_build_check" in expected and has_build_check:
        matched += 1
    if "trw_deliver" in expected and has_deliver:
        matched += 1
    if "trw_review" in expected and has_review:
        matched += 1

    return matched


def _apply_review_adjustments(
    score: int,
    tier_exp: _TierExpectation,
    has_review: bool,
) -> int:
    """Apply review bonus or penalty to the base score."""
    if has_review and tier_exp.review_bonus > 0:
        score = min(100, score + tier_exp.review_bonus)
    if tier_exp.review_mandatory and not has_review:
        score = max(0, score - tier_exp.missing_review_penalty)
    return score


def compute_tier_ceremony_score(
    events: list[dict[str, object]],
    complexity_class: ComplexityClass | str | None = None,
) -> TierCeremonyScoreResult:
    """Compute tier-aware ceremony score (PRD-CORE-060-FR03).

    Normalizes ceremony scores against tier-appropriate phase sets and
    event expectations so that MINIMAL tasks are not penalized against
    COMPREHENSIVE baselines.

    If complexity_class is None, defaults to STANDARD behavior
    (backward compatibility).

    Args:
        events: List of event dicts from events.jsonl.
        complexity_class: The tier to score against. Accepts enum or string.

    Returns:
        Dict with score (0-100), tier used, and per-component details.
    """
    tier_str = _normalize_tier_string(complexity_class)
    tier_exp = _TIER_EXPECTATIONS[tier_str]

    has_recall, has_init, checkpoint_count, has_learn, has_build_check, has_deliver, has_review = (
        _detect_ceremony_events(events)
    )

    matched = _count_matched_events(
        tier_exp,
        has_recall,
        has_init,
        checkpoint_count,
        has_learn,
        has_build_check,
        has_deliver,
        has_review,
    )

    total_expected = len(tier_exp.events)
    score = round((matched / max(total_expected, 1)) * 100)
    score = _apply_review_adjustments(score, tier_exp, has_review)

    return TierCeremonyScoreResult(
        score=score,
        tier=tier_str,
        matched_events=matched,
        expected_events=total_expected,
        has_recall=has_recall,
        has_init=has_init,
        checkpoint_count=checkpoint_count,
        has_learn=has_learn,
        has_build_check=has_build_check,
        has_deliver=has_deliver,
        has_review=has_review,
    )


__all__ = [
    "_HIGH_RISK_SIGNALS",
    "_TIER_EXPECTATIONS",
    "_TierExpectation",
    "classify_complexity",
    "compute_tier_ceremony_score",
    "get_phase_requirements",
]
