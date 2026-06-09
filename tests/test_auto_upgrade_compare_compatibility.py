"""Tests for auto_upgrade version comparison and compatibility helpers."""

from __future__ import annotations

import pytest

import trw_mcp as _mod
from tests._auto_upgrade_test_support import reset_cfg  # noqa: F401
from trw_mcp.state.auto_upgrade import (
    _compare_versions,
    _is_compatible,
    get_installed_version,
)


class TestCompareVersions:
    def test_newer(self) -> None:
        assert _compare_versions("0.4.0", "0.5.0") is True

    def test_same(self) -> None:
        assert _compare_versions("0.4.0", "0.4.0") is False

    def test_older(self) -> None:
        assert _compare_versions("0.5.0", "0.4.0") is False

    def test_invalid_current(self) -> None:
        assert _compare_versions("abc", "0.4.0") is False

    def test_invalid_latest(self) -> None:
        assert _compare_versions("0.4.0", "xyz") is False

    def test_patch_newer(self) -> None:
        assert _compare_versions("0.4.0", "0.4.1") is True

    def test_major_newer(self) -> None:
        assert _compare_versions("1.0.0", "2.0.0") is True

    def test_extra_parts_ignored(self) -> None:
        """Only first 3 parts compared."""
        assert _compare_versions("1.0.0.0", "1.0.1.0") is True


class TestIsCompatible:
    def test_equal_versions(self) -> None:
        assert _is_compatible("1.0.0", "1.0.0") is True

    def test_current_above_min(self) -> None:
        assert _is_compatible("2.0.0", "1.0.0") is True

    def test_current_below_min(self) -> None:
        assert _is_compatible("0.9.0", "1.0.0") is False

    def test_patch_level_compat(self) -> None:
        assert _is_compatible("1.0.1", "1.0.0") is True

    def test_patch_level_incompat(self) -> None:
        assert _is_compatible("1.0.0", "1.0.1") is False

    def test_invalid_current_returns_true(self) -> None:
        """Fail-open: unparseable versions assume compatible."""
        assert _is_compatible("abc", "1.0.0") is True

    def test_invalid_min_returns_true(self) -> None:
        assert _is_compatible("1.0.0", "abc") is True

    def test_both_invalid_returns_true(self) -> None:
        assert _is_compatible("abc", "xyz") is True


class TestGetInstalledVersionEdge:
    """Cover the ImportError branch directly."""

    def test_import_error_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When 'from trw_mcp import __version__' raises ImportError, returns '0.0.0'."""
        monkeypatch.delattr(_mod, "__version__", raising=False)
        result = get_installed_version()
        assert result == "0.0.0"

    def test_positive_returns_version_string(self) -> None:
        """Happy path: returns a non-empty version string."""
        result = get_installed_version()
        assert isinstance(result, str)
        assert len(result.split(".")) >= 3


class TestCompareVersionsEdge:
    """Extra edge cases for _compare_versions."""

    def test_two_part_version_equal_to_three_part(self) -> None:
        """'1.0' and '1.0.0' are the same version — zero-padded comparison."""
        assert _compare_versions("1.0", "1.0.0") is False

    def test_two_part_version_same_prefix(self) -> None:
        """Two-part current, three-part latest with patch bump -> newer."""
        assert _compare_versions("1.0", "1.0.1") is True

    def test_empty_string(self) -> None:
        """Empty string causes ValueError — returns False."""
        assert _compare_versions("", "1.0.0") is False

    def test_both_empty(self) -> None:
        assert _compare_versions("", "") is False


class TestIsCompatibleEdge:
    """Extra edge cases for _is_compatible."""

    def test_two_part_version_equals_three_part(self) -> None:
        """'1.0' meets min_version '1.0.0' — zero-padded comparison makes them equal."""
        assert _is_compatible("1.0", "1.0.0") is True

    def test_two_part_version_below_min(self) -> None:
        """'1.0' (padded 1.0.0) is below min_version '1.0.1'."""
        assert _is_compatible("1.0", "1.0.1") is False

    def test_four_part_version_ignores_extra(self) -> None:
        """Only first 3 parts are compared."""
        assert _is_compatible("1.0.0.99", "1.0.0.1") is True

    def test_empty_current_returns_true(self) -> None:
        """Empty string triggers ValueError → fail-open returns True."""
        assert _is_compatible("", "1.0.0") is True
