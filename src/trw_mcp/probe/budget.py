"""Per-session probe budget accounting (PRD-CORE-144 FR-07).

Belongs to the ``probe`` facade. Re-exported from ``probe/__init__.py``.

The budget is keyed to the session's ``PlanningMode`` (a Concept-1 /
SCALE-001 enum that is not yet landed at HEAD — we accept its string name).
Budget is decremented BEFORE a probe spawns (FR-07 A1) so a crash mid-probe
never silently leaves the session over-budget. Exceeding the budget raises
the typed :class:`ProbeBudgetExhausted` (FR-07 A3), never a string error.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

#: Probe budget per Concept-1 ``PlanningMode`` (FR-07 table). Keys are the
#: mode name strings; SCALE-001 will pass its enum's ``.name``/``.value``.
PLANNING_MODE_BUDGETS: dict[str, int] = {
    "DIRECT": 0,
    "DUAL_DRAFT": 1,
    "TRIANGULATED": 2,
    "TRIANGULATED_WITH_PROBE": 3,
}

#: Mode used when a caller supplies an unknown planning mode. Unknown modes
#: get the most permissive non-zero budget's floor (DUAL_DRAFT=1) so an
#: integration that has not yet wired PlanningMode still gets one probe.
_DEFAULT_UNKNOWN_BUDGET = 1


class ProbeBudgetExhausted(RuntimeError):
    """Raised when a session's probe budget is exhausted (FR-07 / US-05)."""

    def __init__(self, *, planning_mode: str, total: int, remaining: int = 0) -> None:
        self.planning_mode = planning_mode
        self.total = total
        self.remaining = remaining
        self.override_hint = "set TRW_PROBE_BUDGET_OVERRIDE=1 to bypass (writes an audit record)"
        super().__init__(
            f"probe budget exhausted for planning_mode={planning_mode} "
            f"(total={total}, remaining={remaining}); {self.override_hint}"
        )


def budget_for_mode(planning_mode: str) -> int:
    """Resolve the probe budget for a planning mode (unknown -> default)."""
    return PLANNING_MODE_BUDGETS.get(planning_mode, _DEFAULT_UNKNOWN_BUDGET)


class ProbeBudget:
    """Mutable per-session probe budget tracker.

    One instance per run/session. ``consume`` is called BEFORE spawning a
    probe; if no budget remains (and no override) it raises
    :class:`ProbeBudgetExhausted`.
    """

    def __init__(self, planning_mode: str) -> None:
        self.planning_mode = planning_mode
        self.total = budget_for_mode(planning_mode)
        self.used = 0
        self.by_hypothesis_id: dict[str, int] = {}

    @property
    def remaining(self) -> int:
        """Probes still available (never negative)."""
        return max(0, self.total - self.used)

    def consume(self, *, hypothesis_id: str | None = None, override: bool = False) -> bool:
        """Reserve one probe slot BEFORE spawning (FR-07 A1).

        Returns ``True`` when the consumption used an operator override on an
        exhausted budget (so the caller can stamp ``budget_override=True`` on
        the result — FR-07 A2). Raises :class:`ProbeBudgetExhausted` when the
        budget is exhausted and no override is active.
        """
        if self.remaining <= 0:
            if not override:
                logger.warning(
                    "probe_budget_exhausted",
                    component="probe.budget",
                    op="consume",
                    outcome="blocked",
                    planning_mode=self.planning_mode,
                    total=self.total,
                )
                raise ProbeBudgetExhausted(
                    planning_mode=self.planning_mode,
                    total=self.total,
                    remaining=0,
                )
            logger.warning(
                "probe_budget_override",
                component="probe.budget",
                op="consume",
                outcome="override",
                planning_mode=self.planning_mode,
                total=self.total,
            )
            self.used += 1
            if hypothesis_id:
                self.by_hypothesis_id[hypothesis_id] = self.by_hypothesis_id.get(hypothesis_id, 0) + 1
            return True
        self.used += 1
        if hypothesis_id:
            self.by_hypothesis_id[hypothesis_id] = self.by_hypothesis_id.get(hypothesis_id, 0) + 1
        return False


__all__ = [
    "PLANNING_MODE_BUDGETS",
    "ProbeBudget",
    "ProbeBudgetExhausted",
    "budget_for_mode",
]
