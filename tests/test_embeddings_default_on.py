"""Option A+ (council-ratified 2026-06-10): embeddings ON by default.

PRD-DIST-254 §FR03 follow-up. The MCP `recall_learnings` path collapses on a
realistic corpus when run with the historical `embeddings_enabled=False`
default (Recall@5=0.125 vs 0.9375 with the full hybrid path). The council
ratified flipping the in-code default to True (Option A+), with a non-blocking
first-recall download warm-up and graceful keyword degradation while the
warm-up is incomplete.

These tests pin:
  - the new default value on a fresh ``TRWConfig``
  - that ``trw_session_start`` stays cold-load-free: probing the embedder with
    ``get_initialized_embedder`` never loads a model, even with embeddings on.
"""

from __future__ import annotations

from unittest.mock import patch

from trw_mcp.models.config import TRWConfig


class TestEmbeddingsEnabledDefault:
    """Council-ratified Option A+: embeddings_enabled defaults to True."""

    def test_fresh_config_defaults_embeddings_on(self) -> None:
        """A fresh TRWConfig (no overrides) must enable embeddings."""
        config = TRWConfig()
        assert config.embeddings_enabled is True

    def test_explicit_disable_is_honored(self) -> None:
        """Operators can still opt OUT explicitly."""
        config = TRWConfig(embeddings_enabled=False)
        assert config.embeddings_enabled is False


class TestSessionStartStaysColdLoadFree:
    """The MCP hot path must never pay a synchronous cold model load."""

    def test_get_initialized_embedder_returns_none_before_warmup(self) -> None:
        """Before any cold init, get_initialized_embedder yields None.

        This is the guard that keeps trw_session_start from blocking on a
        sentence-transformers download/load even with embeddings_enabled=True:
        get_initialized_embedder() returns None until an explicit embedding op
        (learn-time dedup) has initialized the singleton.
        """
        from trw_mcp.state import _memory_connection

        _memory_connection.reset_embedder()
        try:
            # Even with a config that would enable embeddings, the no-cold-init
            # accessor must not construct/load anything.
            with patch(
                "trw_mcp.models.config.get_config",
                return_value=TRWConfig(embeddings_enabled=True),
            ):
                assert _memory_connection.get_initialized_embedder() is None
        finally:
            _memory_connection.reset_embedder()
