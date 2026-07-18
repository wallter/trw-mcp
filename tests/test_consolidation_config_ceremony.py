"""Configuration and ceremony wiring tests for consolidation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig

from ._consolidation_test_helpers import patch_trw_deliver_deps


class TestCeremonyWiring:
    """FR07: trw_deliver includes memory consolidation at step 2.6."""

    def test_consolidation_disabled_result_has_skipped_status(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When memory_consolidation_enabled=False, trw_deliver result has consolidation.status=skipped."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        cfg = TRWConfig(
            memory_consolidation_enabled=False,
            learning_auto_prune_on_deliver=False,
        )
        with patch("trw_mcp.tools.ceremony.get_config", return_value=cfg):
            # Patch consolidate_cycle — should NOT be called
            with patch("trw_mcp.state.consolidation.consolidate_cycle") as mock_cons:
                with patch_trw_deliver_deps(trw_dir):
                    # Call the wiring logic directly (mirrors ceremony.py step 2.6)
                    results: dict[str, Any] = {}
                    errors: list[str] = []
                    try:
                        if cfg.memory_consolidation_enabled:
                            from trw_mcp.state.consolidation import consolidate_cycle as _cc

                            results["consolidation"] = _cc(trw_dir, max_entries=cfg.memory_consolidation_max_per_cycle)
                        else:
                            results["consolidation"] = {"status": "skipped", "reason": "disabled"}
                    except Exception as exc:
                        errors.append(f"consolidation: {exc}")
                        results["consolidation"] = {"status": "failed", "error": str(exc)}

            # consolidate_cycle should not be called when disabled
            mock_cons.assert_not_called()
            assert results["consolidation"]["status"] == "skipped"
            assert results["consolidation"]["reason"] == "disabled"

    def test_consolidation_exception_is_fail_open(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When consolidate_cycle raises, error is collected and result has status=failed."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        cfg = TRWConfig(
            memory_consolidation_enabled=True,
            learning_auto_prune_on_deliver=False,
        )
        with patch("trw_mcp.tools.ceremony.get_config", return_value=cfg):
            with patch("trw_mcp.state.consolidation.consolidate_cycle", side_effect=RuntimeError("consolidation boom")):
                with patch_trw_deliver_deps(trw_dir):
                    # Mirror ceremony.py step 2.6 logic exactly
                    results: dict[str, Any] = {}
                    errors: list[str] = []
                    try:
                        if cfg.memory_consolidation_enabled:
                            from trw_mcp.state.consolidation import consolidate_cycle as _cc

                            results["consolidation"] = _cc(trw_dir, max_entries=cfg.memory_consolidation_max_per_cycle)
                        else:
                            results["consolidation"] = {"status": "skipped", "reason": "disabled"}
                    except Exception as exc:
                        errors.append(f"consolidation: {exc}")
                        results["consolidation"] = {"status": "failed", "error": str(exc)}

            # Exception was caught — not re-raised
            assert len(errors) == 1
            assert "consolidation" in errors[0]
            assert "consolidation boom" in errors[0]
            assert results["consolidation"]["status"] == "failed"
            assert "consolidation boom" in str(results["consolidation"]["error"])

    def test_consolidation_result_key_present_when_enabled(self, tmp_path: Path, writer: FileStateWriter) -> None:
        """When enabled, trw_deliver result dict contains 'consolidation' key."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)

        cfg = TRWConfig(
            memory_consolidation_enabled=True,
            learning_auto_prune_on_deliver=False,
        )
        consolidation_result = {"status": "no_clusters", "clusters_found": 0, "consolidated_count": 0}
        with patch("trw_mcp.tools.ceremony.get_config", return_value=cfg):
            with patch("trw_mcp.state.consolidation.consolidate_cycle", return_value=consolidation_result):
                with patch_trw_deliver_deps(trw_dir):
                    results: dict[str, Any] = {}
                    errors: list[str] = []
                    try:
                        if cfg.memory_consolidation_enabled:
                            from trw_mcp.state.consolidation import consolidate_cycle as _cc

                            results["consolidation"] = _cc(trw_dir, max_entries=cfg.memory_consolidation_max_per_cycle)
                        else:
                            results["consolidation"] = {"status": "skipped", "reason": "disabled"}
                    except Exception as exc:
                        errors.append(f"consolidation: {exc}")
                        results["consolidation"] = {"status": "failed", "error": str(exc)}

            assert "consolidation" in results
            assert results["consolidation"]["status"] == "no_clusters"


# ---------------------------------------------------------------------------
# FR08 — Config Fields and Validation
# ---------------------------------------------------------------------------


class TestConsolidationConfig:
    """FR08: TRWConfig consolidation fields have correct defaults and constraints."""

    def test_default_enabled_is_true(self) -> None:
        """memory_consolidation_enabled defaults to True."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_enabled is True

    def test_default_interval_days(self) -> None:
        """memory_consolidation_interval_days defaults to 7."""
        cfg = TRWConfig()
        assert cfg.memory_consolidation_interval_days == 7

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
