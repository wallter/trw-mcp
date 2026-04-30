from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ._mutations_support import _get_tool_fn, _setup_build_tool_mocks


@pytest.mark.integration
class TestBuildCheckScopeIntegration:
    """Integration tests: trw_build_check MCP tool with scope='mutations','deps','api'."""

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.mutations.run_mutation_check")
    @patch("trw_mcp.tools.mutations.cache_mutation_status")
    def test_scope_mutations_calls_mutation_check_and_caches(
        self,
        mock_cache: MagicMock,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='mutations' → run_mutation_check + cache_mutation_status called."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_get_config, tmp_path, mutation_enabled=True)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        mut_result: dict[str, object] = {
            "mutation_passed": True,
            "mutation_score": 0.75,
            "mutation_tier": "standard",
        }
        mock_run.return_value = mut_result
        mock_cache.return_value = trw_dir / "context" / "mutation-status.yaml"

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="mutations")
        mock_run.assert_called_once()
        mock_cache.assert_called_once()
        assert result.get("mutation_passed") is True

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration._run_dep_audit")
    @patch("trw_mcp.tools.build._registration._cache_to_context")
    def test_scope_deps_calls_dep_audit_and_caches(
        self,
        mock_cache: MagicMock,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='deps' → _run_dep_audit + _cache_to_context called."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_get_config, tmp_path, dep_audit_enabled=True)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        dep_result: dict[str, object] = {
            "dep_audit_passed": True,
            "pip_audit_passed": True,
        }
        mock_run.return_value = dep_result
        mock_cache.return_value = trw_dir / "context" / "dep-audit.yaml"

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="deps")
        mock_run.assert_called_once()
        mock_cache.assert_called_once()
        assert result.get("dep_audit_passed") is True

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration._run_api_fuzz")
    @patch("trw_mcp.tools.build._registration._cache_to_context")
    def test_scope_api_calls_api_fuzz_and_caches(
        self,
        mock_cache: MagicMock,
        mock_run: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='api' → _run_api_fuzz + _cache_to_context called."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_get_config, tmp_path, api_fuzz_enabled=True)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        fuzz_result: dict[str, object] = {
            "api_fuzz_skipped": True,
            "api_fuzz_skip_reason": "schemathesis not installed",
        }
        mock_run.return_value = fuzz_result
        mock_cache.return_value = trw_dir / "context" / "api-fuzz-status.yaml"

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="api")
        mock_run.assert_called_once()
        mock_cache.assert_called_once()
        assert result.get("api_fuzz_skipped") is True

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration.run_build_check")
    @patch("trw_mcp.tools.build._registration._run_dep_audit")
    @patch("trw_mcp.tools.build._registration._cache_to_context")
    def test_scope_full_includes_dep_audit_when_enabled(
        self,
        mock_cache_dep: MagicMock,
        mock_dep_audit: MagicMock,
        mock_build: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='full' + dep_audit_enabled=True → _run_dep_audit is called."""
        from trw_mcp.models.build import BuildStatus

        trw_dir, proj_root = _setup_build_tool_mocks(mock_get_config, tmp_path, dep_audit_enabled=True)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        mock_build.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=90.0,
            test_count=100,
        )
        dep_result: dict[str, object] = {
            "dep_audit_passed": True,
            "pip_audit_passed": True,
        }
        mock_dep_audit.return_value = dep_result
        mock_cache_dep.return_value = trw_dir / "context" / "dep-audit.yaml"

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="full")
        mock_dep_audit.assert_called_once()
        mock_cache_dep.assert_called_once()
        assert "dep_audit" in result

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    @patch("trw_mcp.tools.build._registration.run_build_check")
    def test_scope_full_skips_dep_audit_when_disabled(
        self,
        mock_build: MagicMock,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='full' + dep_audit_enabled=False → _run_dep_audit NOT called."""
        from trw_mcp.models.build import BuildStatus

        trw_dir, proj_root = _setup_build_tool_mocks(mock_get_config, tmp_path, dep_audit_enabled=False)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        mock_build.return_value = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=85.0,
            test_count=50,
        )

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="full")
        assert "dep_audit" not in result

    @patch("trw_mcp.tools.build._registration.get_config")
    @patch("trw_mcp.tools.build._registration.resolve_project_root")
    @patch("trw_mcp.tools.build._registration.resolve_trw_dir")
    def test_scope_mutations_returns_skipped_when_disabled(
        self,
        mock_trw_dir: MagicMock,
        mock_proj_root: MagicMock,
        mock_get_config: MagicMock,
        tmp_path: Path,
    ) -> None:
        """scope='mutations' returns result dict (even if skipped) without crashing."""
        trw_dir, proj_root = _setup_build_tool_mocks(mock_get_config, tmp_path)
        mock_trw_dir.return_value = trw_dir
        mock_proj_root.return_value = proj_root

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        tool_fn = _get_tool_fn(server)

        result = tool_fn(scope="mutations")
        assert result["status"] == "skipped"
        assert "mutation_enabled" in str(result["reason"])
