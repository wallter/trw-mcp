"""Core knowledge topology tests for clustering primitives."""

from __future__ import annotations

import pytest

from tests._knowledge_topology_support import _make_entry
from trw_mcp.state.knowledge_topology import (
    build_cooccurrence_matrix,
    form_jaccard_clusters,
    sanitize_slug,
)


class TestSanitizeSlug:
    """FR04: Normalize tag names to filesystem-safe slugs."""

    def test_already_clean(self) -> None:
        assert sanitize_slug("pydantic-gotchas") == "pydantic-gotchas"

    def test_spaces_to_hyphens(self) -> None:
        assert sanitize_slug("my tag name") == "my-tag-name"

    def test_special_chars_stripped(self) -> None:
        result = sanitize_slug("testing@v2!")
        assert result == "testingv2"

    def test_uppercase_lowercased(self) -> None:
        assert sanitize_slug("MyTag") == "mytag"

    def test_long_name_truncated(self) -> None:
        name = "a" * 100
        result = sanitize_slug(name)
        assert len(result) == 64

    def test_empty_string(self) -> None:
        assert sanitize_slug("") == ""

    def test_hyphens_preserved(self) -> None:
        result = sanitize_slug("test-tag-name")
        assert result == "test-tag-name"

    def test_numbers_preserved(self) -> None:
        result = sanitize_slug("v2-testing")
        assert result == "v2-testing"

    def test_mixed_case_and_spaces(self) -> None:
        result = sanitize_slug("FastAPI Testing")
        assert result == "fastapi-testing"

    def test_exactly_64_chars_not_truncated(self) -> None:
        name = "a" * 64
        result = sanitize_slug(name)
        assert result == name
        assert len(result) == 64

    def test_65_chars_truncated(self) -> None:
        name = "a" * 65
        result = sanitize_slug(name)
        assert len(result) == 64


