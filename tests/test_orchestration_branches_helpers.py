"""Coverage-targeted helper tests for trw_mcp/tools/orchestration.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests._tools_orchestration_support import set_project_root  # noqa: F401
from trw_mcp.exceptions import StateError as TRWStateError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools.orchestration import (
    _check_framework_version_staleness,
    _get_bundled_file,
)


class TestGetBundledFile:
    """Line 463: _get_bundled_file with subdir parameter."""

    def test_get_bundled_file_with_subdir_templates(self) -> None:
        """_get_bundled_file('claude_md.md', subdir='templates') returns non-empty string."""
        content = _get_bundled_file("claude_md.md", subdir="templates")

        assert content is not None
        assert isinstance(content, str)
        assert len(content) > 10

    def test_get_bundled_file_without_subdir(self) -> None:
        """_get_bundled_file('framework.md') (no subdir) returns content."""
        content = _get_bundled_file("framework.md")

        assert content is not None
        assert isinstance(content, str)
        assert len(content) > 10

    def test_get_bundled_file_nonexistent_returns_none(self) -> None:
        """_get_bundled_file for a file that doesn't exist returns None."""
        result = _get_bundled_file("does_not_exist.xyz")
        assert result is None

    def test_get_bundled_file_nonexistent_subdir_returns_none(self) -> None:
        """_get_bundled_file with nonexistent subdir returns None."""
        result = _get_bundled_file("framework.md", subdir="nonexistent_subdir")
        assert result is None


class TestGetPackageVersion:
    """Lines 475-476: _get_package_version fallback when package not found."""

    def test_returns_string(self) -> None:
        """_get_package_version always returns a string."""
        from trw_mcp.tools._orchestration_helpers import _get_package_version

        result = _get_package_version()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_unknown_when_package_not_installed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When importlib.metadata raises an exception, returns 'unknown' (lines 475-476)."""
        import importlib.metadata as im

        from trw_mcp.tools._orchestration_helpers import _get_package_version

        def broken_version(distribution_name: str) -> str:
            raise Exception("simulated failure")

        monkeypatch.setattr(im, "version", broken_version)
        result = _get_package_version()
        assert result == "unknown"


class TestCheckFrameworkVersionStaleness:
    """Lines 580-599: _check_framework_version_staleness all branches."""

    def test_empty_string_returns_none(self, tmp_path: Path) -> None:
        """Empty run_framework string returns None immediately (line 580)."""
        result = _check_framework_version_staleness("")
        assert result is None

    def test_version_file_does_not_exist_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When VERSION.yaml does not exist, returns None (line 586)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
        result = _check_framework_version_staleness("v18.0_TRW")
        assert result is None

    def test_matching_versions_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When run version matches deployed version, returns None (line 591)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {"framework_version": "v24.0_TRW"},
        )

        result = _check_framework_version_staleness("v24.0_TRW")
        assert result is None

    def test_stale_version_returns_warning_string(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When versions differ, returns a warning message string (lines 593-599)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {"framework_version": "v24.0_TRW"},
        )

        result = _check_framework_version_staleness("v18.0_TRW")

        assert result is not None
        assert isinstance(result, str)
        assert "v18.0_TRW" in result
        assert "v24.0_TRW" in result

    def test_empty_current_version_in_file_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When VERSION.yaml has empty framework_version, returns None (line 590)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        writer = FileStateWriter()
        writer.write_yaml(
            frameworks_dir / "VERSION.yaml",
            {"framework_version": ""},
        )

        result = _check_framework_version_staleness("v18.0_TRW")
        assert result is None

    def test_exception_during_read_returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """StateError during version file read is caught and returns None (line 598)."""
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

        frameworks_dir = tmp_path / ".trw" / "frameworks"
        frameworks_dir.mkdir(parents=True)
        (frameworks_dir / "VERSION.yaml").write_text(
            "framework_version: v99.0_TRW\n",
            encoding="utf-8",
        )

        def exploding_read(path: Path) -> dict[str, object]:
            raise TRWStateError("simulated read failure")

        with patch.object(FileStateReader, "read_yaml", side_effect=exploding_read):
            result = _check_framework_version_staleness("v18.0_TRW")
        assert result is None


# --- PRD-CORE-265-NFR01: owner_of is cheap enough for the commit boundary ----


def test_formation_owner_of_latency_budget(formation_env: FormationFixture) -> None:
    """NFR01. 16 members, 200 resolutions, median at or under 50 ms each.

    The budget exists because FR09 runs this on EVERY scoped commit; a resolution
    an operator can feel would make the ownership check something people disable.
    The manifest is loaded inside the timed section (``context=None``), so the
    number measured is the one the commit boundary actually pays, not a warmed
    in-memory match.
    """
    import statistics
    import time

    from trw_mcp.formation import create, owner_of

    members = [
        {
            "member_id": f"impl-{index:02d}",
            "client": "claude-code",
            "owned_paths": [f"src/pkg{index:02d}"],
            "test_owned_paths": [f"tests/test_pkg{index:02d}.py"],
        }
        for index in range(16)
    ]
    create(formation_env.orchestrator_run, formation_env.payload(members=members), prds_dir=None)

    samples: list[float] = []
    for _ in range(200):
        started = time.perf_counter()
        ownership = owner_of("src/pkg09/thing.py", run_path=formation_env.orchestrator_run)
        samples.append((time.perf_counter() - started) * 1000.0)
    assert ownership is not None and ownership.member_id == "impl-09"

    median_ms = statistics.median(samples)
    assert median_ms <= 50.0, f"median owner_of resolution {median_ms:.2f} ms exceeds the 50 ms budget"
