"""Tests for trw_mcp.api — public API surface module.

Verifies that all 10 curated public types from FR01 are importable from
trw_mcp.api and are the correct classes/functions from their canonical
source modules.
"""

from __future__ import annotations


class TestApiSurfaceFR01:
    """FR01: Verify api package exposes the 10 curated public types."""

    def test_trwconfig_is_canonical(self) -> None:
        from trw_mcp.api import TRWConfig
        from trw_mcp.models.config import TRWConfig as Canonical

        assert TRWConfig is Canonical

    def test_get_config_is_canonical(self) -> None:
        from trw_mcp.api import get_config
        from trw_mcp.models.config import get_config as canonical

        assert get_config is canonical

    def test_learning_entry_is_canonical(self) -> None:
        from trw_mcp.api import LearningEntry
        from trw_mcp.models.learning import LearningEntry as Canonical

        assert LearningEntry is Canonical

    def test_learning_status_is_canonical(self) -> None:
        from trw_mcp.api import LearningStatus
        from trw_mcp.models.learning import LearningStatus as Canonical

        assert LearningStatus is Canonical

    def test_phase_is_canonical(self) -> None:
        from trw_mcp.api import Phase
        from trw_mcp.models.run import Phase as Canonical

        assert Phase is Canonical

    def test_run_state_is_canonical(self) -> None:
        from trw_mcp.api import RunState
        from trw_mcp.models.run import RunState as Canonical

        assert RunState is Canonical

    def test_event_is_canonical(self) -> None:
        from trw_mcp.api import Event
        from trw_mcp.models.run import Event as Canonical

        assert Event is Canonical

    def test_validation_result_is_canonical(self) -> None:
        from trw_mcp.api import ValidationResult
        from trw_mcp.models.requirements import ValidationResult as Canonical

        assert ValidationResult is Canonical

    def test_trw_error_is_canonical(self) -> None:
        from trw_mcp.api import TRWError
        from trw_mcp.exceptions import TRWError as Canonical

        assert TRWError is Canonical

    def test_state_error_is_canonical(self) -> None:
        from trw_mcp.api import StateError
        from trw_mcp.exceptions import StateError as Canonical

        assert StateError is Canonical

    def test_all_fr01_types_in_all(self) -> None:
        """__all__ contains the 10 FR01 types."""
        from trw_mcp.api import __all__

        fr01_types = {
            "TRWConfig",
            "get_config",
            "LearningEntry",
            "LearningStatus",
            "Phase",
            "RunState",
            "Event",
            "ValidationResult",
            "TRWError",
            "StateError",
        }
        assert fr01_types.issubset(set(__all__))

    def test_existing_scoring_exports_preserved(self) -> None:
        """Existing scoring API exports are not removed."""
        from trw_mcp.api import __all__

        # These were in the original api/__init__.py
        assert "compute_ceremony_score" in __all__
        assert "CeremonyWeights" in __all__
