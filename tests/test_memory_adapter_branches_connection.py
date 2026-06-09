"""Targeted memory adapter connection and migration branch tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from trw_memory.models.memory import MemoryEntry

from tests._memory_adapter_branches_support import _make_backend
from trw_mcp.state.memory_adapter import (
    _embed_and_store,
    check_embeddings_status,
    ensure_migrated,
    get_backend,
    get_embedder,
    reset_embedder,
)
from ._memory_adapter_branches_support import trw_dir  # noqa: F401

from ._memory_adapter_branches_support import trw_dir  # noqa: F401

from ._memory_adapter_branches_support import trw_dir  # noqa: F401


class TestGetBackendAutoResolve:
    def test_auto_resolve_trw_dir_when_none(self, trw_dir: Path) -> None:
        """get_backend(None) calls resolve_trw_dir() to find .trw dir."""
        with patch("trw_mcp.state._paths.resolve_trw_dir", return_value=trw_dir):
            backend = get_backend(None)
            assert backend is not None


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

            result1 = get_embedder()
            assert result1 is None

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


class TestEmbedAndStore:
    def test_no_embedder_returns_early(self, trw_dir: Path) -> None:
        """When embedder is None, _embed_and_store returns immediately (line 178)."""
        backend = _make_backend(trw_dir)
        try:
            with patch("trw_mcp.state._memory_connection.get_embedder", return_value=None):
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
                namespace="",
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
                backend.store = original_store
        finally:
            backend.close()
