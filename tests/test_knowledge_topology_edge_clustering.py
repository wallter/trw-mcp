"""Focused edge-case tests for knowledge_topology clustering algorithms."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests._knowledge_topology_edge_support import _entry, _make_config
from trw_mcp.state.knowledge_topology import (
    _assign_entries_to_clusters,
    _base_result,
    _jaccard,
    _merge_small_clusters,
    build_cooccurrence_matrix,
    form_jaccard_clusters,
)


class TestJaccard:
    """Direct tests for the Jaccard similarity helper."""

    def test_both_empty_returns_zero(self) -> None:
        assert _jaccard(set(), set()) == 0.0

    def test_one_empty_returns_zero(self) -> None:
        assert _jaccard({"a", "b"}, set()) == 0.0
        assert _jaccard(set(), {"a", "b"}) == 0.0

    def test_identical_sets_returns_one(self) -> None:
        s = {"a", "b", "c"}
        assert _jaccard(s, s) == 1.0

    def test_disjoint_sets_returns_zero(self) -> None:
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_subset_returns_correct_ratio(self) -> None:
        result = _jaccard({"a", "b"}, {"a", "b", "c"})
        assert abs(result - 2.0 / 3.0) < 1e-9

    def test_partial_overlap(self) -> None:
        result = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert result == 0.5

    def test_single_element_match(self) -> None:
        assert _jaccard({"x"}, {"x"}) == 1.0

    def test_single_element_no_match(self) -> None:
        assert _jaccard({"x"}, {"y"}) == 0.0


class TestAssignEntriesToClusters:
    """Direct tests for the greedy cluster assignment step."""

    def test_single_entry_creates_one_cluster(self) -> None:
        entries = [_entry("L-001", tags=["a", "b"])]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_list"]) == 1

    def test_all_no_tags_returns_empty(self) -> None:
        entries = [_entry(f"L-{i:03d}", tags=[]) for i in range(5)]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        assert clusters == []

    def test_threshold_zero_disjoint_still_separate(self) -> None:
        entries = [
            _entry("L-001", tags=["a"]),
            _entry("L-002", tags=["b"]),
            _entry("L-003", tags=["c"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.0)
        assert len(clusters) == 3

    def test_threshold_zero_with_overlap_merges(self) -> None:
        entries = [
            _entry("L-001", tags=["a", "b"]),
            _entry("L-002", tags=["a", "c"]),
            _entry("L-003", tags=["a", "d"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.0)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_list"]) == 3

    def test_threshold_one_requires_exact_match(self) -> None:
        entries = [
            _entry("L-001", tags=["a", "b"]),
            _entry("L-002", tags=["a", "b"]),
            _entry("L-003", tags=["a", "c"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=1.0)
        assert len(clusters) == 2

    def test_cluster_tag_set_widens_on_add(self) -> None:
        entries = [
            _entry("L-001", tags=["a", "b"]),
            _entry("L-002", tags=["a", "c"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        assert len(clusters) == 1
        assert clusters[0]["tag_set"] == {"a", "b", "c"}

    def test_mixed_tagged_and_untagged(self) -> None:
        entries = [
            _entry("L-001", tags=["a"]),
            _entry("L-002", tags=[]),
            _entry("L-003", tags=["a"]),
        ]
        clusters = _assign_entries_to_clusters(entries, similarity_threshold=0.3)
        total = sum(len(c["entry_list"]) for c in clusters)
        assert total == 2


class TestMergeSmallClusters:
    """Direct tests for the undersized-cluster merge/drop pass."""

    def _make_cluster(self, tags: set[str], count: int) -> dict[str, Any]:
        return {
            "tag_set": tags,
            "entry_list": [_entry(f"L-{i:03d}", tags=list(tags)) for i in range(count)],
        }

    def test_all_meet_min_size_unchanged(self) -> None:
        clusters = [
            self._make_cluster({"a"}, 5),
            self._make_cluster({"b"}, 5),
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 2

    def test_undersized_merged_into_closest(self) -> None:
        clusters = [
            self._make_cluster({"a", "b"}, 5),
            self._make_cluster({"a", "c"}, 1),
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 1
        assert len(result[0]["entry_list"]) == 6

    def test_all_undersized_dropped(self) -> None:
        clusters = [self._make_cluster({"a"}, 1)]
        result = _merge_small_clusters(clusters, min_size=5)
        assert result == []

    def test_two_undersized_merge_then_survive(self) -> None:
        clusters = [
            self._make_cluster({"a", "b"}, 2),
            self._make_cluster({"a", "c"}, 2),
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 1
        assert len(result[0]["entry_list"]) == 4

    def test_cascading_merge(self) -> None:
        clusters = [
            self._make_cluster({"x"}, 10),
            self._make_cluster({"x", "y"}, 1),
            self._make_cluster({"x", "z"}, 1),
        ]
        result = _merge_small_clusters(clusters, min_size=3)
        assert len(result) == 1
        assert len(result[0]["entry_list"]) == 12

    def test_min_size_one_keeps_everything(self) -> None:
        clusters = [
            self._make_cluster({"a"}, 1),
            self._make_cluster({"b"}, 1),
        ]
        result = _merge_small_clusters(clusters, min_size=1)
        assert len(result) == 2

    def test_empty_clusters_list(self) -> None:
        result = _merge_small_clusters([], min_size=3)
        assert result == []


class TestBaseResult:
    """Verify _base_result returns all expected keys."""

    def test_all_keys_present(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        result = _base_result(42, config, trw_dir, threshold_met=True, dry_run=False)
        assert result["threshold_met"] is True
        assert result["entry_count"] == 42
        assert result["threshold"] == config.knowledge_sync_threshold
        assert result["topics_generated"] == 0
        assert result["entries_clustered"] == 0
        assert result["dry_run"] is False
        assert result["errors"] == []
        assert "output_dir" in result

    def test_dry_run_flag_propagated(self, tmp_path: Path) -> None:
        config = _make_config(tmp_path)
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(exist_ok=True)
        result = _base_result(0, config, trw_dir, threshold_met=False, dry_run=True)
        assert result["dry_run"] is True
        assert result["threshold_met"] is False


class TestCooccurrenceEdgeCases:
    """Edge cases not covered in the main test file."""

    def test_duplicate_tags_within_entry_deduplicated(self) -> None:
        entries = [
            _entry("L-001", tags=["a", "a", "b"]),
            _entry("L-002", tags=["a", "b"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert ("a", "b") in matrix
        assert matrix[("a", "b")] == 2

    def test_many_tags_produces_all_pairs(self) -> None:
        entries = [
            _entry("L-001", tags=["a", "b", "c", "d"]),
            _entry("L-002", tags=["a", "b", "c", "d"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert len(matrix) == 6

    def test_one_entry_never_meets_freq_threshold(self) -> None:
        entries = [_entry("L-001", tags=["a", "b", "c"])]
        matrix = build_cooccurrence_matrix(entries)
        assert matrix == {}


class TestFormJaccardClustersEdge:
    """Additional edge cases for the full clustering pipeline."""

    def test_min_size_one_keeps_singletons(self) -> None:
        entries = [
            _entry("L-001", tags=["a"]),
            _entry("L-002", tags=["b"]),
            _entry("L-003", tags=["c"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=1.0, min_size=1)
        assert len(clusters) == 3

    def test_all_no_tags_returns_empty(self) -> None:
        entries = [_entry(f"L-{i:03d}", tags=[]) for i in range(10)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=2)
        assert clusters == []

    def test_avg_importance_is_mean_of_entries(self) -> None:
        entries = [
            _entry("L-001", tags=["x"], importance=0.2),
            _entry("L-002", tags=["x"], importance=0.8),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=1)
        assert len(clusters) == 1
        assert clusters[0]["avg_importance"] == 0.5

    def test_tags_in_output_are_sorted(self) -> None:
        entries = [
            _entry("L-001", tags=["z", "a", "m"]),
            _entry("L-002", tags=["z", "a", "m"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=1)
        assert list(clusters[0]["tags"]) == ["a", "m", "z"]  # type: ignore[arg-type]

    def test_entry_ids_match_input_ids(self) -> None:
        entries = [
            _entry("L-aaa", tags=["x"]),
            _entry("L-bbb", tags=["x"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=1)
        ids = set(clusters[0]["entry_ids"])  # type: ignore[arg-type]
        assert ids == {"L-aaa", "L-bbb"}

    def test_large_min_size_drops_everything(self) -> None:
        entries = [_entry(f"L-{i:03d}", tags=["x"]) for i in range(5)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=100)
        assert clusters == []

    def test_slug_uses_sanitized_most_common_tag(self) -> None:
        entries = [
            _entry("L-001", tags=["My Tag!", "other"]),
            _entry("L-002", tags=["My Tag!", "other"]),
            _entry("L-003", tags=["My Tag!"]),
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.1, min_size=1)
        assert clusters[0]["slug"] == "my-tag"
