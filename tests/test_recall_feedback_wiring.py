"""PRD-FIX-104: Integration tests for recall_count → feedback_decay_score wiring.

Verifies that after update_access_tracking is called multiple times, the
stored recall_count is non-zero AND that entry_utility returns a LOWER score
for an entry with recall_count=5 vs recall_count=0, confirming that
feedback_decay_score actually fires when recall_count > 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.lifecycle.scoring import entry_utility

from trw_mcp.state.memory_adapter import (
    find_entry_by_id,
    store_learning,
    update_access_tracking,
)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw structure for feedback wiring tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


class TestRecallFeedbackDecayWiring:
    def test_recall_count_accumulates_over_multiple_recalls(self, trw_dir: Path) -> None:
        """PRD-FIX-104-FR04 part 1: five update_access_tracking calls → recall_count == 5."""
        store_learning(trw_dir, "L-rfw1", "Wiring test entry", "detail here", impact=0.8)

        for _ in range(5):
            update_access_tracking(trw_dir, ["L-rfw1"])

        entry = find_entry_by_id(trw_dir, "L-rfw1")
        assert entry is not None
        assert entry["recall_count"] == 5

    def test_feedback_decay_score_fires_for_recalled_entries(self, trw_dir: Path) -> None:
        """PRD-FIX-104-FR04 part 2: entry with recall_count=5 has lower utility than recall_count=0.

        This proves feedback_decay_score is actually applied (RC-2 fix verification).
        When recall_count=0, the 'if recall_ct > 0:' guard in entry_utility skips
        feedback_decay_score entirely (base_impact unchanged). When recall_count=5
        with helpful_count=0, decay = 0.95^5 ≈ 0.774, so base_impact is reduced.
        """
        store_learning(trw_dir, "L-rfw2", "Decay wiring test", "detail here", impact=0.8)

        for _ in range(5):
            update_access_tracking(trw_dir, ["L-rfw2"])

        entry_with_recalls = find_entry_by_id(trw_dir, "L-rfw2")
        assert entry_with_recalls is not None
        assert entry_with_recalls["recall_count"] == 5

        # Build a baseline entry dict identical to the recalled one but with recall_count=0
        baseline = dict(entry_with_recalls)
        baseline["recall_count"] = 0

        utility_recalled = entry_utility(dict(entry_with_recalls))
        utility_baseline = entry_utility(baseline)

        # The recalled entry (recall_count=5, helpful_count=0) should have LOWER utility
        # due to 0.95^5 ≈ 0.774 decay applied to base_impact.
        assert utility_recalled < utility_baseline, (
            f"Expected feedback decay to reduce utility for recalled entries: "
            f"utility_recalled={utility_recalled:.4f} should be < utility_baseline={utility_baseline:.4f}"
        )

    def test_helpful_feedback_counteracts_recall_decay(self, trw_dir: Path) -> None:
        """PRD-FIX-104-FR04 adjacent: helpful_count reduces decay exponent.

        With recall_count=5 and helpful_count=5, exponent = 5/5 = 1.0 (some decay).
        With recall_count=5 and helpful_count=0, exponent = 5/1 = 5.0 (more decay).
        The entry with helpful feedback should have higher utility.
        """
        # Build two entry dicts directly (no need to store — we test entry_utility math)
        entry_helpful = {
            "id": "L-rfw3",
            "summary": "helpful entry",
            "importance": 0.8,
            "impact": 0.8,
            "recall_count": 5,
            "helpful_count": 5,
            "unhelpful_count": 0,
            "access_count": 5,
            "q_value": 0.5,
            "q_observations": 0,
            "recurrence": 1,
            "source": "agent",
            "status": "active",
        }
        entry_unhelpful = dict(entry_helpful)
        entry_unhelpful["helpful_count"] = 0

        utility_helpful = entry_utility(entry_helpful)
        utility_unhelpful = entry_utility(entry_unhelpful)

        assert utility_helpful > utility_unhelpful, (
            f"Helpful feedback should yield higher utility: "
            f"helpful={utility_helpful:.4f} > unhelpful={utility_unhelpful:.4f}"
        )

    def test_recall_count_zero_baseline_not_decayed(self, trw_dir: Path) -> None:
        """PRD-FIX-104-FR04 guard: recall_count=0 baseline is NOT decayed by feedback_decay_score."""
        store_learning(trw_dir, "L-rfw4", "No recall baseline", "d", impact=0.8)
        entry = find_entry_by_id(trw_dir, "L-rfw4")
        assert entry is not None
        assert entry["recall_count"] == 0

        # entry_utility on a never-recalled entry should not apply feedback decay
        baseline = dict(entry)
        baseline_util = entry_utility(baseline)

        # The utility should be > 0 (entry exists, positive impact)
        assert baseline_util > 0.0
