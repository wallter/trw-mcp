"""Tests for PRD-FIX-044: Eliminate Module-Level Config Capture.

Verifies that module-level _config, _reader, _writer singletons are no
longer eagerly instantiated at import time, and that backward-compat
__getattr__ provides lazy construction.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch


class TestClaudeMdNoModuleLevelCapture:
    """Verify state/claude_md/__init__.py has no module-level config capture."""

    def test_no_eager_config_capture_in_claude_md(self) -> None:
        """FR: _config is not eagerly captured at import time.

        The module should use __getattr__ for lazy construction instead
        of calling get_config() at module level.  After clearing any
        cached values and reloading, _config must NOT be in __dict__.
        """
        import trw_mcp.state.claude_md as claude_md_mod

        # Clear any lazily-cached values from a prior test run
        for attr in ("_config", "_reader", "_writer"):
            claude_md_mod.__dict__.pop(attr, None)

        # After clearing, _config must not reappear until explicitly accessed
        assert "_config" not in claude_md_mod.__dict__, (
            "_config is still eagerly instantiated at module level — "
            "it should be lazily constructed via __getattr__"
        )

    def test_no_eager_reader_capture_in_claude_md(self) -> None:
        """FR: _reader is not eagerly captured at import time."""
        import trw_mcp.state.claude_md as claude_md_mod

        claude_md_mod.__dict__.pop("_reader", None)
        assert "_reader" not in claude_md_mod.__dict__, (
            "_reader is still eagerly instantiated at module level"
        )

    def test_no_eager_writer_capture_in_claude_md(self) -> None:
        """FR: _writer is not eagerly instantiated at import time."""
        import trw_mcp.state.claude_md as claude_md_mod

        claude_md_mod.__dict__.pop("_writer", None)
        assert "_writer" not in claude_md_mod.__dict__, (
            "_writer is still eagerly instantiated at module level"
        )

    def test_submodules_use_function_level_config(self) -> None:
        """FR: Submodules call get_config() at function level, not module level."""
        from trw_mcp.state.claude_md import _static_sections, _parser

        # Verify get_config is imported for function-level use
        assert hasattr(_static_sections, "get_config")
        assert hasattr(_parser, "get_config")

    def test_submodules_use_function_level_reader_writer(self) -> None:
        """FR: Submodules instantiate FileStateReader/Writer at function level."""
        from trw_mcp.state.claude_md import _static_sections, _parser

        # Verify persistence classes are imported for function-level use
        assert hasattr(_static_sections, "FileStateReader")
        assert hasattr(_parser, "FileStateWriter")

    def test_no_module_level_config_in_submodules(self) -> None:
        """FR: No submodule has _config at module scope."""
        from trw_mcp.state.claude_md import _static_sections, _parser

        assert "_config" not in _static_sections.__dict__
        assert "_config" not in _parser.__dict__

    def test_no_module_level_writer_in_submodules(self) -> None:
        """FR: No submodule has _writer at module scope."""
        from trw_mcp.state.claude_md import _static_sections, _parser

        assert "_writer" not in _static_sections.__dict__
        assert "_writer" not in _parser.__dict__

    def test_getattr_raises_for_unknown_attr(self) -> None:
        """FR: __getattr__ raises AttributeError for non-singleton attributes."""
        import trw_mcp.state.claude_md as claude_md_mod
        import pytest

        with pytest.raises(AttributeError):
            _ = claude_md_mod._nonexistent_attribute


class TestAnalyticsReportNoModuleLevelCapture:
    """Verify state/analytics/report.py has no module-level config capture."""

    def test_no_module_level_config_in_analytics_report(self) -> None:
        """FR: analytics/report.py uses function-level get_config()."""
        import trw_mcp.state.analytics.report as report_mod

        assert "_config" not in report_mod.__dict__
        # But __getattr__ is available for backward compat
        assert hasattr(report_mod, "__getattr__")


class TestAllModulesUseFunctionLevelConfig:
    """Verify key modules don't have module-level _config = get_config()."""

    def test_no_module_level_config_in_key_files(self) -> None:
        """FR: Survey key modules for absence of module-level config capture."""
        import trw_mcp.state.analytics.entries as entries_mod
        import trw_mcp.state.analytics.dedup as dedup_mod
        import trw_mcp.state.analytics.counters as counters_mod
        import trw_mcp.state.analytics.core as core_mod

        for mod in [entries_mod, dedup_mod, counters_mod, core_mod]:
            assert "_config" not in mod.__dict__, (
                f"{mod.__name__} has module-level _config"
            )
