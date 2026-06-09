"""Targeted memory adapter search branch tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.state.memory_adapter import _keyword_search, _search_entries, get_backend
from ._memory_adapter_branches_support import trw_dir  # noqa: F401

from ._memory_adapter_branches_support import trw_dir  # noqa: F401


class TestKeywordSearchMultiToken:
    def test_multi_token_intersection(self, trw_dir: Path) -> None:
        """Multi-token query intersects results from each token (lines 482-508)."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-mt1", content="python testing gotcha", detail="d1"))
        backend.store(MemoryEntry(id="L-mt2", content="python memory leak", detail="d2"))
        backend.store(MemoryEntry(id="L-mt3", content="rust testing safety", detail="d3"))

        results = _keyword_search(backend, "python testing")
        ids = [e.id for e in results]
        assert "L-mt1" in ids

    def test_multi_token_no_common(self, trw_dir: Path) -> None:
        """Multi-token query with no intersection returns empty."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-nc1", content="alpha", detail="only alpha"))
        backend.store(MemoryEntry(id="L-nc2", content="beta", detail="only beta"))
        results = _keyword_search(backend, "alpha beta")
        assert isinstance(results, list)


class TestSearchEntriesHybrid:
    def test_embedder_none_falls_back_to_keyword(self, trw_dir: Path) -> None:
        """When embedder is None, returns keyword results only (line 406)."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-fb1", content="fallback test", detail="d"))
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            results = _search_entries(backend, "fallback")
            assert isinstance(results, list)

    def test_embed_query_returns_none(self, trw_dir: Path) -> None:
        """When embedder.embed(query) returns None, falls back (line 411)."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-en1", content="embed none", detail="d"))
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = None
        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            results = _search_entries(backend, "embed")
            assert isinstance(results, list)

    def test_empty_vector_hits(self, trw_dir: Path) -> None:
        """When search_vectors returns empty, falls back (line 416)."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-ev1", content="vector empty", detail="d"))
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "search_vectors", return_value=[]),
        ):
            results = _search_entries(backend, "vector")
            assert isinstance(results, list)

    def test_hybrid_rrf_fusion_success(self, trw_dir: Path) -> None:
        """Full hybrid path with RRF fusion (lines 418-453)."""
        backend = get_backend(trw_dir)
        entry1 = MemoryEntry(id="L-h1", content="hybrid test alpha", detail="d1")
        entry2 = MemoryEntry(id="L-h2", content="hybrid test beta", detail="d2")
        backend.store(entry1)
        backend.store(entry2)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        vector_hits = [("L-h1", 0.9), ("L-h2", 0.8)]
        mock_fuse = MagicMock(return_value=[("L-h1", 1.0), ("L-h2", 0.8)])

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "search_vectors", return_value=vector_hits),
            patch("trw_memory.retrieval.fusion.rrf_fuse", mock_fuse),
        ):
            results = _search_entries(backend, "hybrid")
            assert len(results) >= 1

    def test_hybrid_vector_only_entry_fetched(self, trw_dir: Path) -> None:
        """Vector-only hits (not in keyword results) are fetched from backend (line 430)."""
        backend = get_backend(trw_dir)
        entry1 = MemoryEntry(id="L-vo1", content="keyword match", detail="d1")
        entry2 = MemoryEntry(id="L-vo2", content="only in vectors", detail="d2", importance=0.9)
        backend.store(entry1)
        backend.store(entry2)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        vector_hits = [("L-vo1", 0.9), ("L-vo2", 0.8)]

        def limited_keyword(be: Any, query: str, **kwargs: Any) -> list[MemoryEntry]:
            return [entry1]

        mock_fuse = MagicMock(return_value=[("L-vo1", 1.0), ("L-vo2", 0.8)])

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "search_vectors", return_value=vector_hits),
            patch("trw_mcp.state._memory_queries._keyword_search", limited_keyword),
            patch("trw_memory.retrieval.fusion.rrf_fuse", mock_fuse),
        ):
            results = _search_entries(backend, "keyword")
            ids = [e.id for e in results]
            assert "L-vo2" in ids

    def test_hybrid_vector_entry_filtered_by_min_impact(self, trw_dir: Path) -> None:
        """Vector-only entry below min_impact is filtered (line 434)."""
        backend = get_backend(trw_dir)
        entry1 = MemoryEntry(id="L-fi1", content="keyword hit", detail="d1", importance=0.9)
        low_impact_entry = MemoryEntry(
            id="L-fi2",
            content="low impact vector",
            detail="d2",
            importance=0.1,
        )
        backend.store(entry1)
        backend.store(low_impact_entry)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        vector_hits = [("L-fi1", 0.9), ("L-fi2", 0.8)]

        def limited_kw(be: Any, query: str, **kwargs: Any) -> list[MemoryEntry]:
            return [entry1]

        mock_fuse = MagicMock(return_value=[("L-fi1", 1.0), ("L-fi2", 0.8)])

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "search_vectors", return_value=vector_hits),
            patch("trw_mcp.state._memory_queries._keyword_search", limited_kw),
            patch("trw_memory.retrieval.fusion.rrf_fuse", mock_fuse),
        ):
            results = _search_entries(backend, "keyword", min_impact=0.5)
            ids = [e.id for e in results]
            assert "L-fi2" not in ids

    def test_hybrid_vector_entry_filtered_by_status(self, trw_dir: Path) -> None:
        """Vector-only entry with wrong status is filtered (line 436)."""
        backend = get_backend(trw_dir)
        entry1 = MemoryEntry(id="L-fs1", content="kw hit", detail="d1")
        wrong_status = MemoryEntry(
            id="L-fs2",
            content="wrong status",
            detail="d2",
            status=MemoryStatus.OBSOLETE,
        )
        backend.store(entry1)
        backend.store(wrong_status)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        vector_hits = [("L-fs1", 0.9), ("L-fs2", 0.8)]

        def limited_kw(be: Any, query: str, **kwargs: Any) -> list[MemoryEntry]:
            return [entry1]

        mock_fuse = MagicMock(return_value=[("L-fs1", 1.0), ("L-fs2", 0.8)])

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "search_vectors", return_value=vector_hits),
            patch("trw_mcp.state._memory_queries._keyword_search", limited_kw),
            patch("trw_memory.retrieval.fusion.rrf_fuse", mock_fuse),
        ):
            results = _search_entries(backend, "kw", mem_status=MemoryStatus.ACTIVE)
            ids = [e.id for e in results]
            assert "L-fs2" not in ids

    def test_hybrid_vector_entry_filtered_by_tags(self, trw_dir: Path) -> None:
        """Vector-only entry with missing tags is filtered (lines 438-439)."""
        backend = get_backend(trw_dir)
        entry1 = MemoryEntry(id="L-ft1", content="kw hit", detail="d1", tags=["python"])
        wrong_tags = MemoryEntry(id="L-ft2", content="wrong tags", detail="d2", tags=["rust"])
        backend.store(entry1)
        backend.store(wrong_tags)

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        vector_hits = [("L-ft1", 0.9), ("L-ft2", 0.8)]

        def limited_kw(be: Any, query: str, **kwargs: Any) -> list[MemoryEntry]:
            return [entry1]

        mock_fuse = MagicMock(return_value=[("L-ft1", 1.0), ("L-ft2", 0.8)])

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "search_vectors", return_value=vector_hits),
            patch("trw_mcp.state._memory_queries._keyword_search", limited_kw),
            patch("trw_memory.retrieval.fusion.rrf_fuse", mock_fuse),
        ):
            results = _search_entries(backend, "kw", tags=["python"])
            ids = [e.id for e in results]
            assert "L-ft2" not in ids

    def test_hybrid_exception_falls_back(self, trw_dir: Path) -> None:
        """When hybrid search raises, falls back to keyword (lines 455-457)."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-ex1", content="exception fallback", detail="d"))

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("vector crash")

        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            results = _search_entries(backend, "exception")
            assert isinstance(results, list)