class TestBuildCooccurrenceMatrix:
    """FR02: Tag co-occurrence counting with frequency filter."""

    def test_basic_cooccurrence(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["a", "b"]) for i in range(5)] + [
            _make_entry(f"L-{i + 5:03d}", tags=["a", "c"]) for i in range(3)
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert ("a", "b") in matrix
        assert matrix[("a", "b")] == 5
        assert ("a", "c") in matrix
        assert matrix[("a", "c")] == 3

    def test_tag_in_only_one_entry_excluded(self) -> None:
        entries = [
            _make_entry("L-001", tags=["common", "rare"]),
            _make_entry("L-002", tags=["common", "other"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        for pair in matrix:
            assert "rare" not in pair

    def test_no_tags_produces_empty_matrix(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=[]) for i in range(5)]
        matrix = build_cooccurrence_matrix(entries)
        assert matrix == {}

    def test_single_tag_entries_no_pairs(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["solo"]) for i in range(10)]
        matrix = build_cooccurrence_matrix(entries)
        assert matrix == {}

    def test_pairs_sorted_alphabetically(self) -> None:
        entries = [
            _make_entry("L-001", tags=["z", "a"]),
            _make_entry("L-002", tags=["z", "a"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert ("a", "z") in matrix
        assert ("z", "a") not in matrix

    def test_empty_entries_list(self) -> None:
        matrix = build_cooccurrence_matrix([])
        assert matrix == {}

    def test_frequency_threshold_boundary(self) -> None:
        entries = [
            _make_entry("L-001", tags=["alpha", "beta"]),
            _make_entry("L-002", tags=["alpha", "beta"]),
        ]
        matrix = build_cooccurrence_matrix(entries)
        assert ("alpha", "beta") in matrix
        assert matrix[("alpha", "beta")] == 2

    def test_three_tags_produces_three_pairs(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["a", "b", "c"]) for i in range(3)]
        matrix = build_cooccurrence_matrix(entries)
        assert ("a", "b") in matrix
        assert ("a", "c") in matrix
        assert ("b", "c") in matrix


class TestFormJaccardClusters:
    """FR03: Jaccard-based clustering with merge and drop logic."""

    def test_two_distinct_clusters(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["pydantic", "testing"]) for i in range(10)] + [
            _make_entry(f"L-{i + 10:03d}", tags=["fastapi", "api"]) for i in range(8)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) == 2
        slugs = {c["slug"] for c in clusters}
        assert len(slugs) == 2

    def test_entries_with_no_tags_skipped(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=[]) for i in range(5)] + [
            _make_entry(f"L-{i + 5:03d}", tags=["testing", "python"]) for i in range(5)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        total_ids = sum(len(c["entry_ids"]) for c in clusters)
        assert total_ids == 5

    def test_all_entries_same_tags_one_cluster(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["testing", "python"]) for i in range(8)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) == 1
        assert len(clusters[0]["entry_ids"]) == 8

    def test_cluster_below_min_size_merged(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["main", "topic"]) for i in range(6)] + [
            _make_entry(f"L-{i + 6:03d}", tags=["main", "small"]) for i in range(2)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        total_ids = sum(len(c["entry_ids"]) for c in clusters)
        assert total_ids == 8

    def test_cluster_output_structure(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["testing"]) for i in range(5)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) >= 1
        cluster = clusters[0]
        assert "slug" in cluster
        assert "tags" in cluster
        assert "entry_ids" in cluster
        assert "entries" in cluster
        assert "avg_importance" in cluster

    def test_avg_importance_computed(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["testing"], importance=float(i) / 10) for i in range(1, 6)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        assert len(clusters) >= 1
        avg = clusters[0]["avg_importance"]
        assert isinstance(avg, float)
        assert 0.0 < float(avg) <= 1.0

    def test_empty_entries_returns_empty(self) -> None:
        clusters = form_jaccard_clusters([], threshold=0.3, min_size=3)
        assert clusters == []

    def test_high_threshold_splits_similar_clusters(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["a", "b"]) for i in range(5)] + [
            _make_entry(f"L-{i + 5:03d}", tags=["c", "d"]) for i in range(5)
        ]
        clusters = form_jaccard_clusters(entries, threshold=1.0, min_size=3)
        total_entries = sum(len(c["entry_ids"]) for c in clusters)
        assert total_entries == 10

    def test_cluster_slug_is_most_common_tag(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["pydantic", "testing"]) for i in range(3)] + [
            _make_entry(f"L-{i + 3:03d}", tags=["pydantic", "fastapi"]) for i in range(2)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.1, min_size=1)
        assert any("pydantic" in str(c["slug"]) for c in clusters)

    def test_tags_in_cluster_are_union(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["a", "b"]) for i in range(4)] + [
            _make_entry(f"L-{i + 4:03d}", tags=["a", "c"]) for i in range(4)
        ]
        clusters = form_jaccard_clusters(entries, threshold=0.1, min_size=3)
        all_tags: list[str] = []
        for cluster in clusters:
            all_tags.extend(cluster["tags"])  # type: ignore[arg-type]
        assert "a" in all_tags

    def test_cluster_entry_ids_match_entries(self) -> None:
        entries = [_make_entry(f"L-{i:03d}", tags=["topic"]) for i in range(5)]
        clusters = form_jaccard_clusters(entries, threshold=0.3, min_size=3)
        for cluster in clusters:
            ids = cluster["entry_ids"]
            mem_entries = cluster["entries"]
            assert len(ids) == len(mem_entries)  # type: ignore[arg-type]


@pytest.mark.unit
class TestJaccardBothEmpty:
    """Line 46: _jaccard with both sets empty returns 0.0 without ZeroDivisionError."""

    def test_both_sets_empty_returns_zero(self) -> None:
        from trw_mcp.state.knowledge_topology import _jaccard

        result = _jaccard(set(), set())
        assert result == 0.0

    def test_one_empty_one_nonempty(self) -> None:
        from trw_mcp.state.knowledge_topology import _jaccard

        assert _jaccard(set(), {"a"}) == 0.0

    def test_identical_nonempty_sets_return_one(self) -> None:
        from trw_mcp.state.knowledge_topology import _jaccard

        assert _jaccard({"x"}, {"x"}) == 1.0
