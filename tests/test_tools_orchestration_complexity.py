"""Complexity classification orchestration tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401
from trw_mcp.state.persistence import FileStateReader


class TestTrwInitComplexity:
    """Tests for trw_init complexity classification wiring (FR04, FR08)."""

    def test_init_without_signals_backward_compat(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """NFR02: trw_init without complexity_signals preserves existing behavior."""
        result = orch_tools["trw_init"].fn(task_name="no-signals")
        assert result["status"] == "initialized"
        assert "complexity_class" not in result

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml.get("complexity_class") is None
        assert run_yaml.get("complexity_signals") is None
        assert run_yaml.get("phase_requirements") is None

    def test_init_with_minimal_signals(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """FR08: trw_init with simple signals classifies as MINIMAL."""
        result = orch_tools["trw_init"].fn(
            task_name="minimal-task",
            complexity_signals={"files_affected": 1},
        )
        assert result["complexity_class"] == "MINIMAL"

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_class"] == "MINIMAL"
        assert run_yaml["complexity_signals"]["files_affected"] == 1
        assert run_yaml["phase_requirements"]["mandatory"] == ["IMPLEMENT", "VALIDATE", "DELIVER"]
        assert "RESEARCH" in run_yaml["phase_requirements"]["skipped"]

    def test_init_with_comprehensive_signals(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """FR08: trw_init with complex signals classifies as COMPREHENSIVE."""
        result = orch_tools["trw_init"].fn(
            task_name="complex-task",
            complexity_signals={
                "files_affected": 5,
                "novel_patterns": True,
                "cross_cutting": True,
            },
        )
        assert result["complexity_class"] == "COMPREHENSIVE"

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_class"] == "COMPREHENSIVE"
        assert len(run_yaml["phase_requirements"]["mandatory"]) == 6

    def test_init_with_hard_override(
        self,
        orch_tools: dict[str, Any],
    ) -> None:
        """FR05/FR09: trw_init with 2 risk signals records override."""
        result = orch_tools["trw_init"].fn(
            task_name="secure-task",
            complexity_signals={
                "files_affected": 1,
                "security_change": True,
                "data_migration": True,
            },
        )
        assert result["complexity_class"] == "COMPREHENSIVE"

        reader = FileStateReader()
        run_yaml = reader.read_yaml(Path(result["run_path"]) / "meta" / "run.yaml")
        assert run_yaml["complexity_override"] is not None
        assert "hard override" in run_yaml["complexity_override"]["reason"]
        assert "security_change" in run_yaml["complexity_override"]["signals"]
