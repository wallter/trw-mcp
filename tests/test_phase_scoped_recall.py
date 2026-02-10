"""Tests for phase-scoped recall scoring (PRD-CORE-017 Step 2.5).

Validates phase bonus behavior in rank_by_utility and config defaults.
"""

from __future__ import annotations

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import rank_by_utility


def _make_entry(
    summary: str,
    phase_scope: str | None = None,
    impact: float = 0.5,
    q_value: float = 0.5,
) -> dict[str, object]:
    """Create a minimal learning entry dict for testing."""
    entry: dict[str, object] = {
        "id": f"L-{summary[:8]}",
        "summary": summary,
        "detail": f"Detail for {summary}",
        "tags": [],
        "impact": impact,
        "q_value": q_value,
        "q_observations": 5,
        "recurrence": 1,
    }
    if phase_scope is not None:
        entry["phase_scope"] = phase_scope
    return entry


def _ranked_ids(entries: list[dict[str, object]]) -> list[str]:
    """Extract ID strings from a ranked entry list."""
    return [str(e["id"]) for e in entries]


class TestPhaseBonus:
    """Phase bonus affects ranking order."""

    def test_matching_phase_ranks_higher(self) -> None:
        """Entry with matching phase_scope ranks first."""
        matching = _make_entry("implement tip", phase_scope="implement")
        non_matching = _make_entry("research tip", phase_scope="research")
        global_entry = _make_entry("global tip", phase_scope=None)

        ranked = rank_by_utility(
            [non_matching, global_entry, matching], [], 0.3, current_phase="implement",
        )

        assert _ranked_ids(ranked)[0] == str(matching["id"])

    def test_global_above_non_matching(self) -> None:
        """Global entries (no phase_scope) rank above non-matching phase entries."""
        non_matching = _make_entry("research tip", phase_scope="research")
        global_entry = _make_entry("global tip", phase_scope=None)

        ranked = rank_by_utility(
            [non_matching, global_entry], [], 0.3, current_phase="implement",
        )

        assert _ranked_ids(ranked)[0] == str(global_entry["id"])

    def test_no_phase_no_bonus(self) -> None:
        """Without current_phase, no bonus applied -- ordering by utility only."""
        a = _make_entry("tip A", phase_scope="implement", impact=0.8, q_value=0.8)
        b = _make_entry("tip B", phase_scope="research", impact=0.9, q_value=0.9)

        ranked_explicit_none = rank_by_utility([a, b], [], 0.3, current_phase=None)
        ranked_default = rank_by_utility([a, b], [], 0.3)

        assert _ranked_ids(ranked_explicit_none) == _ranked_ids(ranked_default)

    def test_empty_phase_scope_treated_as_global(self) -> None:
        """Empty string phase_scope receives the same bonus as None (global)."""
        empty_scope = _make_entry("empty scope", phase_scope="")
        none_scope = _make_entry("none scope", phase_scope=None)

        ranked = rank_by_utility(
            [empty_scope, none_scope], [], 0.3, current_phase="implement",
        )

        ids = _ranked_ids(ranked)
        assert len(ids) == 2
        # Neither entry should be penalized -- both are global, so input order is preserved
        assert ids[0] == str(empty_scope["id"])

    def test_phase_bonus_config_defaults(self) -> None:
        """Config provides expected default bonus values."""
        cfg = TRWConfig()

        assert cfg.phase_bonus_matching == 0.15
        assert cfg.phase_bonus_global == 0.0
        assert cfg.phase_bonus_nonmatching == -0.05
