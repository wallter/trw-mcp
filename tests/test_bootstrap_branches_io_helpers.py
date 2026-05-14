"""Split bootstrap branch coverage for file and install helpers."""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import (
    _check_package_version,
    _copy_file,
    _files_identical,
    _get_bundled_names,
    _pip_install_package,
    _remove_stale_artifacts,
    _write_if_missing,
    update_project,
)


@pytest.mark.unit
class TestRemoveStaleArtifacts:
    """Cover OSError branches in _remove_stale_artifacts."""

    def _setup_manifest(
        self,
        target_dir: Path,
        extra_skills: list[str] | None = None,
        extra_agents: list[str] | None = None,
        extra_hooks: list[str] | None = None,
    ) -> None:
        """Write manifest with extra stale entries."""
        from trw_mcp.state.persistence import FileStateWriter

        bundled = _get_bundled_names()
        manifest = {
            "version": 1,
            "skills": bundled["skills"] + (extra_skills or []),
            "agents": bundled["agents"] + (extra_agents or []),
            "hooks": bundled["hooks"] + (extra_hooks or []),
        }
        manifest_path = target_dir / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        FileStateWriter().write_yaml(manifest_path, manifest)

    def test_oserror_on_stale_skill_removal(self, initialized_repo: Path) -> None:
        """OSError removing stale skill is silently ignored."""
        self._setup_manifest(initialized_repo, extra_skills=["stale-skill"])
        stale_skill = initialized_repo / ".claude" / "skills" / "stale-skill"
        stale_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        with patch("shutil.rmtree", side_effect=OSError("permission denied")):
            _remove_stale_artifacts(initialized_repo, result)

        assert stale_skill.exists()

    def test_oserror_on_stale_agent_removal(self, initialized_repo: Path) -> None:
        """OSError removing stale agent is silently ignored."""
        self._setup_manifest(initialized_repo, extra_agents=["stale-agent.md"])
        stale_agent = initialized_repo / ".claude" / "agents" / "stale-agent.md"
        stale_agent.write_text("stale", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            _remove_stale_artifacts(initialized_repo, result)

        assert stale_agent.exists()

    def test_oserror_on_stale_hook_removal(self, initialized_repo: Path) -> None:
        """OSError removing stale hook is silently ignored."""
        self._setup_manifest(initialized_repo, extra_hooks=["stale-hook.sh"])
        stale_hook = initialized_repo / ".claude" / "hooks" / "stale-hook.sh"
        stale_hook.write_text("#!/bin/sh", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            _remove_stale_artifacts(initialized_repo, result)

        assert stale_hook.exists()


@pytest.mark.unit
class TestCheckPackageVersion:
    """Cover _check_package_version branches."""

    def test_package_not_found_adds_warning(self) -> None:
        """PackageNotFoundError → warning about missing package."""
        result: dict[str, list[str]] = {"warnings": [], "preserved": []}

        with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError("trw-mcp")):
            _check_package_version(result)

        assert any("not found" in w for w in result["warnings"])

    def test_version_mismatch_adds_warning(self) -> None:
        """Version mismatch → warning about reinstall.

        _check_package_version does `from trw_mcp import __version__` at call time,
        so we patch `trw_mcp.__version__` to simulate a source/installed mismatch.
        """
        result: dict[str, list[str]] = {"warnings": [], "preserved": []}

        import trw_mcp

        real_version = trw_mcp.__version__
        fake_installed = "0.0.0-old"

        with patch("importlib.metadata.version", return_value=fake_installed):
            with patch.object(trw_mcp, "__version__", real_version):
                _check_package_version(result)

        if real_version != fake_installed:
            assert any("differs from source" in w for w in result["warnings"])
        else:
            assert any("up to date" in p for p in result["preserved"])

    def test_version_match_adds_preserved(self) -> None:
        """Matching versions → preserved entry."""
        import trw_mcp

        real_version = trw_mcp.__version__

        result: dict[str, list[str]] = {"warnings": [], "preserved": []}

        with patch("importlib.metadata.version", return_value=real_version):
            _check_package_version(result)

        assert any("up to date" in p for p in result["preserved"])

    def test_version_mismatch_direct(self) -> None:
        """Direct mismatch test using monkeypatched source version."""
        result: dict[str, list[str]] = {"warnings": [], "preserved": []}

        import trw_mcp as _trw

        real_version = _trw.__version__
        fake_installed = "0.0.0-stale"

        with patch("importlib.metadata.version", return_value=fake_installed):
            _check_package_version(result)

        if real_version != fake_installed:
            assert any("differs from source" in w for w in result["warnings"])


@pytest.mark.unit
class TestPipInstallPackage:
    """Cover _pip_install_package branches."""

    def test_pip_install_success(self, tmp_path: Path) -> None:
        """Successful pip install adds 'updated' entry."""
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            _pip_install_package(tmp_path, result)

        assert any("pip install" in u for u in result["updated"])

    def test_pip_install_failure(self, tmp_path: Path) -> None:
        """Failed pip install adds error with exit code."""
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "ERROR: some pip error occurred\n"

        with patch("subprocess.run", return_value=mock_proc):
            _pip_install_package(tmp_path, result)

        assert any("pip install failed" in e and "exit 1" in e for e in result["errors"])

    def test_pip_install_timeout(self, tmp_path: Path) -> None:
        """subprocess.TimeoutExpired → error entry."""
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=120)):
            _pip_install_package(tmp_path, result)

        assert any("pip install failed" in e for e in result["errors"])

    def test_pip_install_oserror(self, tmp_path: Path) -> None:
        """OSError running pip → error entry."""
        result: dict[str, list[str]] = {"updated": [], "errors": []}

        with patch("subprocess.run", side_effect=OSError("executable not found")):
            _pip_install_package(tmp_path, result)

        assert any("pip install failed" in e for e in result["errors"])

    def test_pip_install_no_pyproject(self, tmp_path: Path) -> None:
        """Missing pyproject.toml → error about not finding package."""
        result: dict[str, list[str]] = {"updated": [], "errors": []}
        fake_data_dir = tmp_path / "src" / "trw_mcp" / "data"
        fake_data_dir.mkdir(parents=True)

        with patch("trw_mcp.bootstrap._DATA_DIR", fake_data_dir):
            _pip_install_package(tmp_path, result)

        assert any("Cannot find" in e or "pyproject.toml" in e for e in result["errors"])

    def test_update_project_with_pip_install(self, initialized_repo: Path) -> None:
        """update_project(pip_install=True) triggers pip install."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = update_project(initialized_repo, pip_install=True)

        assert any("pip install" in u for u in result["updated"])


@pytest.mark.unit
class TestCopyFile:
    """Cover _copy_file error path."""

    def test_copy_file_oserror(self, tmp_path: Path) -> None:
        """OSError during copy adds to errors."""
        src = tmp_path / "src.txt"
        src.write_text("content", encoding="utf-8")
        dest = tmp_path / "dest.txt"

        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch("shutil.copy2", side_effect=OSError("permission denied")):
            _copy_file(src, dest, force=True, result=result)

        assert any("Failed to copy" in e for e in result["errors"])

    def test_copy_file_skips_when_exists_no_force(self, tmp_path: Path) -> None:
        """Existing file without force → skipped."""
        src = tmp_path / "src.txt"
        src.write_text("source", encoding="utf-8")
        dest = tmp_path / "dest.txt"
        dest.write_text("existing", encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}
        _copy_file(src, dest, force=False, result=result)

        assert str(dest) in result["skipped"]
        assert dest.read_text(encoding="utf-8") == "existing"

    def test_copy_file_makes_sh_executable(self, tmp_path: Path) -> None:
        """Shell script gets executable bits set."""
        src = tmp_path / "test-hook.sh"
        src.write_text("#!/bin/sh\nexit 0", encoding="utf-8")
        dest = tmp_path / "dest-hook.sh"

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _copy_file(src, dest, force=True, result=result)

        assert not result["errors"]
        assert os.access(dest, os.X_OK)


@pytest.mark.unit
class TestWriteIfMissing:
    """Cover _write_if_missing error path."""

    def test_write_error(self, tmp_path: Path) -> None:
        """OSError writing file adds to errors."""
        dest = tmp_path / "config.yaml"
        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch.object(Path, "write_text", side_effect=OSError("read-only")):
            _write_if_missing(dest, "content", force=True, result=result)

        assert any("Failed to write" in e for e in result["errors"])


@pytest.mark.unit
class TestFilesIdentical:
    """Cover _files_identical edge cases."""

    def test_identical_files(self, tmp_path: Path) -> None:
        """Identical files return True."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"hello world")
        b.write_bytes(b"hello world")
        assert _files_identical(a, b) is True

    def test_different_files(self, tmp_path: Path) -> None:
        """Different files return False."""
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_bytes(b"hello")
        b.write_bytes(b"world")
        assert _files_identical(a, b) is False

    def test_oserror_returns_false(self, tmp_path: Path) -> None:
        """OSError reading files returns False."""
        a = tmp_path / "missing_a.txt"
        b = tmp_path / "missing_b.txt"
        assert _files_identical(a, b) is False
