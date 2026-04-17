"""Tests for trw_build_check result reporter API (PRD-CORE-098 FR01/FR02).

Verifies the new parameter-based result reporter pattern where agents
run tests via Bash and report results through trw_build_check, instead
of the tool running subprocesses itself.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import TRWConfig


class TestBuildCheckReporterAPI:
    """Tests for the trw_build_check result reporter signature."""

    def test_build_check_accepts_result_params(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR01: Call with tests_passed=True, test_count=47, verify returns dict."""
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=None,
            ),
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                tests_passed=True,
                test_count=47,
                coverage_pct=91.5,
                mypy_clean=True,
                scope="full",
            )

        assert isinstance(result, dict)
        assert result["tests_passed"] is True
        assert result["test_count"] == 47
        assert result["coverage_pct"] == 91.5
        assert result["mypy_clean"] is True
        assert result["scope"] == "full"
        assert "cache_path" in result

    def test_build_check_tests_passed_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR01: tests_passed=False with failures produces correct result."""
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=None,
            ),
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                tests_passed=False,
                test_count=50,
                failure_count=3,
                failures=["test_a FAILED", "test_b FAILED", "test_c FAILED"],
            )

        assert result["tests_passed"] is False
        assert result["failure_count"] == 3
        assert len(result["failures"]) == 3

    def test_build_check_missing_tests_passed_returns_guidance(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FR01: Missing tests_passed returns usage guidance, not generic validation."""
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=None,
            ),
        ):
            with pytest.raises(
                ToolError,
                match=(
                    r"tests_passed is required.*"
                    r"trw_build_check\(tests_passed=True, test_count=47, "
                    r"coverage_pct=92.3, mypy_clean=True, scope='full'\)"
                ),
            ):
                asyncio.run(server.call_tool("trw_build_check", {}))

    def test_build_check_logs_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR02: build_check_complete event is logged to events.jsonl."""
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        # Create run directory with meta/events.jsonl
        run_dir = tmp_path / "runs" / "test-run"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        events_file = meta_dir / "events.jsonl"

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=run_dir,
            ),
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            tool.fn(
                tests_passed=True,
                test_count=10,
                coverage_pct=85.0,
            )

        assert events_file.exists(), "events.jsonl should be created"
        events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
        assert any(e.get("event") == "build_check_complete" for e in events), (
            f"Expected build_check_complete event, got: {events}"
        )

    def test_build_check_caches_to_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR02: BuildStatus is cached via cache_build_status."""
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=None,
            ),
            patch(
                "trw_mcp.tools.build._registration.cache_build_status",
                return_value=tmp_path / ".trw" / "context" / "build-status.yaml",
            ) as mock_cache,
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(tests_passed=True, test_count=25)

        # Verify cache_build_status was called with a BuildStatus instance
        mock_cache.assert_called_once()
        cached_status = mock_cache.call_args[0][1]
        assert cached_status.tests_passed is True
        assert cached_status.test_count == 25
        assert "cache_path" in result

    def test_build_check_disabled_returns_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """build_check_enabled=False returns skipped status."""
        config = TRWConfig(build_check_enabled=False)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(tests_passed=True)

        assert result["status"] == "skipped"
        assert "build_check_enabled" in result["reason"]

    def test_build_check_coverage_threshold_enforcement(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """min_coverage param forces tests_passed=False when coverage is too low."""
        (tmp_path / ".trw" / "context").mkdir(parents=True)

        config = TRWConfig(build_check_enabled=True)
        monkeypatch.setattr("trw_mcp.tools.build._registration.get_config", lambda: config)

        server = make_test_server("build")

        with (
            patch(
                "trw_mcp.tools.build._registration.resolve_trw_dir",
                return_value=tmp_path / ".trw",
            ),
            patch(
                "trw_mcp.tools.build._registration.find_active_run",
                return_value=None,
            ),
        ):
            tools_dict = get_tools_sync(server)
            tool = tools_dict["trw_build_check"]
            result = tool.fn(
                tests_passed=True,
                test_count=50,
                coverage_pct=60.0,
                min_coverage=80.0,
            )

        assert result["tests_passed"] is False
        assert result["coverage_threshold_failed"] is True
        assert result["coverage_threshold"] == 80.0


class TestNoSubprocessImports:
    """Verify _registration.py and _core.py have no subprocess runner imports."""

    def test_registration_has_no_subprocess_runner_imports(self) -> None:
        """_registration.py must not import from _runners or _subprocess."""
        import inspect

        from trw_mcp.tools.build import _registration

        source = inspect.getsource(_registration)
        assert "_runners" not in source, "_registration.py still references _runners"
        assert "_subprocess" not in source, "_registration.py still references _subprocess"

    def test_core_has_no_subprocess_runner_imports(self) -> None:
        """_core.py must not import from _runners or _subprocess."""
        import inspect

        from trw_mcp.tools.build import _core

        source = inspect.getsource(_core)
        assert "_runners" not in source, "_core.py still references _runners"
        assert "_subprocess" not in source, "_core.py still references _subprocess"

    def test_registration_has_no_ceremony_nudge_references(self) -> None:
        """PRD-CORE-098: build reporter should not depend on ceremony nudges."""
        import inspect

        from trw_mcp.tools.build import _registration

        source = inspect.getsource(_registration)
        assert "ceremony_nudge" not in source
        assert "append_ceremony_nudge" not in source
        assert "mark_build_check" not in source

    def test_core_has_no_run_build_check(self) -> None:
        """_core.py must not export run_build_check."""
        from trw_mcp.tools.build import _core

        assert not hasattr(_core, "run_build_check"), "_core.py still has run_build_check function"
