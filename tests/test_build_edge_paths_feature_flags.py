"""Feature-flag skip edge paths for build tool registration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from fastmcp import FastMCP

from tests._build_edge_paths_support import _get_tool_fn
from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import register_build_tools


class TestDisabledFeatureFlags:
    """Tests for disabled feature flags returning skipped status."""

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_mutations_disabled_returns_skipped(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 905: scope='mutations' with mutation_enabled=False returns skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(
            build_check_enabled=True,
            build_check_timeout_secs=300,
            mutation_enabled=False,
        )

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="mutations")
        assert result["status"] == "skipped"
        assert "mutation_enabled" in str(result["reason"])

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_deps_disabled_returns_skipped(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 917: scope='deps' with dep_audit_enabled=False returns skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(
            build_check_enabled=True,
            build_check_timeout_secs=300,
            dep_audit_enabled=False,
        )

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="deps")
        assert result["status"] == "skipped"
        assert "dep_audit_enabled" in str(result["reason"])

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_api_fuzz_disabled_returns_skipped(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Line 924: scope='api' with api_fuzz_enabled=False returns skipped."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = tmp_path
        mock_get_config.return_value = TRWConfig(
            build_check_enabled=True,
            build_check_timeout_secs=300,
            api_fuzz_enabled=False,
        )

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="api")
        assert result["status"] == "skipped"
        assert "api_fuzz_enabled" in str(result["reason"])
