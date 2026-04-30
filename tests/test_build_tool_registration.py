"""Split tests for build tool registration paths."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import get_tools_sync
from trw_mcp.models.config import TRWConfig

pytest.importorskip(
    "trw_mcp.tools.build._subprocess",
    reason="PRD-CORE-098: subprocess modules removed — these tests are obsolete, see test_build_check_reporter.py",
)


@pytest.mark.skip(
    reason="Patches run_build_check which was removed in PRD-CORE-098 — see test_build_check_reporter.py for new API tests"
)
class TestTrwBuildCheckTool:
    """Tests for the trw_build_check MCP tool closure (OBSOLETE — PRD-CORE-098)."""

    def _get_tool(self) -> object:
        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)
        return server

    def test_build_check_disabled_returns_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = TRWConfig(build_check_enabled=False)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build._registration.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build._registration.resolve_project_root", return_value=tmp_path),
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(scope="full", run_path=None, timeout_secs=None)

        assert result["status"] == "skipped"
        assert "build_check_enabled" in result["reason"]

    def test_build_check_runs_and_caches(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".trw").mkdir()

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build._registration.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build._registration.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build._registration.run_build_check") as mock_rbc,
            patch("trw_mcp.tools.build._registration.cache_build_status") as mock_cache,
        ):
            from trw_mcp.models.build import BuildStatus

            mock_status = BuildStatus(
                tests_passed=True,
                mypy_clean=True,
                coverage_pct=92.0,
                test_count=50,
                failure_count=0,
                scope="full",
                duration_secs=10.0,
            )
            mock_rbc.return_value = mock_status
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(scope="full", run_path=None, timeout_secs=None)

        assert result["tests_passed"] is True
        assert result["mypy_clean"] is True
        assert result["coverage_pct"] == 92.0
        assert result["test_count"] == 50
        assert "cache_path" in result

    def test_build_check_with_run_path_and_events(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".trw").mkdir()

        run_dir = tmp_path / "runs" / "test-run"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build._registration.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build._registration.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build._registration.run_build_check") as mock_rbc,
            patch("trw_mcp.tools.build._registration.cache_build_status") as mock_cache,
        ):
            from trw_mcp.models.build import BuildStatus

            mock_status = BuildStatus(
                tests_passed=True,
                mypy_clean=True,
                coverage_pct=88.0,
                test_count=30,
                failure_count=0,
                scope="pytest",
                duration_secs=5.0,
            )
            mock_rbc.return_value = mock_status
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                scope="pytest",
                run_path=str(run_dir),
                timeout_secs=60,
            )

        assert result["tests_passed"] is True
        assert result["scope"] == "pytest"
        events_path = meta_dir / "events.jsonl"
        assert events_path.exists()

        with events_path.open() as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        assert any(e.get("event") == "build_check_complete" for e in events)

    def test_build_check_run_path_no_meta_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".trw").mkdir()
        run_dir = tmp_path / "runs" / "nonexistent-run"

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        with (
            patch("trw_mcp.tools.build._registration.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build._registration.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build._registration.run_build_check") as mock_rbc,
            patch("trw_mcp.tools.build._registration.cache_build_status") as mock_cache,
        ):
            from trw_mcp.models.build import BuildStatus

            mock_status = BuildStatus(
                tests_passed=False,
                mypy_clean=True,
                coverage_pct=70.0,
                test_count=10,
                failure_count=2,
                scope="full",
                duration_secs=3.0,
            )
            mock_rbc.return_value = mock_status
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                scope="full",
                run_path=str(run_dir),
                timeout_secs=None,
            )

        assert result["tests_passed"] is False
        assert result["failure_count"] == 2

    def test_build_check_timeout_capped_at_600(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".trw").mkdir()
        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        captured_timeout: list[int] = []

        with (
            patch("trw_mcp.tools.build._registration.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build._registration.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build._registration.cache_build_status") as mock_cache,
            patch("trw_mcp.tools.build._registration.run_build_check") as mock_rbc,
        ):
            from trw_mcp.models.build import BuildStatus

            def capture_rbc(proj_root: Path, **kwargs: object) -> BuildStatus:
                captured_timeout.append(int(str(kwargs.get("timeout_secs", 0))))
                return BuildStatus(
                    tests_passed=True,
                    mypy_clean=True,
                    coverage_pct=80.0,
                    test_count=5,
                    scope="full",
                    duration_secs=1.0,
                )

            mock_rbc.side_effect = capture_rbc
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            tool.fn(scope="full", run_path=None, timeout_secs=9999)

        assert captured_timeout[0] == 600

    def test_build_check_uses_config_default_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / ".trw").mkdir()
        config = TRWConfig(build_check_enabled=True, build_check_timeout_secs=120)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        from fastmcp import FastMCP

        from trw_mcp.tools.build import register_build_tools

        server = FastMCP("test")
        register_build_tools(server)

        captured_timeout: list[int] = []

        with (
            patch("trw_mcp.tools.build._registration.resolve_trw_dir", return_value=tmp_path / ".trw"),
            patch("trw_mcp.tools.build._registration.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.tools.build._registration.cache_build_status") as mock_cache,
            patch("trw_mcp.tools.build._registration.run_build_check") as mock_rbc,
        ):
            from trw_mcp.models.build import BuildStatus

            def capture_rbc(proj_root: Path, **kwargs: object) -> BuildStatus:
                captured_timeout.append(int(str(kwargs.get("timeout_secs", 0))))
                return BuildStatus(
                    tests_passed=True,
                    mypy_clean=True,
                    coverage_pct=80.0,
                    test_count=5,
                    scope="full",
                    duration_secs=1.0,
                )

            mock_rbc.side_effect = capture_rbc
            mock_cache.return_value = tmp_path / ".trw" / "context" / "build-status.yaml"

            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            tool.fn(scope="full", run_path=None, timeout_secs=None)

        assert captured_timeout[0] == 120
