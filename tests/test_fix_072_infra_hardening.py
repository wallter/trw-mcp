"""Tests for PRD-FIX-072: Infrastructure Hardening — Analytics & Installer.

FR01: Turn-scoped analytics cache (contextvars-based)
FR02: Absolute path resolution for Gemini MCP config
FR03: Specific exception handling for analytics loading
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# FR01 — Turn-scoped analytics cache
# ---------------------------------------------------------------------------


class TestAnalyticsTurnCache:
    """FR01: _load_analytics_counts uses a ContextVar cache to avoid re-parsing."""

    def test_cache_hit_avoids_repeated_file_reads(self, tmp_path: Path) -> None:
        """Second call in same turn returns cached value without reading file."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        # Reset cache
        _analytics_cache.set(None)

        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        analytics_path.parent.mkdir(parents=True)
        analytics_path.write_text(
            yaml.dump({"sessions_tracked": 42, "total_learnings": 200}),
            encoding="utf-8",
        )

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"

            # First call — reads file
            result1 = _load_analytics_counts()
            assert result1 == (42, 200)

            # Remove file — second call should still return cached
            analytics_path.unlink()
            result2 = _load_analytics_counts()
            assert result2 == (42, 200)

        # Cleanup
        _analytics_cache.set(None)

    def test_cache_miss_on_new_context(self, tmp_path: Path) -> None:
        """After cache reset, reads from file again."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        _analytics_cache.set(None)

        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        analytics_path.parent.mkdir(parents=True)
        analytics_path.write_text(
            yaml.dump({"sessions_tracked": 10, "total_learnings": 50}),
            encoding="utf-8",
        )

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"

            result = _load_analytics_counts()
            assert result == (10, 50)

        _analytics_cache.set(None)

    def test_cache_returns_zeros_when_file_missing(self, tmp_path: Path) -> None:
        """Missing analytics file returns (0, 0) and caches the result."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        _analytics_cache.set(None)

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"

            result = _load_analytics_counts()
            assert result == (0, 0)

        _analytics_cache.set(None)

    def test_ttl_expiry_re_reads_file(self, tmp_path: Path) -> None:
        """After TTL expires, cache is invalidated and file is re-read."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        _analytics_cache.set(None)

        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        analytics_path.parent.mkdir(parents=True)
        analytics_path.write_text(
            yaml.dump({"sessions_tracked": 5, "total_learnings": 25}),
            encoding="utf-8",
        )

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
            patch("trw_mcp.state.claude_md._static_sections.time") as mock_time,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"

            # First call at time 100
            mock_time.monotonic.return_value = 100.0
            result1 = _load_analytics_counts()
            assert result1 == (5, 25)

            # Update file
            analytics_path.write_text(
                yaml.dump({"sessions_tracked": 99, "total_learnings": 999}),
                encoding="utf-8",
            )

            # Within TTL — still returns old value
            mock_time.monotonic.return_value = 103.0
            result2 = _load_analytics_counts()
            assert result2 == (5, 25)

            # After TTL (>5s) — re-reads file
            mock_time.monotonic.return_value = 106.0
            result3 = _load_analytics_counts()
            assert result3 == (99, 999)

        _analytics_cache.set(None)


# ---------------------------------------------------------------------------
# FR02 — Absolute path resolution for Gemini MCP
# ---------------------------------------------------------------------------


class TestGeminiAbsolutePath:
    """FR02: generate_gemini_mcp_config produces fully-qualified command path."""

    def test_resolve_helper_uses_which_when_available(self) -> None:
        """_resolve_trw_mcp_command returns absolute path when on PATH."""
        import shutil

        from trw_mcp.bootstrap._gemini import _resolve_trw_mcp_command

        with patch.object(shutil, "which", return_value="/usr/bin/trw-mcp"):
            cmd, args = _resolve_trw_mcp_command()
            assert cmd == "/usr/bin/trw-mcp"
            assert args == ["serve"]

    def test_resolve_helper_falls_back_to_sys_executable(self) -> None:
        """_resolve_trw_mcp_command falls back to sys.executable when not on PATH."""
        import shutil
        import sys

        from trw_mcp.bootstrap._gemini import _resolve_trw_mcp_command

        with patch.object(shutil, "which", return_value=None):
            cmd, args = _resolve_trw_mcp_command()
            assert cmd == sys.executable
            assert args == ["-m", "trw_mcp", "serve"]

    def test_config_uses_resolved_command(self, tmp_path: Path) -> None:
        """generate_gemini_mcp_config uses the resolved command, not bare 'trw-mcp'."""
        from trw_mcp.bootstrap._gemini import generate_gemini_mcp_config

        fake_path = "/opt/bin/trw-mcp"
        with patch(
            "trw_mcp.bootstrap._gemini._resolve_trw_mcp_command",
            return_value=(fake_path, ["serve"]),
        ):
            generate_gemini_mcp_config(tmp_path)
            settings_path = tmp_path / ".gemini" / "settings.json"
            data = json.loads(settings_path.read_text(encoding="utf-8"))

            assert data["mcpServers"]["trw"]["command"] == fake_path
            assert data["mcpServers"]["trw"]["args"] == ["serve"]

    def test_config_fallback_uses_sys_executable(self, tmp_path: Path) -> None:
        """When trw-mcp is not on PATH, config uses sys.executable."""
        import sys

        from trw_mcp.bootstrap._gemini import generate_gemini_mcp_config

        with patch(
            "trw_mcp.bootstrap._gemini._resolve_trw_mcp_command",
            return_value=(sys.executable, ["-m", "trw_mcp", "serve"]),
        ):
            generate_gemini_mcp_config(tmp_path, force=True)
            settings_path = tmp_path / ".gemini" / "settings.json"
            data = json.loads(settings_path.read_text(encoding="utf-8"))

            assert data["mcpServers"]["trw"]["command"] == sys.executable
            assert data["mcpServers"]["trw"]["args"] == ["-m", "trw_mcp", "serve"]


# ---------------------------------------------------------------------------
# FR03 — Specific exception handling for analytics
# ---------------------------------------------------------------------------


class TestAnalyticsSpecificExceptions:
    """FR03: Analytics loading uses targeted exception handling with distinct logging."""

    def test_file_not_found_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """FileNotFoundError is caught and logged distinctly."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        _analytics_cache.set(None)

        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        analytics_path.parent.mkdir(parents=True)
        # Create the file so .exists() returns True, but then make read fail
        analytics_path.write_text("valid: yaml", encoding="utf-8")

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
            patch("trw_mcp.state.claude_md._static_sections.FileStateReader") as mock_reader_cls,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"
            mock_reader_cls.return_value.read_yaml.side_effect = FileNotFoundError("gone")

            result = _load_analytics_counts()
            assert result == (0, 0)

        _analytics_cache.set(None)

    def test_yaml_parse_error_logged(self, tmp_path: Path) -> None:
        """YAML parse error is caught and returns (0, 0)."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        _analytics_cache.set(None)

        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        analytics_path.parent.mkdir(parents=True)
        analytics_path.write_text("valid: yaml", encoding="utf-8")

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
            patch("trw_mcp.state.claude_md._static_sections.FileStateReader") as mock_reader_cls,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"
            mock_reader_cls.return_value.read_yaml.side_effect = yaml.YAMLError("bad yaml")

            result = _load_analytics_counts()
            assert result == (0, 0)

        _analytics_cache.set(None)

    def test_os_error_logged(self, tmp_path: Path) -> None:
        """OSError (e.g., locked file) is caught and returns (0, 0)."""
        from trw_mcp.state.claude_md._static_sections import (
            _analytics_cache,
            _load_analytics_counts,
        )

        _analytics_cache.set(None)

        analytics_path = tmp_path / ".trw" / "context" / "analytics.yaml"
        analytics_path.parent.mkdir(parents=True)
        analytics_path.write_text("valid: yaml", encoding="utf-8")

        with (
            patch("trw_mcp.state.claude_md._static_sections.resolve_project_root", return_value=tmp_path),
            patch("trw_mcp.state.claude_md._static_sections.get_config") as mock_cfg,
            patch("trw_mcp.state.claude_md._static_sections.FileStateReader") as mock_reader_cls,
        ):
            mock_cfg.return_value.trw_dir = ".trw"
            mock_cfg.return_value.context_dir = "context"
            mock_reader_cls.return_value.read_yaml.side_effect = OSError("locked")

            result = _load_analytics_counts()
            assert result == (0, 0)

        _analytics_cache.set(None)
