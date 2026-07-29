"""Tests for trw_build_check result reporter API (PRD-CORE-098 FR01/FR02).

Verifies the new parameter-based result reporter pattern where agents
run tests via Bash and report results through trw_build_check, instead
of the tool running subprocesses itself.

Most cases below use conftest's ``build_check_invoke`` fixture rather than
re-implementing the server + ``resolve_trw_dir`` redirect by hand. The two that
still do it longhand need a non-default ``TRWConfig``, which the fixture does
not take.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError

from tests.conftest import get_tools_sync, make_test_server
from trw_mcp.models.config import TRWConfig


class TestBuildCheckReporterAPI:
    """Tests for the trw_build_check result reporter signature."""

    def test_build_check_accepts_result_params(self, build_check_invoke: Any) -> None:
        """FR01: Call with tests_passed=True, test_count=47, verify returns dict."""
        result = build_check_invoke(
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

    def test_build_check_tests_passed_false(self, build_check_invoke: Any) -> None:
        """FR01: tests_passed=False with failures produces correct result."""
        result = build_check_invoke(
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
                    r"failure_count=0, coverage_pct=92.3, static_checks_clean=True, "
                    r"scope='pytest tests/'\)"
                ),
            ):
                asyncio.run(server.call_tool("trw_build_check", {}))

    def test_build_check_logs_event(self, tmp_path: Path, build_check_invoke: Any) -> None:
        """FR02: build_check_complete event is logged to events.jsonl."""
        run_dir = tmp_path / "runs" / "test-run"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        events_file = meta_dir / "events.jsonl"

        with patch(
            "trw_mcp.tools.build._registration.find_active_run",
            return_value=run_dir,
        ):
            build_check_invoke(tests_passed=True, test_count=10, coverage_pct=85.0)

        assert events_file.exists(), "events.jsonl should be created"
        events = [json.loads(line) for line in events_file.read_text().splitlines() if line.strip()]
        assert any(e.get("event") == "build_check_complete" for e in events), (
            f"Expected build_check_complete event, got: {events}"
        )
        build_event = next(e for e in events if e.get("event") == "build_check_complete")
        assert build_event["tests_passed"] is True
        assert build_event["static_checks_clean"] is True

    def test_build_check_caches_to_yaml(self, tmp_path: Path, build_check_invoke: Any) -> None:
        """FR02: BuildStatus is cached via cache_build_status."""
        with patch(
            "trw_mcp.tools.build._registration.cache_build_status",
            return_value=tmp_path / ".trw" / "context" / "build-status.yaml",
        ) as mock_cache:
            result = build_check_invoke(tests_passed=True, test_count=25)

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


class TestMinCoverageThreshold:
    """``min_coverage`` is the one parameter that can flip a reported pass to a fail.

    The negative cases are the point and are asserted separately: an
    over-eager threshold check that flagged a *meeting* build, or one that fired
    when no threshold was requested, would turn honest green runs red. Those two
    claims were rescued from ``test_tools_build_config.py``'s obsolete
    ``TestMinCoverageThreshold``, which mocked the long-deleted
    ``run_build_check`` and had therefore been skipped since PRD-CORE-098.
    """

    def test_below_threshold_flips_tests_passed_to_false(self, build_check_invoke: Any) -> None:
        result = build_check_invoke(tests_passed=True, test_count=50, coverage_pct=60.0, min_coverage=80.0)
        assert result["tests_passed"] is False
        assert result["coverage_threshold_failed"] is True
        assert result["coverage_threshold"] == 80.0
        assert "60.0%" in str(result["coverage_threshold_message"])

    @pytest.mark.parametrize(
        ("coverage_pct", "min_coverage", "why"),
        [
            (90.0, 80.0, "comfortably above the threshold"),
            (80.0, 80.0, "exactly at the threshold — the boundary is inclusive"),
            (50.0, None, "no threshold requested, so low coverage is not a failure"),
        ],
    )
    def test_threshold_not_flagged(
        self,
        build_check_invoke: Any,
        coverage_pct: float,
        min_coverage: float | None,
        why: str,
    ) -> None:
        result = build_check_invoke(
            tests_passed=True,
            test_count=100,
            coverage_pct=coverage_pct,
            min_coverage=min_coverage,
        )
        assert result["tests_passed"] is True, why
        assert "coverage_threshold_failed" not in result, why


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
