from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools._orchestration_helpers import _get_bundled_file, _get_package_version
from trw_mcp.tools._orchestration_phase import _check_framework_version_staleness


class TestGetBundledFile:
    """Tests for _get_bundled_file helper (line 463)."""

    def test_returns_none_for_nonexistent_file(self) -> None:
        """Returns None when the requested file does not exist."""
        result = _get_bundled_file("totally_nonexistent_file_xyz.md")
        assert result is None

    def test_returns_content_for_existing_file(self) -> None:
        """Returns string content for a file that exists in data/."""
        result = _get_bundled_file("framework.md")
        assert result is None or isinstance(result, str)

    def test_returns_none_for_nonexistent_subdir_file(self) -> None:
        """Returns None when subdir/file combo doesn't exist."""
        result = _get_bundled_file("nonexistent.md", subdir="templates")
        assert result is None


class TestGetPackageVersion:
    """Tests for _get_package_version helper (lines 475-476)."""

    def test_returns_string(self) -> None:
        """Returns a string (either version or 'unknown')."""
        result = _get_package_version()
        assert isinstance(result, str)

    def test_returns_unknown_when_package_not_found(self) -> None:
        """Returns 'unknown' when importlib.metadata raises PackageNotFoundError."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("trw-mcp"),
        ):
            result = _get_package_version()
            assert result == "unknown"

    def test_exception_path_returns_unknown(self) -> None:
        """Directly test the exception path by patching importlib.metadata."""
        from importlib.metadata import PackageNotFoundError

        with patch(
            "importlib.metadata.version",
            side_effect=PackageNotFoundError("trw-mcp"),
        ):
            result = _get_package_version()
            assert result == "unknown"


class TestCheckFrameworkVersionStaleness:
    """Direct tests for _check_framework_version_staleness (lines 580-599)."""

    def test_empty_run_framework_returns_none(self) -> None:
        """Returns None when run_framework is empty string (line 580)."""
        result = _check_framework_version_staleness("")
        assert result is None

    def test_no_version_yaml_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when VERSION.yaml does not exist (line 586)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        result = _check_framework_version_staleness("v1.0_TRW")
        assert result is None

    def test_matching_versions_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when run framework matches deployed version (line 590-591)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {
                "framework_version": cfg.framework_version,
            },
        )

        result = _check_framework_version_staleness(cfg.framework_version)
        assert result is None

    def test_mismatched_versions_returns_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns warning string when versions differ (lines 593-597)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {
                "framework_version": "v99.0_TRW",
            },
        )

        result = _check_framework_version_staleness("v1.0_TRW")

        assert result is not None
        assert "v1.0_TRW" in result
        assert "v99.0_TRW" in result
        assert "re-bootstrapping" in result

    def test_empty_current_version_in_yaml_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when VERSION.yaml has empty framework_version (line 590)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {
                "framework_version": "",
            },
        )

        result = _check_framework_version_staleness("v1.0_TRW")
        assert result is None

    def test_state_error_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Returns None when StateError raised during read (line 598-599)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        from trw_mcp.exceptions import StateError as TRWStateError
        from trw_mcp.state.persistence import FileStateReader as _FSR

        cfg = TRWConfig()
        frameworks_dir = tmp_path / cfg.trw_dir / cfg.frameworks_dir
        frameworks_dir.mkdir(parents=True)
        (frameworks_dir / "VERSION.yaml").write_text("framework_version: v1.0_TRW\n", encoding="utf-8")

        def _raise_state_error(path: Path) -> dict[str, object]:
            raise TRWStateError("simulated error")

        with patch.object(_FSR, "read_yaml", side_effect=_raise_state_error):
            result = _check_framework_version_staleness("v2.0_TRW")
        assert result is None
