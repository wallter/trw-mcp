"""Tests for PRD-CORE-184 FR04/FR06 — task-type nudge weights + recall policy.

FR04: per-task-type nudge pool weights (workflow/learnings/ceremony/context)
applied in ``resolve_task_profile``. FR06: per-task-type recall policy hint.
Wiring tests prove the resolver consumes the table (not a dead facade).
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import resolve_client_profile
from trw_mcp.models.task_profile import resolve_task_profile
from trw_mcp.models.task_profile_types import (
    _TASK_TYPE_NUDGE_DEFAULTS,
    TaskType,
    task_type_recall_policy,
)


def _weights(task_type: TaskType) -> tuple[int, int, int, int]:
    profile = resolve_client_profile("claude-code")
    resolved = resolve_task_profile(client_profile=profile, task_type=task_type)
    return resolved.nudge_pool_weights


# ── FR04: table presence + invariants ───────────────────────────────────────


def test_nudge_defaults_table_has_all_task_types() -> None:
    from typing import get_args

    assert set(_TASK_TYPE_NUDGE_DEFAULTS) == set(get_args(TaskType))


def test_nudge_defaults_each_sums_to_100() -> None:
    for task_type, weights in _TASK_TYPE_NUDGE_DEFAULTS.items():
        assert sum(weights) == 100, f"{task_type} weights must sum to 100"


# ── FR04: resolver wiring (weights differ by task type) ─────────────────────


def test_resolve_task_profile_populates_task_type() -> None:
    profile = resolve_client_profile("claude-code")
    resolved = resolve_task_profile(client_profile=profile, task_type="coding")
    assert resolved.task_type == "coding"


def test_nudge_pool_coding_vs_research() -> None:
    """FR04 AC: ceremony weight higher for coding; learnings higher for research."""
    coding = _weights("coding")  # (workflow, learnings, ceremony, context)
    research = _weights("research")
    # ceremony dimension (index 2): coding >= research by >= 10pp
    assert coding[2] - research[2] >= 10
    # learnings dimension (index 1): research > coding
    assert research[1] > coding[1]


def test_nudge_pool_rca_learnings_elevated() -> None:
    """FR04 AC: RCA elevates the learnings pool (>= 35)."""
    rca = _weights("rca")
    assert rca[1] >= 35


def test_unknown_task_type_uses_default_weights() -> None:
    """Regression: unknown task type keeps the historical default weights."""
    assert _weights("unknown") == (40, 30, 20, 10)


# ── FR06: recall policy ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("task_type", "expected"),
    [
        ("coding", "similarity"),
        ("rca", "failure_pattern"),
        ("research", "breadth_first"),
        ("docs", "similarity"),
        ("eval", "provenance"),
        ("planning", "structural"),
        ("unknown", "similarity"),
    ],
)
def test_task_type_recall_policy(task_type: TaskType, expected: str) -> None:
    assert task_type_recall_policy(task_type) == expected


def test_resolve_task_profile_populates_recall_policy() -> None:
    profile = resolve_client_profile("claude-code")
    resolved = resolve_task_profile(client_profile=profile, task_type="rca")
    assert resolved.recall_policy == "failure_pattern"
