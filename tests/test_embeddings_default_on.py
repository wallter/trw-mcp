"""Option A+ (council-ratified 2026-06-10): embeddings ON by default.

PRD-DIST-254 §FR03 follow-up. The MCP `recall_learnings` path collapses on a
realistic corpus when run with the historical `embeddings_enabled=False`
default (Recall@5=0.125 vs 0.9375 with the full hybrid path). The council
ratified flipping the in-code default to True (Option A+), with a non-blocking
first-recall download warm-up and graceful keyword degradation while the
warm-up is incomplete.

These tests pin the default value on a fresh ``TRWConfig``. trw-mcp loads no
model at all since PRD-CORE-302 FR05; the daemon owns it.
"""

from __future__ import annotations

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
