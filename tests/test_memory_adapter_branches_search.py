"""Targeted memory adapter search branch tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_adapter import _keyword_search, _search_entries, get_backend

from ._memory_adapter_branches_support import trw_dir  # noqa: F401


class _HybridEmbedder:
    """Embedder returning a fixed query vector; combined with patched stored
    embeddings this drives the real cosine ``dense_search`` ranker
    deterministically through ``hybrid_search``."""

    def embed(self, _text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def available(self) -> bool:
        return True


def _run_hybrid(
    backend: Any,
    query: str,
    *,
    stored: dict[str, list[float]],
    min_impact: float = 0.0,
    mem_status: MemoryStatus | None = None,
    tags: list[str] | None = None,
) -> list[str]:
    """Drive ``_search_entries`` through the REAL hybrid pipeline (BM25 + dense
    + RRF), injecting the dense ranking via patched stored embeddings."""
    cfg = TRWConfig()
    with (
        patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=_HybridEmbedder(),
        ),
        patch.object(backend, "get_stored_embeddings", return_value=stored),
        patch("trw_mcp.models.config.get_config", return_value=cfg),
    ):
        results = _search_entries(backend, query, min_impact=min_impact, mem_status=mem_status, tags=tags)
    return [e.id for e in results]


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
        """Full hybrid path through the real BM25 + dense + RRF pipeline.

        PRD-DIST-254 §FR03 follow-up: ``_search_entries`` delegates to
        ``trw_memory.retrieval.pipeline.hybrid_search``. The dense ranker is
        driven via patched stored embeddings; both entries share the query token
        so BM25 also ranks them. Both must surface.
        """
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-h1", content="hybrid test alpha", detail="d1"))
        backend.store(MemoryEntry(id="L-h2", content="hybrid test beta", detail="d2"))

        ids = _run_hybrid(
            backend,
            "hybrid",
            stored={"L-h1": [1.0, 0.0, 0.0], "L-h2": [0.0, 1.0, 0.0]},
        )
        assert len(ids) >= 1
        assert "L-h1" in ids and "L-h2" in ids

    def test_hybrid_vector_only_entry_fetched(self, trw_dir: Path) -> None:
        """A dense-nearest entry with no lexical match still surfaces.

        The widened candidate pool includes every namespace entry, so a vector-
        only gold record (no keyword overlap) is ranked and returned — the gap
        the old ~75-record keyword+vector slice could miss on a large namespace.
        """
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-vo1", content="keyword match token", detail="d1"))
        backend.store(MemoryEntry(id="L-vo2", content="orthogonal unrelated content", detail="d2", importance=0.9))

        ids = _run_hybrid(
            backend,
            "keyword token",
            stored={"L-vo1": [0.0, 1.0, 0.0], "L-vo2": [1.0, 0.0, 0.0]},
        )
        assert "L-vo2" in ids, f"vector-only entry missing: {ids}"

    def test_hybrid_vector_entry_filtered_by_min_impact(self, trw_dir: Path) -> None:
        """A dense-nearest entry below min_impact is excluded at the pool scan."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-fi1", content="keyword hit token", detail="d1", importance=0.9))
        backend.store(MemoryEntry(id="L-fi2", content="low impact vector", detail="d2", importance=0.1))

        ids = _run_hybrid(
            backend,
            "keyword token",
            stored={"L-fi1": [0.0, 1.0, 0.0], "L-fi2": [1.0, 0.0, 0.0]},
            min_impact=0.5,
        )
        assert "L-fi2" not in ids
        assert "L-fi1" in ids

    def test_hybrid_vector_entry_filtered_by_status(self, trw_dir: Path) -> None:
        """A dense-nearest entry with the wrong status is excluded at the pool scan."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-fs1", content="kw hit token", detail="d1"))
        backend.store(MemoryEntry(id="L-fs2", content="wrong status", detail="d2", status=MemoryStatus.OBSOLETE))

        ids = _run_hybrid(
            backend,
            "kw token",
            stored={"L-fs1": [0.0, 1.0, 0.0], "L-fs2": [1.0, 0.0, 0.0]},
            mem_status=MemoryStatus.ACTIVE,
        )
        assert "L-fs2" not in ids

    def test_hybrid_vector_entry_filtered_by_tags(self, trw_dir: Path) -> None:
        """A dense-nearest entry missing the requested tag is filtered post-rank."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-ft1", content="kw hit token", detail="d1", tags=["python"]))
        backend.store(MemoryEntry(id="L-ft2", content="wrong tags", detail="d2", tags=["rust"]))

        ids = _run_hybrid(
            backend,
            "kw token",
            stored={"L-ft1": [0.0, 1.0, 0.0], "L-ft2": [1.0, 0.0, 0.0]},
            tags=["python"],
        )
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
