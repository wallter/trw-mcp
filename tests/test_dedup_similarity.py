"""Tests for the LearningEntry.merged_from field dedup merges write."""

from __future__ import annotations

from trw_mcp.models.learning import LearningEntry


class TestLearningEntryMergedFrom:
    """Tests for the merged_from field added to LearningEntry."""

    def test_default_merged_from_is_empty_list(self) -> None:
        """LearningEntry.merged_from defaults to []."""
        entry = LearningEntry(
            id="L-test01",
            summary="test",
            detail="detail",
        )
        assert entry.merged_from == []

    def test_merged_from_can_be_populated(self) -> None:
        """LearningEntry.merged_from accepts a list of ID strings."""
        entry = LearningEntry(
            id="L-test02",
            summary="test",
            detail="detail",
            merged_from=["L-abc", "L-def"],
        )
        assert entry.merged_from == ["L-abc", "L-def"]
