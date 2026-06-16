"""FR-07 — per-session probe budget (PRD-CORE-144)."""

from __future__ import annotations

import pytest

from trw_mcp.probe.budget import (
    PLANNING_MODE_BUDGETS,
    ProbeBudget,
    ProbeBudgetExhausted,
    budget_for_mode,
)


def test_per_mode_limits_match_table() -> None:
    # FR-07 table.
    assert PLANNING_MODE_BUDGETS == {
        "DIRECT": 0,
        "DUAL_DRAFT": 1,
        "TRIANGULATED": 2,
        "TRIANGULATED_WITH_PROBE": 3,
    }


def test_consume_decrements_before_spawn() -> None:
    # FR-07 A1: budget decremented before probe spawn.
    budget = ProbeBudget("TRIANGULATED")
    assert budget.remaining == 2
    budget.consume(hypothesis_id="H1")
    assert budget.used == 1
    assert budget.remaining == 1
    assert budget.by_hypothesis_id == {"H1": 1}


def test_exhaustion_raises_typed_exception() -> None:
    # FR-07 A3: exhaustion raises typed exception, not string error.
    budget = ProbeBudget("DUAL_DRAFT")  # budget = 1
    budget.consume()
    with pytest.raises(ProbeBudgetExhausted) as exc:
        budget.consume()
    assert exc.value.planning_mode == "DUAL_DRAFT"
    assert exc.value.remaining == 0
    assert "override" in exc.value.override_hint.lower()


def test_direct_mode_zero_budget_blocks_first_probe() -> None:
    budget = ProbeBudget("DIRECT")
    assert budget.total == 0
    with pytest.raises(ProbeBudgetExhausted):
        budget.consume()


def test_override_consumes_past_exhaustion_and_flags() -> None:
    # FR-07 A2: override sets evidence.budget_override=True (signalled here as
    # the consume() return value the harness stamps onto the result).
    budget = ProbeBudget("DUAL_DRAFT")
    assert budget.consume() is False  # within budget -> not an override
    used_override = budget.consume(override=True)
    assert used_override is True
    assert budget.used == 2


def test_unknown_mode_gets_default_budget() -> None:
    assert budget_for_mode("WHATEVER") == 1
    budget = ProbeBudget("WHATEVER")
    assert budget.total == 1
