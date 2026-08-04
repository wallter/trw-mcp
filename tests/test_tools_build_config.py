"""Tests for build verification config fields.

Two obsolete classes were removed here: ``TestBuildConfigWiring`` and
``TestMinCoverageThreshold`` both mocked ``run_build_check``, deleted by
PRD-CORE-098 when ``trw_build_check`` became a reporter. Their two surviving
NEGATIVE claims about ``min_coverage`` — that meeting the threshold and
omitting it both leave the result unflagged — are not lost: they were ported to
``test_build_check_reporter.py`` against the live reporter API.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig


class TestBuildConfig:
    """Tests for build-related config fields."""

    def test_defaults(self) -> None:
        config = TRWConfig()
        assert config.build_check_enabled is True
        assert config.build_check_timeout_secs == 300
        assert config.build_check_coverage_min == 85.0
        assert config.build_gate_enforcement == "lenient"
        assert config.build_check_pytest_args == ""
        assert config.build_check_mypy_args == "--strict"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_BUILD_CHECK_ENABLED", "false")
        monkeypatch.setenv("TRW_BUILD_GATE_ENFORCEMENT", "strict")
        config = TRWConfig()
        assert config.build_check_enabled is False
        assert config.build_gate_enforcement == "strict"
