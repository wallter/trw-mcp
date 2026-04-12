"""Reward resolution helpers extracted from ``_correlation.py``.

Keeps the correlation module focused on correlation/update flow while preserving
the existing scoring public contracts through re-exports in ``_correlation.py``.
"""

from __future__ import annotations

import math

import structlog

from trw_mcp.models.run import EventType
from trw_mcp.scoring._utils import get_config

logger = structlog.get_logger(__name__)

REWARD_MAP: dict[str, float] = {
    EventType.TESTS_PASSED: 0.8,
    EventType.TESTS_FAILED: -0.3,
    EventType.TASK_COMPLETE: 0.5,
    EventType.PHASE_GATE_PASSED: 1.0,
    EventType.PHASE_GATE_FAILED: -0.5,
    EventType.WAVE_VALIDATION_PASSED: 0.7,
    EventType.SHARD_COMPLETE: 0.6,
    EventType.REFLECTION_COMPLETE: 0.4,
    EventType.COMPLIANCE_PASSED: 0.5,
    EventType.FILE_MODIFIED: 0.2,
    EventType.PRD_APPROVED: 0.7,
    EventType.WAVE_COMPLETE: 0.8,
    EventType.DELIVER_COMPLETE: 1.0,
    EventType.BUILD_PASSED: 0.6,
    EventType.BUILD_FAILED: -0.4,
}

EVENT_ALIASES: dict[str, str | float | None] = {
    EventType.SHARD_COMPLETED: EventType.SHARD_COMPLETE,
    EventType.SHARD_STARTED: None,
    EventType.WAVE_VALIDATED: EventType.WAVE_VALIDATION_PASSED,
    EventType.WAVE_COMPLETED: EventType.WAVE_COMPLETE,
    EventType.PHASE_CHECK: None,
    EventType.PHASE_ENTER: None,
    EventType.PHASE_REVERT: -0.3,
    EventType.RUN_INIT: None,
    EventType.RUN_RESUMED: None,
    EventType.SESSION_START: None,
    EventType.PRD_STATUS_CHANGE: None,
    EventType.PRD_CREATED: 0.3,
    EventType.TEST_RUN: None,
    EventType.CHECKPOINT: 0.1,
    EventType.REFLECTION_COMPLETED: EventType.REFLECTION_COMPLETE,
    EventType.COMPLIANCE_CHECK: None,
}


def _resolve_test_run_reward(
    event_data: dict[str, object],
) -> tuple[float | None, str]:
    """Resolve test_run event to tests_passed or tests_failed."""
    passed = event_data.get("passed")
    if passed is True or str(passed).lower() == "true":
        return REWARD_MAP.get(EventType.TESTS_PASSED), EventType.TESTS_PASSED
    return REWARD_MAP.get(EventType.TESTS_FAILED), EventType.TESTS_FAILED


def _resolve_prd_status_change_reward(
    event_data: dict[str, object],
) -> tuple[float | None, str]:
    """Resolve prd_status_change event."""
    new_status = str(event_data.get("new_status", "")).lower()
    if new_status == "approved":
        return REWARD_MAP.get(EventType.PRD_APPROVED), EventType.PRD_APPROVED
    return None, EventType.PRD_STATUS_CHANGE


def _resolve_compliance_check_reward(
    event_data: dict[str, object],
) -> tuple[float | None, str]:
    """Resolve compliance_check event based on score."""
    score = event_data.get("score")
    if score is not None:
        try:
            if float(str(score)) >= 0.8:
                return REWARD_MAP.get(EventType.COMPLIANCE_PASSED), EventType.COMPLIANCE_PASSED
        except (ValueError, TypeError):
            logger.debug("compliance_score_parse_failed", exc_info=True)
    return None, EventType.COMPLIANCE_CHECK


def _resolve_data_aware_routing(
    event_type: str,
    event_data: dict[str, object],
) -> tuple[float | None, str] | None:
    """Try data-aware routing for composite events."""
    if event_type == EventType.TEST_RUN:
        return _resolve_test_run_reward(event_data)
    if event_type == EventType.PRD_STATUS_CHANGE:
        return _resolve_prd_status_change_reward(event_data)
    if event_type == EventType.COMPLIANCE_CHECK:
        return _resolve_compliance_check_reward(event_data)
    return None


def _resolve_alias_reward(event_type: str) -> tuple[float | None, str] | None:
    """Resolve via EVENT_ALIASES."""
    alias = EVENT_ALIASES.get(event_type)
    if alias is None and event_type in EVENT_ALIASES:
        return None, event_type
    if isinstance(alias, (int, float)):
        return float(alias), event_type
    if isinstance(alias, str):
        mapped_reward = REWARD_MAP.get(alias)
        if mapped_reward is not None:
            return mapped_reward, alias
    return None


def _resolve_event_reward(
    event_type: str,
    event_data: dict[str, object] | None = None,
) -> tuple[float | None, str]:
    """Resolve an event type to a reward value and canonical label."""
    reward = REWARD_MAP.get(event_type)
    if reward is not None:
        return reward, event_type

    if event_data:
        routed = _resolve_data_aware_routing(event_type, event_data)
        if routed is not None:
            return routed

    aliased = _resolve_alias_reward(event_type)
    if aliased is not None:
        return aliased

    cfg = get_config()
    if any(keyword in event_type.lower() for keyword in cfg.scoring_error_keywords):
        return cfg.scoring_error_fallback_reward, event_type

    return None, event_type


def compute_composite_outcome(
    *,
    rework_rate: float = 0.0,
    p0_defect_count: int = 0,
    velocity_tasks: float = 0.0,
    learning_rate: float = 0.0,
    weight_rework: float = -2.0,
    weight_p0_defects: float = -1.5,
    weight_velocity: float = 0.5,
    weight_learning_rate: float = 0.3,
) -> float:
    """Compute composite outcome score respecting TRW value hierarchy."""
    return (
        weight_rework * rework_rate
        + weight_p0_defects * p0_defect_count
        + weight_velocity * velocity_tasks
        + weight_learning_rate * learning_rate
    )


def sigmoid_normalize(score: float, steepness: float = 1.0) -> float:
    """Map composite outcome score to [0, 1] via sigmoid."""
    return 1.0 / (1.0 + math.exp(-steepness * score))


__all__ = [
    "EVENT_ALIASES",
    "REWARD_MAP",
    "_resolve_event_reward",
    "compute_composite_outcome",
    "sigmoid_normalize",
]
