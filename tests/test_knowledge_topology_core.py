"""Core knowledge topology tests for clustering primitives."""

from __future__ import annotations

import pytest

from tests._knowledge_topology_support import _make_entry
from trw_mcp.state.knowledge_topology import (
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
