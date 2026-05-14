"""Tests for dedup similarity helpers and model fields."""

from __future__ import annotations

import math

from tests._dedup_test_support import mock_embed
from trw_mcp.models.learning import LearningEntry
from trw_mcp.state.dedup import cosine_similarity


class TestCosineSimilarity:
    def test_identical_vectors_return_one(self) -> None:
        """Two identical unit vectors → similarity 1.0."""
        v = mock_embed("hello world")
        result = cosine_similarity(v, v)
        assert abs(result - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self) -> None:
        """Two orthogonal vectors → similarity 0.0."""
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        result = cosine_similarity(a, b)
        assert abs(result - 0.0) < 1e-9

    def test_anti_parallel_vectors_return_minus_one(self) -> None:
        """Anti-parallel unit vectors → similarity -1.0."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        result = cosine_similarity(a, b)
        assert abs(result - (-1.0)) < 1e-9

    def test_partial_similarity(self) -> None:
        """Two vectors at 60 degrees → similarity 0.5."""
        a = [1.0, 0.0]
        b = [0.5, math.sqrt(0.75)]
        result = cosine_similarity(a, b)
        assert abs(result - 0.5) < 1e-6

    def test_empty_vectors(self) -> None:
        """Empty vectors → 0.0 (graceful)."""
        result = cosine_similarity([], [])
        assert result == 0.0

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

class TestDistanceToSimilarity:
    """Tests for the _distance_to_similarity helper."""

    def test_zero_distance_is_perfect_similarity(self) -> None:
        from trw_mcp.state.dedup import _distance_to_similarity

        assert abs(_distance_to_similarity(0.0) - 1.0) < 1e-9

    def test_sqrt2_distance_is_zero_similarity(self) -> None:
        """For unit-normalized vectors, L2 distance of sqrt(2) = cosine similarity 0."""
        import math

        from trw_mcp.state.dedup import _distance_to_similarity

        result = _distance_to_similarity(math.sqrt(2.0))
        assert abs(result - 0.0) < 1e-9

    def test_known_distance_to_similarity(self) -> None:
        """distance² = 2(1 - sim), so distance=0.316 → sim ≈ 0.95."""
        import math

        from trw_mcp.state.dedup import _distance_to_similarity

        # For sim=0.95: d² = 2*(1-0.95) = 0.1, d = sqrt(0.1) ≈ 0.3162
        distance = math.sqrt(0.1)
        result = _distance_to_similarity(distance)
        assert abs(result - 0.95) < 1e-6
