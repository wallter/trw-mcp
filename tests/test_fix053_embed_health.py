"""Tests for PRD-FIX-053-FR01 and FR07: Embedding health advisory + failure counter.

FR01: check_embeddings_status() returns advisory when embeddings enabled but unavailable.
FR07: get_embed_failure_count() tracks failures; check_embeddings_status() includes recent_failures.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


class TestEmbedHealthAdvisory:
    """FR01: check_embeddings_status returns advisory when embeddings unavailable."""

    def test_advisory_when_enabled_but_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embeddings_enabled=True but embedder=None → advisory with install hint."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import memory_adapter

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", True)

        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)
        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: None)

        result = memory_adapter.check_embeddings_status()

        assert result["enabled"] is True
        assert result["available"] is False
        advisory = str(result.get("advisory", ""))
        assert "pip install" in advisory or "trw-memory" in advisory, (
            f"Advisory must include install instructions, got: {advisory!r}"
        )

    def test_no_advisory_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embeddings working normally → available=True, empty advisory."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import memory_adapter

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", True)

        mock_embedder = MagicMock()
        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)
        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: mock_embedder)

        result = memory_adapter.check_embeddings_status()

        assert result["enabled"] is True
        assert result["available"] is True
        assert result.get("advisory", "") == ""

    def test_disabled_embeddings_no_advisory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """embeddings_enabled=False → enabled=False, no advisory."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import memory_adapter

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", False)

        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)

        result = memory_adapter.check_embeddings_status()

        assert result["enabled"] is False
        assert result.get("advisory", "") == ""

    def test_check_embeddings_status_has_required_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_embeddings_status always returns enabled, available, advisory keys."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import memory_adapter

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", False)
        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)

        result = memory_adapter.check_embeddings_status()

        assert "enabled" in result
        assert "available" in result
        assert "advisory" in result


class TestEmbedFailureCounter:
    """FR07: _embed_failures counter tracks embedding failures."""

    def test_counter_increments_when_embedder_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """store_learning with embedder=None → failure counter increments."""
        from trw_mcp.state import memory_adapter

        # Reset counter before test
        memory_adapter.reset_embed_failure_count()

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir()

        # Ensure no embedder is available
        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: None)

        # Store 5 learnings — each should increment the failure counter
        for i in range(5):
            memory_adapter.store_learning(
                trw_dir,
                f"L-fail{i:03d}",
                f"Summary {i}",
                f"Detail {i}",
            )

        count = memory_adapter.get_embed_failure_count()
        assert count == 5, f"Expected 5 failures, got {count}"

    def test_counter_does_not_increment_when_embedder_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """store_learning with working embedder → counter stays at 0."""
        from trw_mcp.state import memory_adapter

        memory_adapter.reset_embed_failure_count()

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir()

        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1] * 384

        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: mock_embedder)

        memory_adapter.store_learning(
            trw_dir,
            "L-ok001",
            "Working embedding",
            "Detail",
        )

        count = memory_adapter.get_embed_failure_count()
        assert count == 0

    def test_get_embed_failure_count_exists(self) -> None:
        """get_embed_failure_count function must exist and return int."""
        from trw_mcp.state import memory_adapter

        result = memory_adapter.get_embed_failure_count()
        assert isinstance(result, int)
        assert result >= 0

    def test_reset_embed_failure_count_resets_to_zero(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """reset_embed_failure_count() sets the counter back to 0."""
        from trw_mcp.state import memory_adapter

        # First increment it
        memory_adapter.reset_embed_failure_count()
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir()

        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: None)
        memory_adapter.store_learning(trw_dir, "L-reset01", "Summary", "Detail")
        assert memory_adapter.get_embed_failure_count() > 0

        # Now reset
        memory_adapter.reset_embed_failure_count()
        assert memory_adapter.get_embed_failure_count() == 0

    def test_check_embeddings_status_includes_recent_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """check_embeddings_status response includes recent_failures count (FR07)."""
        from trw_mcp.models.config import TRWConfig
        from trw_mcp.state import memory_adapter

        memory_adapter.reset_embed_failure_count()

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", True)
        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)
        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: None)

        # Set the counter to a known value by direct module attribute assignment.
        # _embed_failures is a module-level int with no public setter API —
        # direct assignment is the only way to set it to an arbitrary value for testing.
        memory_adapter._embed_failures = 7  # type: ignore[attr-defined]

        result = memory_adapter.check_embeddings_status()

        assert "recent_failures" in result, "check_embeddings_status must include recent_failures field (FR07)"
        assert result["recent_failures"] == 7

    def test_recent_failures_reflects_actual_failure_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """recent_failures in check_embeddings_status matches get_embed_failure_count()."""
        from trw_mcp.state import memory_adapter

        memory_adapter.reset_embed_failure_count()

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "memory").mkdir()

        # Use None embedder to trigger failure counter during store_learning
        monkeypatch.setattr(memory_adapter, "get_embedder", lambda: None)

        # Cause 3 real failures via store_learning
        for i in range(3):
            memory_adapter.store_learning(
                trw_dir,
                f"L-rf{i:03d}",
                f"Summary {i}",
                f"Detail {i}",
            )

        actual_count = memory_adapter.get_embed_failure_count()
        assert actual_count == 3, f"Expected 3 failures after 3 stores, got {actual_count}"

        # check_embeddings_status must report the same count
        # Use a properly constructed config that has all required fields
        from trw_mcp.models.config import TRWConfig

        mock_config = TRWConfig.__new__(TRWConfig)
        object.__setattr__(mock_config, "embeddings_enabled", True)
        monkeypatch.setattr(memory_adapter, "get_config", lambda: mock_config)

        result = memory_adapter.check_embeddings_status()
        assert result["recent_failures"] == actual_count, (
            f"recent_failures ({result['recent_failures']}) must equal get_embed_failure_count() ({actual_count})"
        )
