"""Targeted memory adapter embedder-singleton and status-reporting branch tests.

PRD-CORE-280 slice e1: the ``TestGetBackendAutoResolve``, ``TestEmbedAndStore``
and ``TestEnsureMigratedErrors`` classes that used to live here were deleted,
not ported — they asserted purely on the SQLite singleton accessor / SQLite
backend internals (auto-resolve, embed-and-store-onto-a-raw-backend,
YAML-to-SQLite migration) that the fixture contract classifies as SQLite
internals owned by trw-memory. ``get_embedder``/``check_embeddings_status``
stay: neither opens ``memory.db`` nor references a banned name, so they run
unchanged.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from trw_mcp.state.memory_adapter import check_embeddings_status, get_embedder, reset_embedder


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
