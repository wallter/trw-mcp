"""Tests for PRD-QUAL-002: Configurable trw_init task directory root."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateReader


@pytest.fixture
def _mock_project_root(tmp_path: Path) -> None:
    """Patch resolve_project_root to return tmp_path."""
    # Create minimal .trw structure so init doesn't fail
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)


class TestTaskRoot:
    """Tests for configurable task_root in trw_init."""

    def test_init_custom_task_root(
        self, tmp_path: Path, _mock_project_root: None,
    ) -> None:
        """Verify run is created under custom task_root path."""
        with patch(
            "trw_mcp.tools.orchestration.resolve_project_root",
            return_value=tmp_path,
        ):
            from trw_mcp.tools.orchestration import register_orchestration_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_orchestration_tools(server)

            # Access the inner function
            init_fn = server._tool_manager._tools["trw_init"].fn  # type: ignore[attr-defined]
            result = init_fn(
                task_name="my-task",
                task_root="custom-root",
            )

            run_path = Path(result["run_path"])
            assert run_path.exists()
            assert "custom-root" in str(run_path)
            assert "my-task" in str(run_path)
            # Verify it's NOT under docs/
            assert "/docs/" not in str(run_path)

    def test_init_default_task_root_unchanged(
        self, tmp_path: Path, _mock_project_root: None,
    ) -> None:
        """Verify default behavior uses docs/ for backward compatibility."""
        with patch(
            "trw_mcp.tools.orchestration.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.orchestration._config",
            TRWConfig(task_root="docs"),
        ):
            from trw_mcp.tools.orchestration import register_orchestration_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_orchestration_tools(server)

            init_fn = server._tool_manager._tools["trw_init"].fn  # type: ignore[attr-defined]
            result = init_fn(task_name="my-task")

            run_path = Path(result["run_path"])
            assert run_path.exists()
            assert "/docs/" in str(run_path)

    def test_init_task_root_from_config(
        self, tmp_path: Path, _mock_project_root: None,
    ) -> None:
        """Verify config field is used when param is absent."""
        custom_config = TRWConfig(task_root="configured-root")
        with patch(
            "trw_mcp.tools.orchestration.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.orchestration._config",
            custom_config,
        ):
            from trw_mcp.tools.orchestration import register_orchestration_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_orchestration_tools(server)

            init_fn = server._tool_manager._tools["trw_init"].fn  # type: ignore[attr-defined]
            result = init_fn(task_name="my-task")

            run_path = Path(result["run_path"])
            assert run_path.exists()
            assert "configured-root" in str(run_path)

    def test_init_task_root_param_overrides_config(
        self, tmp_path: Path, _mock_project_root: None,
    ) -> None:
        """Verify explicit param overrides config field."""
        custom_config = TRWConfig(task_root="from-config")
        with patch(
            "trw_mcp.tools.orchestration.resolve_project_root",
            return_value=tmp_path,
        ), patch(
            "trw_mcp.tools.orchestration._config",
            custom_config,
        ):
            from trw_mcp.tools.orchestration import register_orchestration_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_orchestration_tools(server)

            init_fn = server._tool_manager._tools["trw_init"].fn  # type: ignore[attr-defined]
            result = init_fn(
                task_name="my-task",
                task_root="from-param",
            )

            run_path = Path(result["run_path"])
            assert run_path.exists()
            assert "from-param" in str(run_path)
            assert "from-config" not in str(run_path)

    def test_init_task_root_in_variables(
        self, tmp_path: Path, _mock_project_root: None,
    ) -> None:
        """Verify TASK_ROOT is included in run variables."""
        reader = FileStateReader()
        with patch(
            "trw_mcp.tools.orchestration.resolve_project_root",
            return_value=tmp_path,
        ):
            from trw_mcp.tools.orchestration import register_orchestration_tools
            from fastmcp import FastMCP

            server = FastMCP("test")
            register_orchestration_tools(server)

            init_fn = server._tool_manager._tools["trw_init"].fn  # type: ignore[attr-defined]
            result = init_fn(
                task_name="my-task",
                task_root="custom",
            )

            # Read run.yaml and check variables
            run_path = Path(result["run_path"])
            run_yaml = reader.read_yaml(run_path / "meta" / "run.yaml")
            variables = run_yaml.get("variables", {})
            assert isinstance(variables, dict)
            assert variables.get("TASK_ROOT") == "custom"
