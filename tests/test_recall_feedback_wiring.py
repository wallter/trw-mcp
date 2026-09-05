"""PRD-CORE-244 FR11 + PRD-FIX-104: the LIVE recall ranker reads the feedback counters.

This file used to import ``trw_memory.lifecycle.scoring.entry_utility`` directly
and assert on it. That proved only that the *unused* implementation worked: the
live ``trw_recall`` ranked through ``trw_mcp.scoring.rank_by_utility`` ->
``trw_mcp.scoring._decay._entry_utility``, a second implementation that read
``q_value``, ``impact``, ``recurrence``, ``access_count``, ``source_type``,
``type``, ``confidence`` and ``expires`` — and never ``helpful_count``,
``unhelpful_count`` or ``recall_count``, the counters ``trw_learn``'s own
docstring credits with feeding decay.

Every test here therefore enters through ``trw_mcp.scoring.rank_by_utility`` or
its config adapter. Each one FAILS against the pre-FR11 tree.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.scoring import rank_by_utility
from trw_mcp.scoring._decay import entry_utility
from trw_mcp.state.memory_adapter import (
    find_entry_by_id,
    store_learning,
    update_access_tracking,
)

_TODAY = date(2026, 9, 3)


def _entry(entry_id: str, **overrides: object) -> dict[str, object]:
    """A ranking-shaped learning dict with every scoring field explicit."""
    base: dict[str, object] = {
        "id": entry_id,
        "summary": "shared summary text",
        "detail": "",
        "tags": [],
        "impact": 0.8,
        "q_value": 0.5,
        "q_observations": 0,
        "recurrence": 1,
        "access_count": 5,
        "recall_count": 0,
        "helpful_count": 0,
        "unhelpful_count": 0,
        "source_type": "agent",
        "type": "pattern",
        "confidence": "verified",
        "status": "active",
        "created": (_TODAY - timedelta(days=10)).isoformat(),
    }
    base.update(overrides)
    return base


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw structure for feedback wiring tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


class TestLiveRankerUsesUnifiedUtility:
    """FR11 acceptance: one implementation, reached by the live ranker."""

    def test_rank_by_utility_uses_unified_entry_utility(self) -> None:
        """The live ranker's score must come from the trw-memory implementation.

        Patching the unified function is the wiring proof: if ``rank_by_utility``
        still called a private trw-mcp copy, the patch would not be observed and
        the ordering would not invert.
        """
        seen: list[str] = []
        import trw_memory.lifecycle.scoring as _unified

        original = _unified.entry_utility

        def _spy(entry: dict[str, object], *args: object, **kwargs: object) -> float:
            seen.append(str(entry.get("id", "")))
            # Invert the natural order so a stale caller cannot pass by accident.
            return 1.0 if entry.get("id") == "L-low" else 0.0

        _unified.entry_utility = _spy  # type: ignore[assignment]
        try:
            ranked = rank_by_utility([_entry("L-high", impact=0.95), _entry("L-low", impact=0.05)], [], 1.0)
        finally:
            _unified.entry_utility = original  # type: ignore[assignment]

        assert seen == ["L-high", "L-low"], "rank_by_utility did not reach the unified implementation"
        assert [e["id"] for e in ranked] == ["L-low", "L-high"]

    def test_trw_mcp_no_longer_defines_its_own_entry_utility(self) -> None:
        """FR11: the duplicate implementation is deleted, not merely bypassed."""
        import trw_mcp.scoring._decay as decay

        assert not hasattr(decay, "_entry_utility")
        assert not hasattr(decay, "_TYPE_HALF_LIFE")


class TestUnifiedUtilityRetainsBothBehaviours:
    """FR11: the merge is a UNION. Each retained behaviour is asserted alone."""

    def test_helpful_count_raises_utility_on_the_live_path(self) -> None:
        """FR11 AC4 — the feedback term the live ranker used to ignore."""
        helpful = _entry("L-helpful", recall_count=5, helpful_count=5)
        unhelpful = _entry("L-unhelpful", recall_count=5, helpful_count=0)

        assert entry_utility(helpful, _TODAY) > entry_utility(unhelpful, _TODAY)

    def test_recall_without_helpful_feedback_decays(self) -> None:
        """recall_count > 0 with helpful_count 0 decays base impact by 0.95**n."""
        recalled = _entry("L-recalled", recall_count=5)
        never_recalled = _entry("L-fresh", recall_count=0)

        assert entry_utility(recalled, _TODAY) < entry_utility(never_recalled, _TODAY)

    def test_expired_entry_is_floored(self) -> None:
        """FR11 AC2 — the expiry floor the trw-memory implementation lacked."""
        expired = _entry("L-expired", expires=(_TODAY - timedelta(days=1)).isoformat())
        assert entry_utility(expired, _TODAY) == pytest.approx(0.01)

    def test_entry_expiring_today_is_not_floored(self) -> None:
        """Day-exclusive boundary, preserved exactly from the pre-merge code."""
        today_expiry = _entry("L-today", expires=_TODAY.isoformat())
        assert entry_utility(today_expiry, _TODAY) > 0.01

    def test_unverified_incident_half_life_is_effectively_infinite(self) -> None:
        """FR11 AC3 — postmortems are not decayed away before the fix is confirmed."""
        aged = _entry(
            "L-incident",
            type="incident",
            confidence="unverified",
            created=(_TODAY - timedelta(days=400)).isoformat(),
        )
        aged_hypothesis = _entry(
            "L-hypothesis",
            type="hypothesis",
            confidence="unverified",
            created=(_TODAY - timedelta(days=400)).isoformat(),
        )
        assert entry_utility(aged, _TODAY) > 0.6
        assert entry_utility(aged_hypothesis, _TODAY) < 0.1

    def test_source_and_access_terms_survive_the_merge(self) -> None:
        """The access-count and human-source boosts from the trw-mcp side."""
        human = _entry("L-human", source_type="human")
        agent = _entry("L-agent", source_type="agent")
        assert entry_utility(human, _TODAY) > entry_utility(agent, _TODAY)

        accessed = _entry("L-accessed", access_count=50)
        unaccessed = _entry("L-unaccessed", access_count=0)
        assert entry_utility(accessed, _TODAY) > entry_utility(unaccessed, _TODAY)

    def test_learning_and_memory_field_vocabularies_score_identically(self) -> None:
        """One function, two serialisations: ``impact``/``importance``, ``source_type``/``source``."""
        learning_shaped = _entry("L-x")
        memory_shaped = dict(learning_shaped)
        memory_shaped["importance"] = memory_shaped.pop("impact")
        memory_shaped["source"] = memory_shaped.pop("source_type")

        assert entry_utility(learning_shaped, _TODAY) == pytest.approx(entry_utility(memory_shaped, _TODAY))


class TestRecallCountIsActuallyRecorded:
    """PRD-FIX-104-FR04: the counter the feedback term reads is really written."""

    def test_recall_count_accumulates_over_multiple_recalls(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-rfw1", "Wiring test entry", "detail here", impact=0.8)

        for _ in range(5):
            update_access_tracking(trw_dir, ["L-rfw1"])

        entry = find_entry_by_id(trw_dir, "L-rfw1")
        assert entry is not None
        assert entry["recall_count"] == 5

    def test_stored_entry_feeds_the_live_ranker(self, trw_dir: Path) -> None:
        """End to end: store, recall five times, then rank through the LIVE path."""
        store_learning(trw_dir, "L-rfw2", "Decay wiring test", "detail here", impact=0.8)
        for _ in range(5):
            update_access_tracking(trw_dir, ["L-rfw2"])

        recalled = find_entry_by_id(trw_dir, "L-rfw2")
        assert recalled is not None
        assert recalled["recall_count"] == 5

        baseline = dict(recalled)
        baseline["recall_count"] = 0
        today = datetime.now(tz=timezone.utc).date()

        assert entry_utility(dict(recalled), today) < entry_utility(baseline, today)
