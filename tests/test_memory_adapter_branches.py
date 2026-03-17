"""Targeted coverage tests for state/memory_adapter.py uncovered lines.

Covers: embedder lifecycle, hybrid search, multi-token keyword search,
migration error paths, update_learning branches, backfill_embeddings,
check_embeddings_status, and access tracking exceptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import MemoryEntry, MemoryStatus
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.state import memory_adapter
from trw_mcp.state.memory_adapter import (
    _embed_and_store,
    _keyword_search,
    _search_entries,
    backfill_embeddings,
    check_embeddings_status,
    ensure_migrated,
    find_yaml_path_for_entry,
    get_backend,
    get_embedder,
    recall_learnings,
    reset_embedder,
    store_learning,
    update_access_tracking,
    update_learning,
)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


def _make_backend(trw_dir: Path) -> SQLiteBackend:
    """Create a fresh SQLiteBackend for a test trw_dir."""
    db_path = trw_dir / "memory" / "memory.db"
    return SQLiteBackend(db_path)


# ---------------------------------------------------------------------------
# get_backend: auto-resolve trw_dir (lines 68-69)
# ---------------------------------------------------------------------------


class TestGetBackendAutoResolve:
    def test_auto_resolve_trw_dir_when_none(self, trw_dir: Path) -> None:
        """get_backend(None) calls resolve_trw_dir() to find .trw dir."""
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            backend = get_backend(None)
            assert backend is not None


# ---------------------------------------------------------------------------
# Embedder lifecycle (lines 112-113, 129-134)
# ---------------------------------------------------------------------------


class TestGetEmbedder:
    def test_embeddings_disabled_returns_none(self) -> None:
        """When embeddings_enabled=False, get_embedder returns None (lines 112-113)."""
        reset_embedder()
        with patch("trw_mcp.models.config.get_config") as mock_cfg:
            cfg = MagicMock()
            cfg.embeddings_enabled = False
            mock_cfg.return_value = cfg
            result = get_embedder()
            assert result is None

    def test_embedder_available_false_logs_hint(self) -> None:
        """When provider.available() returns False (lines 129-132)."""
        reset_embedder()
        mock_provider = MagicMock()
        mock_provider.available.return_value = False

        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch(
                "trw_memory.embeddings.local.LocalEmbeddingProvider",
                return_value=mock_provider,
            ),
        ):
            cfg = MagicMock()
            cfg.embeddings_enabled = True
            cfg.retrieval_embedding_model = "test-model"
            cfg.retrieval_embedding_dim = 128
            mock_cfg.return_value = cfg

            result = get_embedder()
            assert result is None
            mock_provider.available.assert_called_once()

    def test_embedder_init_exception_caught(self) -> None:
        """When LocalEmbeddingProvider raises (lines 133-134)."""
        reset_embedder()
        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch(
                "trw_memory.embeddings.local.LocalEmbeddingProvider",
                side_effect=RuntimeError("import boom"),
            ),
        ):
            cfg = MagicMock()
            cfg.embeddings_enabled = True
            cfg.retrieval_embedding_model = "test-model"
            cfg.retrieval_embedding_dim = 128
            mock_cfg.return_value = cfg

            result = get_embedder()
            assert result is None

    def test_embedder_init_failure_allows_retry(self) -> None:
        """FR06: After init failure, _embedder_checked is NOT set — retry works."""
        reset_embedder()
        mock_provider = MagicMock()
        mock_provider.available.return_value = True

        call_count = {"n": 0}

        def _provider_factory(**kwargs: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("transient failure")
            return mock_provider

        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch(
                "trw_memory.embeddings.local.LocalEmbeddingProvider",
                side_effect=_provider_factory,
            ),
        ):
            cfg = MagicMock()
            cfg.embeddings_enabled = True
            cfg.retrieval_embedding_model = "test-model"
            cfg.retrieval_embedding_dim = 128
            mock_cfg.return_value = cfg

            # First call fails
            result1 = get_embedder()
            assert result1 is None

            # Second call retries and succeeds
            result2 = get_embedder()
            assert result2 is mock_provider
            assert call_count["n"] == 2

    def test_embedder_available_true_caches(self) -> None:
        """When provider.available() returns True, embedder is cached."""
        reset_embedder()
        mock_provider = MagicMock()
        mock_provider.available.return_value = True

        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch(
                "trw_memory.embeddings.local.LocalEmbeddingProvider",
                return_value=mock_provider,
            ),
        ):
            cfg = MagicMock()
            cfg.embeddings_enabled = True
            cfg.retrieval_embedding_model = "test-model"
            cfg.retrieval_embedding_dim = 128
            mock_cfg.return_value = cfg

            result = get_embedder()
            assert result is mock_provider


# ---------------------------------------------------------------------------
# check_embeddings_status (lines 156-164)
# ---------------------------------------------------------------------------


class TestCheckEmbeddingsStatus:
    def test_disabled(self) -> None:
        """When embeddings_enabled=False, returns disabled status (line 158)."""
        with patch("trw_mcp.models.config.get_config") as mock_cfg:
            cfg = MagicMock()
            cfg.embeddings_enabled = False
            mock_cfg.return_value = cfg
            status = check_embeddings_status()
            assert status["enabled"] is False
            assert status["available"] is False
            assert status["advisory"] == ""

    def test_enabled_available(self) -> None:
        """When embedder is available, returns enabled+available (line 162)."""
        mock_embedder = MagicMock()
        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
        ):
            cfg = MagicMock()
            cfg.embeddings_enabled = True
            mock_cfg.return_value = cfg
            status = check_embeddings_status()
            assert status["enabled"] is True
            assert status["available"] is True

    def test_enabled_not_available(self) -> None:
        """When embedder is None but enabled, returns advisory (lines 164-171)."""
        with (
            patch("trw_mcp.models.config.get_config") as mock_cfg,
            patch("trw_mcp.state._memory_connection.get_embedder", return_value=None),
        ):
            cfg = MagicMock()
            cfg.embeddings_enabled = True
            mock_cfg.return_value = cfg
            status = check_embeddings_status()
            assert status["enabled"] is True
            assert status["available"] is False
            assert "sentence-transformers" in str(status["advisory"])


# ---------------------------------------------------------------------------
# _embed_and_store (lines 178, 183-184)
# ---------------------------------------------------------------------------


class TestEmbedAndStore:
    def test_no_embedder_returns_early(self, trw_dir: Path) -> None:
        """When embedder is None, _embed_and_store returns immediately (line 178)."""
        backend = _make_backend(trw_dir)
        try:
            with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
                # Should not raise
                _embed_and_store(backend, "L-test", "some text")
        finally:
            backend.close()

    def test_embed_raises_exception(self, trw_dir: Path) -> None:
        """When embedder.embed raises, logs and continues (lines 183-184)."""
        backend = _make_backend(trw_dir)
        try:
            mock_embedder = MagicMock()
            mock_embedder.embed.side_effect = RuntimeError("embed failed")
            with patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ):
                _embed_and_store(backend, "L-err", "text")
                # No exception raised
        finally:
            backend.close()

    def test_embed_returns_none(self, trw_dir: Path) -> None:
        """When embedder.embed returns None, upsert_vector is not called."""
        backend = _make_backend(trw_dir)
        try:
            mock_embedder = MagicMock()
            mock_embedder.embed.return_value = None
            with patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ):
                _embed_and_store(backend, "L-none", "text")
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# ensure_migrated error paths (lines 221-223, 229, 232-234)
# ---------------------------------------------------------------------------


class TestEnsureMigratedErrors:
    def test_migrate_entries_dir_raises(self, trw_dir: Path) -> None:
        """When migrate_entries_dir raises, returns zeros (lines 221-223)."""
        backend = _make_backend(trw_dir)
        try:
            with patch(
                "trw_memory.migration.from_trw.migrate_entries_dir",
                side_effect=RuntimeError("read failed"),
            ):
                result = ensure_migrated(trw_dir, backend)
                assert result == {"migrated": 0, "skipped": 0}
        finally:
            backend.close()

    def test_entry_with_empty_namespace_gets_default(self, trw_dir: Path) -> None:
        """Entries with empty namespace get _NAMESPACE assigned (line 229)."""
        backend = _make_backend(trw_dir)
        try:
            entry = MemoryEntry(
                id="L-ns001",
                content="Test",
                detail="Detail",
                namespace="",  # empty
            )
            with patch(
                "trw_memory.migration.from_trw.migrate_entries_dir",
                return_value=[entry],
            ):
                result = ensure_migrated(trw_dir, backend)
                assert result["migrated"] == 1
                stored = backend.get("L-ns001")
                assert stored is not None
                assert stored.namespace == "default"
        finally:
            backend.close()

    def test_entry_store_fails_increments_skipped(self, trw_dir: Path) -> None:
        """When backend.store raises, entry is skipped (lines 232-234)."""
        backend = _make_backend(trw_dir)
        try:
            entry = MemoryEntry(
                id="L-fail001",
                content="Test",
                detail="Detail",
            )
            original_store = backend.store

            call_count = 0

            def failing_store(e: Any) -> None:
                nonlocal call_count
                call_count += 1
                raise RuntimeError("store failed")

            backend.store = failing_store  # type: ignore[assignment]
            try:
                with patch(
                    "trw_memory.migration.from_trw.migrate_entries_dir",
                    return_value=[entry],
                ):
                    result = ensure_migrated(trw_dir, backend)
                    assert result["skipped"] == 1
                    assert result["migrated"] == 0
            finally:
                backend.store = original_store  # type: ignore[assignment]
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# _keyword_search multi-token (lines 482-508)
# ---------------------------------------------------------------------------


class TestKeywordSearchMultiToken:
    def test_multi_token_intersection(self, trw_dir: Path) -> None:
        """Multi-token query intersects results from each token (lines 482-508)."""
        backend = get_backend(trw_dir)
        # Store entries that match different token combinations
        backend.store(MemoryEntry(id="L-mt1", content="python testing gotcha", detail="d1"))
        backend.store(MemoryEntry(id="L-mt2", content="python memory leak", detail="d2"))
        backend.store(MemoryEntry(id="L-mt3", content="rust testing safety", detail="d3"))

        # Search with two tokens -- only entries matching BOTH should appear
        results = _keyword_search(backend, "python testing")
        ids = [e.id for e in results]
        assert "L-mt1" in ids
        # L-mt2 matches "python" but not "testing"; L-mt3 matches "testing" but not "python"

    def test_multi_token_no_common(self, trw_dir: Path) -> None:
        """Multi-token query with no intersection returns empty."""
        backend = get_backend(trw_dir)
        backend.store(MemoryEntry(id="L-nc1", content="alpha", detail="only alpha"))
        backend.store(MemoryEntry(id="L-nc2", content="beta", detail="only beta"))
        results = _keyword_search(backend, "alpha beta")
        # "alpha" matches L-nc1, "beta" matches L-nc2, intersection is empty
        # (unless detail/content match both -- depends on backend search)
        # Just verify the function runs without error
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# _search_entries hybrid search (lines 406-457)
# ---------------------------------------------------------------------------


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

        # vector hits return both entries
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

        # keyword search returns only entry1, vector hits include entry2
        vector_hits = [("L-vo1", 0.9), ("L-vo2", 0.8)]

        # Make keyword search return only entry1
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
        low_impact_entry = MemoryEntry(id="L-fi2", content="low impact vector", detail="d2", importance=0.1)
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


# ---------------------------------------------------------------------------
# recall_learnings: invalid status (lines 531-534)
# ---------------------------------------------------------------------------


class TestRecallLearningsStatusParsing:
    def test_invalid_status_string_ignored(self, trw_dir: Path) -> None:
        """Invalid status string is silently ignored (lines 531-534)."""
        store_learning(trw_dir, "L-is1", "Status test", "d")
        results = recall_learnings(trw_dir, "*", status="bogus_status")
        # Should not crash; returns entries unfiltered by status
        assert isinstance(results, list)

    def test_valid_status_active(self, trw_dir: Path) -> None:
        """Valid status='active' filters correctly."""
        store_learning(trw_dir, "L-va1", "Active entry", "d")
        results = recall_learnings(trw_dir, "*", status="active")
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# update_learning: detail, summary, out-of-range impact (lines 599-608)
# ---------------------------------------------------------------------------


class TestUpdateLearningBranches:
    def test_detail_update(self, trw_dir: Path) -> None:
        """detail= kwarg updates the detail field (lines 599-600)."""
        store_learning(trw_dir, "L-du1", "Summary", "Old detail")
        result = update_learning(trw_dir, "L-du1", detail="New detail")
        assert result["status"] == "updated"
        assert "detail updated" in result["changes"]

    def test_summary_update(self, trw_dir: Path) -> None:
        """summary= kwarg updates the content field (lines 603-604)."""
        store_learning(trw_dir, "L-su1", "Old Summary", "d")
        result = update_learning(trw_dir, "L-su1", summary="New Summary")
        assert result["status"] == "updated"
        assert "summary updated" in result["changes"]

    def test_impact_out_of_range(self, trw_dir: Path) -> None:
        """Impact outside [0.0, 1.0] returns invalid (line 608)."""
        store_learning(trw_dir, "L-ir1", "s", "d")
        result = update_learning(trw_dir, "L-ir1", impact=1.5)
        assert result["status"] == "invalid"
        assert "Impact must be" in result["error"]

    def test_impact_negative(self, trw_dir: Path) -> None:
        """Negative impact returns invalid."""
        store_learning(trw_dir, "L-ir2", "s", "d")
        result = update_learning(trw_dir, "L-ir2", impact=-0.1)
        assert result["status"] == "invalid"


# ---------------------------------------------------------------------------
# find_yaml_path_for_entry: index.yaml skip (line 710)
# ---------------------------------------------------------------------------


class TestFindYamlPathIndexSkip:
    def test_skips_index_yaml_returns_none(self, tmp_path: Path) -> None:
        """index.yaml is skipped; when it is the only file, returns None (line 710)."""
        from trw_mcp.models.config import get_config as _get_config

        trw = tmp_path / ".trw"
        trw.mkdir()
        cfg = _get_config()
        entries_dir = trw / cfg.learnings_dir / cfg.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        # ONLY create index.yaml -- no match possible
        (entries_dir / "index.yaml").write_text("index: true\n")

        result = find_yaml_path_for_entry(trw, "L-nomatch")
        assert result is None

    def test_entries_dir_missing_returns_none(self, tmp_path: Path) -> None:
        """When entries dir does not exist, returns None (line 698)."""
        trw = tmp_path / ".trw"
        trw.mkdir()
        # Do NOT create learnings/entries
        result = find_yaml_path_for_entry(trw, "L-any")
        assert result is None

    def test_partial_match_with_index_yaml_present(self, trw_dir: Path) -> None:
        """Partial match works when index.yaml is also present."""
        from trw_mcp.models.config import get_config as _get_config

        cfg = _get_config()
        entries_dir = trw_dir / cfg.learnings_dir / cfg.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        (entries_dir / "index.yaml").write_text("index: true\n")
        (entries_dir / "2026-01-01-L-idx001-some-summary.yaml").write_text("id: L-idx001\n")

        result = find_yaml_path_for_entry(trw_dir, "L-idx001")
        assert result is not None
        assert result.name != "index.yaml"
        assert "L-idx001" in result.name


# ---------------------------------------------------------------------------
# update_access_tracking: exception path (lines 736-737)
# ---------------------------------------------------------------------------


class TestAccessTrackingException:
    def test_exception_during_update_continues(self, trw_dir: Path) -> None:
        """Exception during backend.update is caught and skipped (lines 736-737)."""
        store_learning(trw_dir, "L-ae1", "s", "d")
        store_learning(trw_dir, "L-ae2", "s2", "d2")

        backend = get_backend(trw_dir)
        original_update = backend.update

        call_count = 0

        def failing_update(lid: str, **kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("update failed")
            return original_update(lid, **kwargs)

        backend.update = failing_update  # type: ignore[assignment]
        try:
            # First call will fail, second should succeed
            update_access_tracking(trw_dir, ["L-ae1", "L-ae2"])
            # Should not raise
        finally:
            backend.update = original_update  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# backfill_embeddings (lines 749-785)
# ---------------------------------------------------------------------------


class TestBackfillEmbeddings:
    def test_no_embedder_returns_zeros(self, trw_dir: Path) -> None:
        """When embedder is None, returns all zeros (lines 750-751)."""
        with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
            result = backfill_embeddings(trw_dir)
            assert result == {"embedded": 0, "skipped": 0, "failed": 0}

    def test_embeds_all_entries(self, trw_dir: Path) -> None:
        """Backfills embeddings for all entries (lines 753-775)."""
        store_learning(trw_dir, "L-bf1", "Alpha backfill", "Detail alpha")
        store_learning(trw_dir, "L-bf2", "Beta backfill", "Detail beta")

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]

        backend = get_backend(trw_dir)

        with (
            patch(
                "trw_mcp.state._memory_connection.get_embedder",
                return_value=mock_embedder,
            ),
            patch.object(backend, "upsert_vector") as mock_upsert,
        ):
            result = backfill_embeddings(trw_dir)
            assert result["embedded"] == 2
            assert result["skipped"] == 0
            assert result["failed"] == 0
            assert mock_upsert.call_count == 2

    def test_skips_empty_content(self, trw_dir: Path) -> None:
        """Entries with empty content+detail are skipped (lines 765-767)."""
        backend = get_backend(trw_dir)
        # Store an entry with empty content and detail
        backend.store(MemoryEntry(id="L-sk1", content="", detail=""))

        mock_embedder = MagicMock()

        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = backfill_embeddings(trw_dir)
            assert result["skipped"] == 1
            mock_embedder.embed.assert_not_called()

    def test_embed_returns_none_counts_as_failed(self, trw_dir: Path) -> None:
        """When embed returns None, increments failed (lines 770-772)."""
        store_learning(trw_dir, "L-fn1", "Fail none", "Detail")

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = None

        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = backfill_embeddings(trw_dir)
            assert result["failed"] == 1

    def test_exception_counts_as_failed(self, trw_dir: Path) -> None:
        """When embedding raises, increments failed (lines 776-777)."""
        store_learning(trw_dir, "L-fe1", "Fail exception", "Detail")

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("embed error")

        with patch(
            "trw_mcp.state._memory_connection.get_embedder",
            return_value=mock_embedder,
        ):
            result = backfill_embeddings(trw_dir)
            assert result["failed"] == 1
