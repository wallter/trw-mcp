"""Learning-tool wiring tests for impact calibration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.conftest import get_tools_sync


class TestTrwLearnForcedDistributionWiring:
    """Verify enforce_tier_distribution is called and demotions persist."""

    @pytest.fixture(autouse=True)
    def _isolate_project_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(f"id: {fname}\nimpact: {impact}\nstatus: {status}\n")

    def _get_tools(self) -> dict[str, Any]:
        from fastmcp import FastMCP

        from trw_mcp.tools.learning import register_learning_tools

        srv = FastMCP("test")
        register_learning_tools(srv)
        return get_tools_sync(srv)

    def _entries_dir(self, root: Path) -> Path:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        return root / cfg.trw_dir / cfg.learnings_dir / cfg.entries_dir

    def test_demotion_persisted_to_disk(self, tmp_path: Path) -> None:
        """Demoted entries have their impact scores updated on disk."""
        tools = self._get_tools()
        entries_dir = self._entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="New critical learning",
            detail="Detail",
            impact=0.95,
        )
        assert result["status"] == "recorded"

        from trw_mcp.state.persistence import FileStateReader

        reader = FileStateReader()
        impacts = []
        for yaml_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(yaml_file)
            impacts.append(float(str(data.get("impact", 0.5))))
        assert min(impacts) < 0.9

    def test_no_demotion_when_disabled(self, tmp_path: Path) -> None:
        """When impact_forced_distribution_enabled=False, no demotions occur."""
        from trw_mcp.models.config import TRWConfig

        disabled_cfg = TRWConfig().model_copy(update={"impact_forced_distribution_enabled": False})
        with patch("trw_mcp.tools.learning.get_config", return_value=disabled_cfg):
            tools = self._get_tools()
            entries_dir = self._entries_dir(tmp_path)
            for i in range(10):
                self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

            result = tools["trw_learn"].fn(
                summary="Critical",
                detail="Detail",
                impact=0.95,
            )
        assert result["distribution_warning"] == ""

    def test_demotion_warning_contains_tier_name(self, tmp_path: Path) -> None:
        """Distribution warning message names the affected tier."""
        tools = self._get_tools()
        entries_dir = self._entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Critical learning",
            detail="Very important",
            impact=0.95,
        )
        warning = result["distribution_warning"]
        assert warning != ""
        assert "critical" in warning or "high" in warning

    def test_no_demotion_below_impact_threshold(self, tmp_path: Path) -> None:
        """Low-impact learnings (< 0.7) don't trigger distribution enforcement."""
        tools = self._get_tools()
        entries_dir = self._entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Low impact",
            detail="Not important",
            impact=0.5,
        )
        assert result["distribution_warning"] == ""
