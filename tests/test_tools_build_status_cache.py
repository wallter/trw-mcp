"""Tests for build verification gate models and cache persistence."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests._tools_build_support import _write_build_cache  # noqa: F401
from trw_mcp.models.build import BuildStatus
from trw_mcp.tools.build import cache_build_status


class TestBuildStatusModel:
    """Tests for the BuildStatus Pydantic model."""

    def test_defaults(self) -> None:
        status = BuildStatus()
        assert status.tests_passed is False
        assert status.mypy_clean is False
        assert status.coverage_pct == 0.0
        assert status.test_count == 0
        assert status.failure_count == 0
        assert status.failures == []
        assert status.scope == "full"
        assert status.duration_secs == 0.0

    def test_full_status(self) -> None:
        status = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=92.5,
            test_count=150,
            failure_count=0,
            failures=[],
            scope="full",
            duration_secs=45.3,
        )
        assert status.tests_passed is True
        assert status.coverage_pct == 92.5
        assert status.test_count == 150

    def test_coverage_bounds(self) -> None:
        with pytest.raises(ValidationError):
            BuildStatus(coverage_pct=101.0)
        with pytest.raises(ValidationError):
            BuildStatus(coverage_pct=-1.0)

    def test_serializable(self) -> None:
        status = BuildStatus(tests_passed=True, coverage_pct=88.0)
        data = status.model_dump()
        assert isinstance(data, dict)
        assert data["tests_passed"] is True
        assert data["coverage_pct"] == 88.0


class TestStripAnsi:
    """Tests for ANSI escape code stripping."""

    def test_plain_text(self) -> None:
        assert _strip_ansi("hello world") == "hello world"

    def test_colored_text(self) -> None:
        assert _strip_ansi("\x1b[31mFAILED\x1b[0m test_foo") == "FAILED test_foo"

    def test_bold_text(self) -> None:
        assert _strip_ansi("\x1b[1m5 passed\x1b[0m") == "5 passed"


class TestCacheBuildStatus:
    """Tests for build status caching to .trw/context/."""

    def test_write_and_read(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        status = BuildStatus(
            tests_passed=True,
            mypy_clean=True,
            coverage_pct=90.0,
            test_count=100,
        )
        path = cache_build_status(trw_dir, status)
        assert path.exists()
        assert path.name == "build-status.yaml"

        from trw_mcp.state.persistence import FileStateReader

        data = FileStateReader().read_yaml(path)
        assert data["tests_passed"] is True
        assert data["coverage_pct"] == 90.0

    def test_creates_context_dir(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        status = BuildStatus()
        path = cache_build_status(trw_dir, status)
        assert path.parent.name == "context"
        assert path.exists()
