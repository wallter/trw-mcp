"""The project consolidation policy fields, which step 2.75 sends to the daemon (PRD-CORE-302 FR03)."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig

# ---------------------------------------------------------------------------
# FR08 — Config Fields and Validation
# ---------------------------------------------------------------------------


class TestConsolidationConfig:
    """FR08: TRWConfig consolidation fields have correct defaults and constraints."""

    def test_default_enabled_is_true(self) -> None:
        """memory_consolidation_enabled defaults to True."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_enabled is True

    def test_interval_days_is_retired(self) -> None:
        """memory_consolidation_interval_days was retired 2026-09-16 (PRD-QUAL-139-FR05).

        It had no reader in any corpus, so the cadence it named never gated a
        consolidation run. Asserting its absence keeps the retirement from being
        silently undone by a re-added declaration.
        """
        assert not hasattr(TRWConfig(), "memory_consolidation_interval_days")

    def test_default_min_cluster(self) -> None:
        """memory_consolidation_min_cluster defaults to 3."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_min_cluster == 3

    def test_default_similarity_threshold(self) -> None:
        """memory_consolidation_similarity_threshold defaults to 0.75."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_similarity_threshold == pytest.approx(0.75)

    def test_default_max_per_cycle(self) -> None:
        """memory_consolidation_max_per_cycle defaults to 50."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_max_per_cycle == 50

    def test_min_cluster_below_2_raises_validation_error(self) -> None:
        """min_cluster < 2 raises a ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_min_cluster=1)

    def test_min_cluster_exactly_2_is_valid(self) -> None:
        """min_cluster = 2 is valid (boundary)."""
        cfg = TRWConfig(memory_consolidation_min_cluster=2)
        assert cfg.memory_consolidation_min_cluster == 2

    def test_similarity_threshold_above_1_raises_validation_error(self) -> None:
        """similarity_threshold > 1.0 raises a ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_similarity_threshold=1.1)

    def test_similarity_threshold_below_0_raises_validation_error(self) -> None:
        """similarity_threshold < 0.0 raises a ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_similarity_threshold=-0.1)

    def test_similarity_threshold_boundary_values_valid(self) -> None:
        """similarity_threshold = 0.0 and 1.0 are valid boundaries."""
        cfg_low = TRWConfig(memory_consolidation_similarity_threshold=0.0)
        assert cfg_low.memory_consolidation_similarity_threshold == 0.0
        cfg_high = TRWConfig(memory_consolidation_similarity_threshold=1.0)
        assert cfg_high.memory_consolidation_similarity_threshold == 1.0

    def test_max_per_cycle_below_1_raises_validation_error(self) -> None:
        """max_per_cycle < 1 raises a ValidationError."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TRWConfig(memory_consolidation_max_per_cycle=0)

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_CONSOLIDATION_ENABLED env var overrides default."""
        monkeypatch.setenv("TRW_MEMORY_CONSOLIDATION_ENABLED", "false")
        cfg = TRWConfig()
        assert cfg.memory_consolidation_enabled is False

    def test_env_var_min_cluster_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_CONSOLIDATION_MIN_CLUSTER env var overrides default."""
        monkeypatch.setenv("TRW_MEMORY_CONSOLIDATION_MIN_CLUSTER", "5")
        cfg = TRWConfig()
        assert cfg.memory_consolidation_min_cluster == 5

    def test_env_var_similarity_threshold_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TRW_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD env var overrides default."""
        monkeypatch.setenv("TRW_MEMORY_CONSOLIDATION_SIMILARITY_THRESHOLD", "0.9")
        cfg = TRWConfig()
        assert cfg.memory_consolidation_similarity_threshold == pytest.approx(0.9)
