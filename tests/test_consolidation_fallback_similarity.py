"""Fallback summarization and similarity helper tests for consolidation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trw_mcp.state.consolidation import _mean_pairwise_similarity, _summarize_cluster_fallback

from ._consolidation_test_helpers import make_cluster, make_vec


class TestSummarizeClusterFallback:
    """FR05: _summarize_cluster_fallback selects best entry without LLM."""

    def test_returns_longest_summary_plus_detail_entry(self) -> None:
        """Selects the entry with the longest combined summary+detail."""
        cluster = [
            {"id": "e1", "summary": "short", "detail": "x"},
            {"id": "e2", "summary": "much longer summary here", "detail": "and detail too"},
            {"id": "e3", "summary": "mid", "detail": "middle"},
        ]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "much longer summary here"
        assert result["detail"] == "and detail too"

    def test_returns_dict_with_summary_and_detail_keys(self) -> None:
        """Result always has 'summary' and 'detail' keys."""
        cluster = make_cluster(3)
        result = _summarize_cluster_fallback(cluster)
        assert "summary" in result
        assert "detail" in result

    def test_missing_fields_default_to_empty_string(self) -> None:
        """Entries missing summary/detail fields use empty strings."""
        cluster = [
            {"id": "e1"},
            {"id": "e2", "summary": "some content", "detail": "more content here"},
            {"id": "e3"},
        ]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "some content"

    def test_single_entry_cluster(self) -> None:
        """Works with a single-entry cluster."""
        cluster = [{"id": "e1", "summary": "only one", "detail": "entry"}]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "only one"

    def test_summary_and_detail_are_strings(self) -> None:
        """Return values are always strings."""
        cluster = make_cluster(3)
        result = _summarize_cluster_fallback(cluster)
        assert isinstance(result["summary"], str)
        assert isinstance(result["detail"], str)

class TestMeanPairwiseSimilarity:
    """FR06: _mean_pairwise_similarity computes mean cosine similarity."""

    def test_single_entry_returns_zero(self) -> None:
        """Single-entry cluster has no pairs — returns 0.0."""
        cluster = [{"id": "e1", "summary": "s", "detail": "d"}]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[make_vec(1.0, 0.0, 0.0)]):
            result = _mean_pairwise_similarity(cluster)
        assert result == 0.0

    def test_empty_cluster_returns_zero(self) -> None:
        """Empty cluster returns 0.0."""
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[]):
            result = _mean_pairwise_similarity([])
        assert result == 0.0

    def test_identical_vectors_returns_one(self) -> None:
        """Identical unit vectors → mean similarity = 1.0."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
        ]
        vecs = [make_vec(1.0, 0.0, 0.0), make_vec(1.0, 0.0, 0.0)]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(1.0)

    def test_orthogonal_vectors_returns_zero(self) -> None:
        """Orthogonal unit vectors → mean similarity = 0.0."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
        ]
        vecs = [make_vec(1.0, 0.0, 0.0), make_vec(0.0, 1.0, 0.0)]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_none_vectors_filtered_out(self) -> None:
        """None vectors in batch are filtered before computing similarity."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
            {"id": "e3", "summary": "s3", "detail": "d3"},
        ]
        vecs: list[list[float] | None] = [
            make_vec(1.0, 0.0, 0.0),
            None,
            make_vec(1.0, 0.0, 0.0),
        ]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(1.0)

    def test_all_none_vectors_returns_zero(self) -> None:
        """All None vectors → returns 0.0 without raising."""
        cluster = [
            {"id": "e1", "summary": "s1", "detail": "d1"},
            {"id": "e2", "summary": "s2", "detail": "d2"},
        ]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=[None, None]):
            result = _mean_pairwise_similarity(cluster)
        assert result == 0.0

class TestSummarizeClusterFallbackEdgeCases:
    """Edge cases for _summarize_cluster_fallback selection logic."""

    def test_tiebreaker_selects_first_max_length_entry(self) -> None:
        """When multiple entries tie on length, max() selects the first."""
        cluster = [
            {"id": "e1", "summary": "12345", "detail": ""},
            {"id": "e2", "summary": "abcde", "detail": ""},  # same length
        ]
        result = _summarize_cluster_fallback(cluster)
        # Python's max() returns the first maximal element
        assert result["summary"] == "12345"

    def test_detail_contributes_to_length_selection(self) -> None:
        """Entry with shorter summary but longer detail wins by total length."""
        cluster = [
            {"id": "e1", "summary": "short", "detail": "x"},  # len = 6
            {"id": "e2", "summary": "s", "detail": "very long detail here"},  # len = 22
        ]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == "s"
        assert result["detail"] == "very long detail here"

    def test_all_entries_empty_returns_empty_strings(self) -> None:
        """Cluster where all entries have empty summary and detail returns empty."""
        cluster = [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}]
        result = _summarize_cluster_fallback(cluster)
        assert result["summary"] == ""
        assert result["detail"] == ""

class TestMeanPairwiseSimilarityEdgeCases:
    """Edge cases for _mean_pairwise_similarity computation."""

    def test_three_entries_computes_three_pairs(self) -> None:
        """Three entries produce 3 pairwise comparisons."""
        cluster = [
            {"id": "e1", "summary": "a", "detail": ""},
            {"id": "e2", "summary": "b", "detail": ""},
            {"id": "e3", "summary": "c", "detail": ""},
        ]
        # 3 identical vectors -> all pairs have sim=1.0 -> mean=1.0
        vecs = [make_vec(1.0, 0.0, 0.0)] * 3
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(1.0)

    def test_one_none_vector_with_three_entries_uses_two_valid(self) -> None:
        """One None among three entries: only the 2 valid vectors form 1 pair."""
        cluster = [
            {"id": "e1", "summary": "a", "detail": ""},
            {"id": "e2", "summary": "b", "detail": ""},
            {"id": "e3", "summary": "c", "detail": ""},
        ]
        vecs: list[list[float] | None] = [
            make_vec(1.0, 0.0, 0.0),
            None,
            make_vec(0.0, 1.0, 0.0),
        ]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        # cos([1,0,0], [0,1,0]) = 0.0
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_mixed_similarity_returns_mean(self) -> None:
        """Mean of mixed pair similarities is computed correctly."""
        cluster = [
            {"id": "e1", "summary": "a", "detail": ""},
            {"id": "e2", "summary": "b", "detail": ""},
            {"id": "e3", "summary": "c", "detail": ""},
        ]
        # e1=[1,0,0], e2=[1,0,0], e3=[0,1,0]
        # pairs: (e1,e2)=1.0, (e1,e3)=0.0, (e2,e3)=0.0
        # mean = 1.0/3 ~= 0.333
        vecs = [
            make_vec(1.0, 0.0, 0.0),
            make_vec(1.0, 0.0, 0.0),
            make_vec(0.0, 1.0, 0.0),
        ]
        with patch("trw_mcp.state.memory_adapter.embed_text_batch", return_value=vecs):
            result = _mean_pairwise_similarity(cluster)
        assert result == pytest.approx(1.0 / 3.0, abs=1e-6)
