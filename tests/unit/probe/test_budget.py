"""FR-07 — per-session probe budget (PRD-CORE-144)."""

from __future__ import annotations

import pytest

from trw_mcp.probe.budget import (
    DEFAULT_PROBE_BUDGET,
    ProbeBudget,
    ProbeBudgetExhausted,
)

pytestmark = pytest.mark.unit


def test_default_budget_is_three() -> None:
    assert DEFAULT_PROBE_BUDGET == 3
    assert ProbeBudget().total == 3


def test_consume_decrements_before_spawn() -> None:
    # FR-07 A1: budget decremented before probe spawn.
    budget = ProbeBudget()
    assert budget.remaining == 3
    budget.consume(hypothesis_id="H1")
    assert budget.used == 1
    assert budget.remaining == 2
    assert budget.by_hypothesis_id == {"H1": 1}


def test_exhaustion_raises_typed_exception() -> None:
    # FR-07 A3: exhaustion raises typed exception, not string error.
    budget = ProbeBudget(1)
    budget.consume()
    with pytest.raises(ProbeBudgetExhausted) as exc:
        budget.consume()
    assert exc.value.total == 1
    assert exc.value.remaining == 0
    assert "override" in exc.value.override_hint.lower()


def test_zero_budget_blocks_first_probe() -> None:
    budget = ProbeBudget(0)
    with pytest.raises(ProbeBudgetExhausted):
        budget.consume()


def test_override_consumes_past_exhaustion_and_flags() -> None:
    # FR-07 A2: override sets evidence.budget_override=True (signalled here as
    # the consume() return value the harness stamps onto the result).
    budget = ProbeBudget(1)
    assert budget.consume() is False  # within budget -> not an override
    used_override = budget.consume(override=True)
    assert used_override is True
    assert budget.used == 2
