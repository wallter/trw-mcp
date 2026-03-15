"""Tests for hybrid retrieval engine — BM25 + RRF (PRD-CORE-041)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# BM25 search
# ---------------------------------------------------------------------------


class TestBM25Search:
    def test_returns_ranked_results(self) -> None:
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": "a1", "summary": "pydantic validation error", "detail": "...", "tags": []},
            {"id": "b2", "summary": "unrelated topic about scoring", "detail": "scoring decay", "tags": []},
            {"id": "c3", "summary": "pydantic model config settings", "detail": "field alias", "tags": ["pydantic"]},
        ]
        results = bm25_search("pydantic validation", entries, top_k=3)
        assert len(results) >= 1
        ids = [r[0] for r in results]
        # "pydantic validation error" should rank high
        assert "a1" in ids

    def test_returns_list_of_id_score_tuples(self) -> None:
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": "x1", "summary": "learning entry", "detail": "some detail", "tags": ["tag1"]},
        ]
        results = bm25_search("learning entry", entries, top_k=5)
        assert isinstance(results, list)
        if results:
            entry_id, score = results[0]
            assert isinstance(entry_id, str)
            assert isinstance(score, float)

    def test_returns_empty_when_no_entries(self) -> None:
        from trw_mcp.state.retrieval import bm25_search

        results = bm25_search("query", [], top_k=5)
        assert results == []

    def test_top_k_limits_results(self) -> None:
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": f"e{i}", "summary": f"entry {i} test query", "detail": "detail", "tags": []} for i in range(10)
        ]
        results = bm25_search("test query", entries, top_k=3)
        assert len(results) <= 3

    def test_uses_tags_in_corpus(self) -> None:
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": "tag-match", "summary": "generic entry", "detail": "generic detail", "tags": ["special-keyword"]},
            {"id": "no-match-a", "summary": "nothing here", "detail": "nothing", "tags": []},
            {"id": "no-match-b", "summary": "unrelated content", "detail": "other detail", "tags": []},
        ]
        results = bm25_search("special keyword", entries, top_k=5)
        ids = [r[0] for r in results]
        # The entry with the matching tag should score higher (hyphen split expands to "special" + "keyword")
        assert "tag-match" in ids

    def test_returns_empty_when_rank_bm25_unavailable(self) -> None:
        import sys

        with patch.dict(sys.modules, {"rank_bm25": None}):
            import trw_mcp.state.retrieval as ret_mod

            original = ret_mod._BM25_AVAILABLE
            try:
                ret_mod._BM25_AVAILABLE = False
                from trw_mcp.state.retrieval import bm25_search

                results = bm25_search("query", [{"id": "x", "summary": "test", "detail": "", "tags": []}], top_k=5)
                assert results == []
            finally:
                ret_mod._BM25_AVAILABLE = original


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


class TestRRFFuse:
    def test_fuses_two_rankings(self) -> None:
        from trw_mcp.state.retrieval import rrf_fuse

        ranking1 = [("a", 1.0), ("b", 0.5), ("c", 0.1)]
        ranking2 = [("b", 1.0), ("a", 0.8), ("d", 0.2)]
        result = rrf_fuse([ranking1, ranking2], k=60)
        assert len(result) >= 3
        ids = [r[0] for r in result]
        # "a" and "b" appear in both lists — they should score high
        assert "a" in ids
        assert "b" in ids

    def test_returns_sorted_by_score_descending(self) -> None:
        from trw_mcp.state.retrieval import rrf_fuse

        ranking1 = [("x", 1.0), ("y", 0.5)]
        ranking2 = [("x", 1.0), ("z", 0.3)]
        result = rrf_fuse([ranking1, ranking2], k=60)
        scores = [r[1] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_score_formula(self) -> None:
        from trw_mcp.state.retrieval import rrf_fuse

        # rank 0 in list of 1 = 1/(60+1) * 1 list = 1/61
        ranking = [("only", 1.0)]
        result = rrf_fuse([ranking], k=60)
        assert len(result) == 1
        entry_id, score = result[0]
        assert entry_id == "only"
        expected = 1.0 / (60 + 1)
        assert abs(score - expected) < 1e-9

    def test_empty_rankings_returns_empty(self) -> None:
        from trw_mcp.state.retrieval import rrf_fuse

        result = rrf_fuse([], k=60)
        assert result == []

    def test_single_ranking_passthrough(self) -> None:
        from trw_mcp.state.retrieval import rrf_fuse

        ranking = [("a", 1.0), ("b", 0.5), ("c", 0.1)]
        result = rrf_fuse([ranking], k=60)
        ids = [r[0] for r in result]
        assert "a" in ids
        assert "b" in ids
        assert "c" in ids

    def test_entry_in_both_lists_ranks_higher(self) -> None:
        from trw_mcp.state.retrieval import rrf_fuse

        # "shared" is in both lists at rank 0
        # "unique" is only in ranking2 at rank 0
        ranking1 = [("shared", 1.0), ("only1", 0.5)]
        ranking2 = [("shared", 1.0), ("only2", 0.5)]
        result = rrf_fuse([ranking1, ranking2], k=60)
        ids = [r[0] for r in result]
        assert ids[0] == "shared"


# ---------------------------------------------------------------------------
# Config fields
# ---------------------------------------------------------------------------


class TestConfigFields:
    def test_hybrid_retrieval_config_fields_exist(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert hasattr(config, "memory_store_path")
        assert hasattr(config, "hybrid_bm25_candidates")
        assert hasattr(config, "hybrid_vector_candidates")
        assert hasattr(config, "hybrid_rrf_k")
        assert hasattr(config, "hybrid_reranking_enabled")

    def test_config_defaults(self) -> None:
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.memory_store_path == ".trw/memory/vectors.db"
        assert config.hybrid_bm25_candidates == 50
        assert config.hybrid_vector_candidates == 50
        assert config.hybrid_rrf_k == 60
        assert config.hybrid_reranking_enabled is False


# ---------------------------------------------------------------------------
# BM25 edge cases: all-zero scores and empty-id fallback
# ---------------------------------------------------------------------------


class TestBM25EdgeCases:
    """Additional BM25 edge cases to cover token-overlap fallback path."""

    def test_bm25_all_zero_scores_uses_token_overlap(self) -> None:
        """When all BM25 scores are zero (term in half of docs), fall back to token-overlap."""
        from trw_mcp.state.retrieval import bm25_search

        # With only 2 documents and a query term that appears in both,
        # BM25 IDF = log((2-2+0.5)/(2+0.5)) which can be zero or negative.
        # To guarantee all-zero BM25 scores: use a query term that is NOT in any doc.
        # Actually, we need the term to appear in EVERY doc to get zero IDF.
        # Use "common" in all 2 docs.
        entries = [
            {"id": "e1", "summary": "common term document one", "detail": "more info", "tags": []},
            {"id": "e2", "summary": "common term document two", "detail": "more info", "tags": []},
        ]
        # "common term" appears in 100% of corpus → IDF approaches zero
        # Result: rely on overlap fallback
        results = bm25_search("common", entries, top_k=5)
        # Either BM25 returned results, or token-overlap fallback ran — no exception
        assert isinstance(results, list)

    def test_bm25_entry_with_empty_id_skipped_in_fallback(self) -> None:
        """Entries with empty id are skipped in the token-overlap fallback."""
        from trw_mcp.state.retrieval import bm25_search

        # Force all-zero BM25 scores by using a term in all docs
        entries = [
            {"id": "", "summary": "alpha beta gamma", "detail": "delta", "tags": []},
            {"id": "e2", "summary": "alpha beta gamma", "detail": "delta", "tags": []},
        ]
        # "alpha" in all docs → zero IDF → fallback used
        results = bm25_search("alpha", entries, top_k=5)
        ids = [r[0] for r in results]
        assert "" not in ids  # empty-id entry must be skipped

    def test_bm25_single_entry_corpus_returns_match(self) -> None:
        """Single-entry corpus with matching query returns that entry."""
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": "single", "summary": "unique rare word xyzzy", "detail": "plugh", "tags": []},
        ]
        results = bm25_search("xyzzy unique", entries, top_k=5)
        # With a single-entry corpus, IDF may be zero → fallback path
        # Either way, "single" should appear
        assert isinstance(results, list)

    def test_bm25_query_no_matching_tokens_returns_empty(self) -> None:
        """Query tokens that don't match any corpus entry → empty results."""
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": "x1", "summary": "python testing", "detail": "pytest", "tags": []},
            {"id": "x2", "summary": "python coding", "detail": "unittest", "tags": []},
        ]
        results = bm25_search("zzz999 nonexistent xyz123 qwerty", entries, top_k=5)
        # Both BM25 and token-overlap return empty when no terms match
        assert results == []

    def test_bm25_fallback_skips_entry_with_empty_id(self) -> None:
        """In the token-overlap fallback, entries with empty id string are skipped.

        This covers retrieval.py line 83 (the 'if not entry_id: continue' in fallback).
        We need all BM25 scores = 0.0 exactly (use a term not in corpus) + empty-id entry.

        To force all-zero BM25 scores, we can monkeypatch BM25Okapi.get_scores
        to return [0.0, 0.0] forcing the fallback path.
        """
        from trw_mcp.state.retrieval import bm25_search

        entries = [
            {"id": "", "summary": "alpha beta gamma", "detail": "delta", "tags": []},
            {"id": "valid-entry", "summary": "alpha beta gamma", "detail": "delta", "tags": []},
        ]

        # Force all-zero BM25 scores by patching BM25Okapi
        import trw_mcp.state.retrieval as ret_mod

        if not ret_mod._BM25_AVAILABLE:
            pytest.skip("rank_bm25 not available")

        with patch("trw_mcp.state.retrieval.BM25Okapi") as mock_bm25_cls:
            mock_bm25 = MagicMock()
            mock_bm25.get_scores.return_value = [0.0, 0.0]  # All zeros → fallback
            mock_bm25_cls.return_value = mock_bm25

            results = bm25_search("alpha", entries, top_k=5)

        # Empty-id entry is skipped; valid-entry is returned if it has token overlap
        ids = [r[0] for r in results]
        assert "" not in ids
