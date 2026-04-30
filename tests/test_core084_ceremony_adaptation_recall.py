"""Tests for PRD-CORE-084 light-mode recall capping."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP


class TestRecallCappingLightMode:
    """FR05: Light mode caps recall results to LIGHT_MODE_RECALL_CAP."""

    def test_light_mode_caps_recall_results(self, tmp_path: Path) -> None:
        """With ceremony_mode=light and 25 learnings, at most 10 are returned."""
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)

        config = TRWConfig(
            trw_dir=str(trw_dir),
            ceremony_mode="light",
            recall_max_results=25,
        )

        all_learnings = [{"id": f"L-{i:04d}", "summary": f"Learning {i}", "impact": 0.8} for i in range(25)]
        reader = FileStateReader()

        def mock_recall(
            trw_dir_arg: Path,
            query: str = "*",
            *,
            tags: list[str] | None = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            status: str | None = None,
        ) -> list[dict[str, object]]:
            """Return learnings capped to max_results."""
            return all_learnings[:max_results]

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=mock_recall),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, _auto, _extra = perform_session_recalls(
                trw_dir,
                "",
                config,
                reader,
            )

        assert len(learnings) <= LIGHT_MODE_RECALL_CAP

    def test_full_mode_uses_configured_recall_max(self, tmp_path: Path) -> None:
        """With ceremony_mode=full, recall_max_results is used as-is."""
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)

        config = TRWConfig(
            trw_dir=str(trw_dir),
            ceremony_mode="full",
            recall_max_results=25,
        )

        all_learnings = [{"id": f"L-{i:04d}", "summary": f"Learning {i}", "impact": 0.8} for i in range(25)]
        reader = FileStateReader()

        def mock_recall(
            trw_dir_arg: Path,
            query: str = "*",
            *,
            tags: list[str] | None = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            status: str | None = None,
        ) -> list[dict[str, object]]:
            """Return learnings capped to max_results."""
            return all_learnings[:max_results]

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=mock_recall),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, _auto, _extra = perform_session_recalls(
                trw_dir,
                "",
                config,
                reader,
            )

        assert len(learnings) == 25

    def test_light_mode_effective_max_calculation(self) -> None:
        """Verify effective_max = min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)."""
        config = TRWConfig(ceremony_mode="light", recall_max_results=25)
        effective_max = min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        assert effective_max == LIGHT_MODE_RECALL_CAP

    def test_light_mode_respects_lower_configured_max(self) -> None:
        """If recall_max_results < LIGHT_MODE_RECALL_CAP, the lower value is used."""
        config = TRWConfig(ceremony_mode="light", recall_max_results=5)
        effective_max = min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        assert effective_max == 5

    def test_light_mode_focused_recall_also_capped(self, tmp_path: Path) -> None:
        """Focused (non-empty query) recall is also capped in light mode."""
        from trw_mcp.state.persistence import FileStateReader
        from trw_mcp.tools._ceremony_helpers import perform_session_recalls

        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir(parents=True, exist_ok=True)

        config = TRWConfig(
            trw_dir=str(trw_dir),
            ceremony_mode="light",
            recall_max_results=25,
        )

        all_learnings = [{"id": f"L-{i:04d}", "summary": f"Learning {i}", "impact": 0.8} for i in range(25)]
        reader = FileStateReader()
        captured_max_results: list[int] = []

        def mock_recall(
            trw_dir_arg: Path,
            query: str = "*",
            *,
            tags: list[str] | None = None,
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            status: str | None = None,
        ) -> list[dict[str, object]]:
            captured_max_results.append(max_results)
            return all_learnings[:max_results]

        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=mock_recall),
            patch("trw_mcp.state.memory_adapter.update_access_tracking"),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
        ):
            learnings, _auto, _extra = perform_session_recalls(
                trw_dir,
                "testing query",
                config,
                reader,
            )

        assert all(mr <= LIGHT_MODE_RECALL_CAP for mr in captured_max_results)
        assert len(learnings) <= LIGHT_MODE_RECALL_CAP
