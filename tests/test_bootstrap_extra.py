"""Extra coverage tests for trw_mcp.bootstrap.

Targets uncovered lines:
  245, 253-255, 272-274, 285-287, 305-307, 315-317, 330-332, 340-342,
  359-362, 370, 378-379, 390, 442-443, 447, 452-455, 469-472, 511,
  520-521, 549-550, 592-593, 605-606, 618-619, 635-640, 643, 664-690,
  723-724, 740-741, 780-781, 806-807, 810, 823-824, 832-833, 877-878,
  895, 903-909, 916, 927-928
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import (
    _check_package_version,
    _copy_file,
    _files_identical,
    _generate_mcp_json,
    _get_bundled_names,
    _merge_mcp_json,
    _minimal_claude_md,
    _minimal_claude_md_trw_block,
    _pip_install_package,
    _read_manifest,
    _remove_stale_artifacts,
    _trw_mcp_server_entry,
    _update_claude_md_trw_section,
    _verify_installation,
    _write_if_missing,
    _write_installer_metadata,
    _write_manifest,
    init_project,
    update_project,
)


@pytest.fixture()
def fake_git_repo(tmp_path: Path) -> Path:
    """Create a minimal fake git repo directory."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture()
def initialized_repo(fake_git_repo: Path) -> Path:
    """Create a repo with TRW already initialized."""
    result = init_project(fake_git_repo)
    assert not result["errors"]
    return fake_git_repo


# ---------------------------------------------------------------------------
# dry_run "would create" path (line 245) — framework files that don't exist
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDryRunWouldCreate:
    """Cover the 'would create' branches in dry-run mode."""

    def test_dry_run_framework_would_create(self, fake_git_repo: Path) -> None:
        """Missing framework file in dry-run reports 'would create'."""
        # Manually create .trw so validation passes but leave dest missing
        (fake_git_repo / ".trw").mkdir()
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        (fake_git_repo / ".trw" / "context").mkdir()
        (fake_git_repo / ".trw" / "templates").mkdir()
        (fake_git_repo / ".claude" / "settings.json").parent.mkdir(parents=True)

        result = update_project(fake_git_repo, dry_run=True)

        # Several framework files won't exist → "would create"
        assert any("would create" in c for c in result["created"])

    def test_dry_run_identical_file_not_reported(self, initialized_repo: Path) -> None:
        """Dry-run skips identical files — only changed files reported as 'would update'."""
        result = update_project(initialized_repo, dry_run=True)
        # Identical files are not reported; at most there's a DRY RUN warning
        assert any("DRY RUN" in w for w in result["warnings"])

    def test_dry_run_hook_would_create_when_missing(self, fake_git_repo: Path) -> None:
        """Dry-run reports hook 'would create' when hooks dir is missing."""
        (fake_git_repo / ".trw").mkdir()

        result = update_project(fake_git_repo, dry_run=True)

        # No errors expected (dry run just reports)
        all_output = result["created"] + result["updated"]
        # Hook files that don't exist → 'would create'
        hook_creates = [x for x in all_output if "would create" in x and "hook" in x.lower()]
        # If hooks exist in data dir, we should see would-create entries
        from trw_mcp.bootstrap import _DATA_DIR
        if (_DATA_DIR / "hooks").is_dir():
            assert len(hook_creates) > 0

    def test_dry_run_skill_would_create(self, fake_git_repo: Path) -> None:
        """Dry-run reports skill 'would create' when skills dir is missing."""
        (fake_git_repo / ".trw").mkdir()
        result = update_project(fake_git_repo, dry_run=True)
        all_output = result["created"] + result["updated"]
        skill_creates = [x for x in all_output if "would create" in x and "skill" in x.lower()]
        from trw_mcp.bootstrap import _DATA_DIR
        if (_DATA_DIR / "skills").is_dir():
            assert len(skill_creates) > 0

    def test_dry_run_agent_would_create(self, fake_git_repo: Path) -> None:
        """Dry-run reports agent 'would create' when agents dir is missing."""
        (fake_git_repo / ".trw").mkdir()
        result = update_project(fake_git_repo, dry_run=True)
        all_output = result["created"] + result["updated"]
        agent_creates = [x for x in all_output if "would create" in x and ".md" in x]
        from trw_mcp.bootstrap import _DATA_DIR
        if (_DATA_DIR / "agents").is_dir():
            assert len(agent_creates) > 0

    def test_dry_run_claude_md_would_create(self, fake_git_repo: Path) -> None:
        """Dry-run reports CLAUDE.md 'would create' when file is missing."""
        (fake_git_repo / ".trw").mkdir()
        result = update_project(fake_git_repo, dry_run=True)
        all_output = result["created"]
        assert any("CLAUDE.md" in c for c in all_output)

    def test_dry_run_claude_md_would_update(self, initialized_repo: Path) -> None:
        """Dry-run reports CLAUDE.md 'would update' when file exists."""
        result = update_project(initialized_repo, dry_run=True)
        assert any("CLAUDE.md" in u and "would update" in u for u in result["updated"])

    def test_dry_run_mcp_json_preserved_when_trw_present(self, initialized_repo: Path) -> None:
        """Dry-run reports .mcp.json preserved when trw key already present."""
        result = update_project(initialized_repo, dry_run=True)
        mcp_path = initialized_repo / ".mcp.json"
        # trw key is present, so it should be preserved
        assert any(str(mcp_path) in p for p in result["preserved"])

    def test_dry_run_mcp_json_would_merge_when_trw_missing(self, initialized_repo: Path) -> None:
        """Dry-run reports .mcp.json would merge when trw key is missing."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"other": {}}}), encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)
        assert any("would merge" in u and "trw entry" in u for u in result["updated"])

    def test_dry_run_mcp_json_invalid_json_would_merge(self, initialized_repo: Path) -> None:
        """Dry-run handles corrupt .mcp.json by reporting would-merge."""
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text("not-json{{{", encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)
        assert any("would merge" in u for u in result["updated"])

    def test_dry_run_mcp_json_would_create_if_missing(self, fake_git_repo: Path) -> None:
        """Dry-run reports .mcp.json 'would create' when file doesn't exist."""
        (fake_git_repo / ".trw").mkdir()
        result = update_project(fake_git_repo, dry_run=True)
        assert any(".mcp.json" in c and "would create" in c for c in result["created"])


# ---------------------------------------------------------------------------
# update_project — OSError paths in framework/hook/skill/agent copy (lines 253-255, 285-287, etc.)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateOSErrorPaths:
    """Cover OSError branches in update_project copy loops."""

    def test_framework_copy_oserror(self, initialized_repo: Path) -> None:
        """OSError during framework file copy adds to errors."""
        with patch("shutil.copy2", side_effect=OSError("disk full")):
            result = update_project(initialized_repo)
        assert any("Failed to copy" in e for e in result["errors"])

    def test_hook_copy_oserror(self, initialized_repo: Path) -> None:
        """OSError during hook copy adds to errors."""
        from trw_mcp.bootstrap import _DATA_DIR
        if not (_DATA_DIR / "hooks").is_dir():
            pytest.skip("no bundled hooks")

        original_copy2 = shutil.copy2

        def selective_fail(src: Path | str, dst: Path | str, **kwargs: object) -> None:
            src_path = Path(str(src))
            if src_path.suffix == ".sh":
                raise OSError("permission denied")
            return original_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=selective_fail):
            result = update_project(initialized_repo)
        assert any("Failed to copy" in e for e in result["errors"])

    def test_skill_copy_oserror(self, initialized_repo: Path) -> None:
        """OSError during skill file copy adds to errors."""
        from trw_mcp.bootstrap import _DATA_DIR
        if not (_DATA_DIR / "skills").is_dir():
            pytest.skip("no bundled skills")

        original_copy2 = shutil.copy2
        call_count = [0]

        def fail_after_n(src: Path | str, dst: Path | str, **kwargs: object) -> None:
            call_count[0] += 1
            # Fail on copies from skills directory
            if "skills" in str(src):
                raise OSError("skill copy failed")
            return original_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=fail_after_n):
            result = update_project(initialized_repo)
        assert any("skill" in e.lower() or "Failed to copy" in e for e in result["errors"])

    def test_agent_copy_oserror(self, initialized_repo: Path) -> None:
        """OSError during agent file copy adds to errors."""
        from trw_mcp.bootstrap import _DATA_DIR
        if not (_DATA_DIR / "agents").is_dir():
            pytest.skip("no bundled agents")

        original_copy2 = shutil.copy2

        def fail_agents(src: Path | str, dst: Path | str, **kwargs: object) -> None:
            if "agents" in str(src):
                raise OSError("agent copy failed")
            return original_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=fail_agents):
            result = update_project(initialized_repo)
        assert any("Failed to copy" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# _update_claude_md_trw_section — error paths (lines 442-443, 452-455)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateClaudeMdTrwSection:
    """Cover error branches in _update_claude_md_trw_section."""

    def test_write_error_with_existing_markers(self, tmp_path: Path) -> None:
        """OSError writing updated CLAUDE.md with existing markers → error."""
        claude_md = tmp_path / "CLAUDE.md"
        # Write content with valid TRW markers
        content = (
            "# User content\n\n"
            "<!-- TRW AUTO-GENERATED — do not edit between markers -->\n"
            "<!-- trw:start -->\nOld TRW block\n<!-- trw:end -->\n"
        )
        claude_md.write_text(content, encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}

        with patch.object(Path, "write_text", side_effect=OSError("read-only fs")):
            _update_claude_md_trw_section(claude_md, result)

        assert any("Failed to update" in e for e in result["errors"])

    def test_malformed_markers_start_without_end(self, tmp_path: Path) -> None:
        """CLAUDE.md with trw:start but no trw:end → error about malformed markers."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text(
            "<!-- trw:start -->\nno end marker here\n", encoding="utf-8"
        )

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        assert any("malformed" in e for e in result["errors"])

    def test_append_when_no_trw_section(self, tmp_path: Path) -> None:
        """CLAUDE.md with no TRW section → append the block."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        assert not result["errors"]
        assert any(str(claude_md) in u for u in result["updated"])
        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content

    def test_append_write_error(self, tmp_path: Path) -> None:
        """OSError when appending TRW block → error."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _update_claude_md_trw_section(claude_md, result)

        assert any("Failed to update" in e for e in result["errors"])

    def test_content_without_trailing_newline(self, tmp_path: Path) -> None:
        """Content without trailing newline gets one added before TRW block."""
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project", encoding="utf-8")  # No trailing newline

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _update_claude_md_trw_section(claude_md, result)

        content = claude_md.read_text(encoding="utf-8")
        assert "<!-- trw:start -->" in content
        assert "# My Project\n" in content


# ---------------------------------------------------------------------------
# _minimal_claude_md_trw_block — fallback path (lines 469-472)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMinimalClaudeMdTrwBlock:
    """Cover _minimal_claude_md_trw_block including fallback."""

    def test_returns_trw_block(self) -> None:
        """Returns a non-empty string with TRW markers."""
        block = _minimal_claude_md_trw_block()
        assert "<!-- trw:start -->" in block
        assert "<!-- trw:end -->" in block

    def test_fallback_when_header_marker_missing(self) -> None:
        """Returns trw:start..end block when header marker not in template."""
        fake_md = "<!-- trw:start -->\nSome content\n<!-- trw:end -->\n"
        with patch("trw_mcp.bootstrap._minimal_claude_md", return_value=fake_md):
            block = _minimal_claude_md_trw_block()
        assert "<!-- trw:start -->" in block
        assert "<!-- trw:end -->" in block

    def test_returns_empty_when_no_markers(self) -> None:
        """Returns empty string when no TRW markers found."""
        with patch("trw_mcp.bootstrap._minimal_claude_md", return_value="no markers"):
            block = _minimal_claude_md_trw_block()
        assert block == ""


# ---------------------------------------------------------------------------
# _read_manifest — OSError path (lines 520-521)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestReadManifest:
    """Cover _read_manifest edge cases."""

    def test_returns_none_when_missing(self, tmp_path: Path) -> None:
        """Returns None when manifest file doesn't exist."""
        result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_none_when_not_dict(self, tmp_path: Path) -> None:
        """Returns None when read_yaml returns a non-dict (e.g. a list)."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("- not\n- a\n- dict\n", encoding="utf-8")

        mock_reader = MagicMock()
        mock_reader.read_yaml.return_value = ["not", "a", "dict"]
        with patch("trw_mcp.state.persistence.FileStateReader", return_value=mock_reader):
            result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_none_on_oserror(self, tmp_path: Path) -> None:
        """Returns None when OSError reading manifest."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text("version: 1\nskills: []\n", encoding="utf-8")

        with patch("trw_mcp.state.persistence.FileStateReader.read_yaml", side_effect=OSError("io error")):
            result = _read_manifest(tmp_path)
        assert result is None

    def test_returns_dict_with_lists(self, tmp_path: Path) -> None:
        """Returns dict with skills/agents/hooks lists."""
        manifest_path = tmp_path / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True)
        from trw_mcp.state.persistence import FileStateWriter
        FileStateWriter().write_yaml(manifest_path, {
            "version": 1,
            "skills": ["deliver", "learn"],
            "agents": ["trw-tester.md"],
            "hooks": ["session-start.sh"],
        })

        result = _read_manifest(tmp_path)
        assert result is not None
        assert "deliver" in result["skills"]
        assert "trw-tester.md" in result["agents"]


# ---------------------------------------------------------------------------
# _write_manifest — OSError path (lines 549-550)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWriteManifest:
    """Cover _write_manifest error path."""

    def test_write_manifest_oserror(self, tmp_path: Path) -> None:
        """OSError writing manifest adds to errors."""
        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", side_effect=OSError("disk full")):
            _write_manifest(tmp_path, result)

        assert any("Failed to write manifest" in e for e in result["errors"])

    def test_write_manifest_uses_updated_key_when_present(self, tmp_path: Path) -> None:
        """When 'updated' key exists in result, manifest is appended there."""
        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        (tmp_path / ".trw").mkdir(parents=True)

        _write_manifest(tmp_path, result)

        assert any("managed-artifacts" in u for u in result["updated"])
        assert not result["errors"]


# ---------------------------------------------------------------------------
# _remove_stale_artifacts — OSError during rmtree/unlink (lines 592-593, 605-606, 618-619)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRemoveStaleArtifacts:
    """Cover OSError branches in _remove_stale_artifacts."""

    def _setup_manifest(self, target_dir: Path, extra_skills: list[str] | None = None,
                        extra_agents: list[str] | None = None,
                        extra_hooks: list[str] | None = None) -> None:
        """Write manifest with extra stale entries."""
        from trw_mcp.bootstrap import _get_bundled_names
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

        # Stale skill still exists (removal failed silently)
        assert stale_skill.exists()

    def test_oserror_on_stale_agent_removal(self, initialized_repo: Path) -> None:
        """OSError removing stale agent is silently ignored."""
        self._setup_manifest(initialized_repo, extra_agents=["stale-agent.md"])
        stale_agent = initialized_repo / ".claude" / "agents" / "stale-agent.md"
        stale_agent.write_text("stale", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            _remove_stale_artifacts(initialized_repo, result)

        # Agent still exists since unlink failed
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


# ---------------------------------------------------------------------------
# _check_package_version — PackageNotFoundError path (lines 635-640) and mismatch (643)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCheckPackageVersion:
    """Cover _check_package_version branches."""

    def test_package_not_found_adds_warning(self) -> None:
        """PackageNotFoundError → warning about missing package."""
        result: dict[str, list[str]] = {"warnings": [], "preserved": []}

        with patch("importlib.metadata.version",
                   side_effect=importlib.metadata.PackageNotFoundError("trw-mcp")):
            _check_package_version(result)

        assert any("not found" in w for w in result["warnings"])

    def test_version_mismatch_adds_warning(self) -> None:
        """Version mismatch → warning about reinstall.

        _check_package_version does `from trw_mcp import __version__` at call time,
        so we patch `trw_mcp.__version__` to simulate a source/installed mismatch.
        """
        result: dict[str, list[str]] = {"warnings": [], "preserved": []}

        # Patch the installed version to something clearly different from source
        import trw_mcp
        real_version = trw_mcp.__version__
        fake_installed = "0.0.0-old"

        with patch("importlib.metadata.version", return_value=fake_installed):
            with patch.object(trw_mcp, "__version__", real_version):
                _check_package_version(result)

        # real_version != fake_installed → warning
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

        # Patch installed version to something different from source
        import trw_mcp as _trw
        real_version = _trw.__version__
        fake_installed = "0.0.0-stale"

        with patch("importlib.metadata.version", return_value=fake_installed):
            _check_package_version(result)

        # If real_version != fake_installed, we get a warning
        if real_version != fake_installed:
            assert any("differs from source" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# _pip_install_package — success/failure paths (lines 664-690)
# ---------------------------------------------------------------------------

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

        with patch("subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="pip", timeout=120)):
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

        # Point _DATA_DIR.parent.parent.parent to a path without pyproject.toml
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


# ---------------------------------------------------------------------------
# _copy_file — OSError path (lines 723-724)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _write_if_missing — OSError path (lines 740-741)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# _files_identical — OSError path (lines 780-781)
# ---------------------------------------------------------------------------

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
        # Neither file exists → OSError
        assert _files_identical(a, b) is False


# ---------------------------------------------------------------------------
# _merge_mcp_json — error paths (lines 806-807, 810, 823-824, 832-833)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMergeMcpJson:
    """Cover _merge_mcp_json edge cases."""

    def test_corrupt_mcp_json_treated_as_empty(self, tmp_path: Path) -> None:
        """Corrupt .mcp.json is treated as empty dict, trw entry added."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text("INVALID JSON {{{{", encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_mcp_json_mcpservers_not_dict(self, tmp_path: Path) -> None:
        """mcpServers is not a dict → replaced with dict containing trw."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": "invalid"}), encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)

        data = json.loads(mcp_path.read_text(encoding="utf-8"))
        assert "trw" in data["mcpServers"]

    def test_existing_trw_key_reported_as_key(self, tmp_path: Path) -> None:
        """Existing 'trw' key is updated — result key matches result dict."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps(
            {"mcpServers": {"trw": {"command": "old", "args": []}}}
        ), encoding="utf-8")

        # When "updated" key exists
        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _merge_mcp_json(tmp_path, result)
        assert any(".mcp.json" in u for u in result["updated"])

    def test_write_error_existing_mcp_json(self, tmp_path: Path) -> None:
        """OSError writing merged .mcp.json → error."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

        result: dict[str, list[str]] = {"created": [], "errors": []}
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _merge_mcp_json(tmp_path, result)

        assert any("Failed to write" in e for e in result["errors"])

    def test_write_error_new_mcp_json(self, tmp_path: Path) -> None:
        """OSError creating new .mcp.json → error."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _merge_mcp_json(tmp_path, result)

        assert any("Failed to write" in e for e in result["errors"])

    def test_created_key_used_when_no_updated(self, tmp_path: Path) -> None:
        """When result has 'created' but not 'updated', 'created' key is used."""
        mcp_path = tmp_path / ".mcp.json"
        # No existing .mcp.json
        result: dict[str, list[str]] = {"created": [], "errors": []}
        _merge_mcp_json(tmp_path, result)
        assert any(".mcp.json" in c for c in result["created"])


# ---------------------------------------------------------------------------
# _write_installer_metadata — OSError path (lines 877-878)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestWriteInstallerMetadata:
    """Cover _write_installer_metadata error path."""

    def test_oserror_adds_to_errors(self, tmp_path: Path) -> None:
        """OSError writing metadata adds to errors."""
        result: dict[str, list[str]] = {"created": [], "errors": []}

        with patch("trw_mcp.state.persistence.FileStateWriter.write_yaml",
                   side_effect=OSError("disk full")):
            _write_installer_metadata(tmp_path, "init-project", result)

        assert any("Failed to write" in e for e in result["errors"])

    def test_writes_metadata_on_init(self, fake_git_repo: Path) -> None:
        """installer-meta.yaml is created with correct action."""
        result: dict[str, list[str]] = {"created": [], "errors": []}
        _write_installer_metadata(fake_git_repo, "init-project", result)

        meta_path = fake_git_repo / ".trw" / "installer-meta.yaml"
        # Meta path is created
        assert any("installer-meta" in c for c in result["created"])


# ---------------------------------------------------------------------------
# _verify_installation — all branches (lines 895, 903-909, 916)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestVerifyInstallation:
    """Cover all _verify_installation branches."""

    def test_non_executable_hook_warns(self, tmp_path: Path) -> None:
        """Non-executable hook → warning."""
        hooks_dir = tmp_path / ".claude" / "hooks"
        hooks_dir.mkdir(parents=True)
        hook = hooks_dir / "test-hook.sh"
        hook.write_text("#!/bin/sh\n", encoding="utf-8")
        # Remove executable bits
        hook.chmod(0o644)

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("not executable" in w for w in result["warnings"])

    def test_missing_mcp_json_warns(self, tmp_path: Path) -> None:
        """Missing .mcp.json → warning."""
        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any(".mcp.json not found" in w for w in result["warnings"])

    def test_mcp_json_missing_trw_entry_warns(self, tmp_path: Path) -> None:
        """mcp.json without 'trw' key → warning."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"other": {}}}), encoding="utf-8")

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("missing 'trw'" in w for w in result["warnings"])

    def test_invalid_mcp_json_warns(self, tmp_path: Path) -> None:
        """Invalid JSON in .mcp.json → warning."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text("INVALID", encoding="utf-8")

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("not valid JSON" in w for w in result["warnings"])

    def test_claude_md_missing_markers_warns(self, tmp_path: Path) -> None:
        """CLAUDE.md without TRW markers → warning."""
        mcp_path = tmp_path / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": {"trw": {}}}), encoding="utf-8")
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# My Project\n", encoding="utf-8")

        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(tmp_path, result)

        assert any("missing TRW" in w for w in result["warnings"])

    def test_healthy_install_no_warnings(self, initialized_repo: Path) -> None:
        """Healthy install produces no verification warnings."""
        result: dict[str, list[str]] = {"warnings": []}
        _verify_installation(initialized_repo, result)

        health_warnings = [
            w for w in result["warnings"]
            if "not executable" in w or "missing" in w.lower() or "not valid" in w
        ]
        assert len(health_warnings) == 0


# ---------------------------------------------------------------------------
# _generate_mcp_json — legacy helper (lines 927-928)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGenerateMcpJson:
    """Cover _generate_mcp_json legacy helper."""

    def test_returns_valid_json(self) -> None:
        """Returns valid JSON string with trw entry."""
        result_str = _generate_mcp_json()
        data = json.loads(result_str)
        assert "mcpServers" in data
        assert "trw" in data["mcpServers"]
        assert "command" in data["mcpServers"]["trw"]

    def test_ends_with_newline(self) -> None:
        """Generated JSON ends with newline."""
        result_str = _generate_mcp_json()
        assert result_str.endswith("\n")


# ---------------------------------------------------------------------------
# _trw_mcp_server_entry — fallback path
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTrwMcpServerEntry:
    """Cover _trw_mcp_server_entry."""

    def test_returns_entry_with_command(self) -> None:
        """Returns dict with command and args."""
        entry = _trw_mcp_server_entry()
        assert "command" in entry
        assert "args" in entry

    def test_falls_back_to_python_m_when_no_which(self) -> None:
        """Falls back to python -m when trw-mcp not in PATH."""
        with patch("shutil.which", return_value=None):
            entry = _trw_mcp_server_entry()
        assert "-m" in str(entry["command"]) or "trw_mcp" in str(entry["command"])


# ---------------------------------------------------------------------------
# _get_bundled_names — covers all branches
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestGetBundledNames:
    """Cover _get_bundled_names."""

    def test_returns_expected_categories(self) -> None:
        """Returns dict with skills, agents, hooks keys."""
        names = _get_bundled_names()
        assert "skills" in names
        assert "agents" in names
        assert "hooks" in names

    def test_returns_lists(self) -> None:
        """All values are lists."""
        names = _get_bundled_names()
        assert isinstance(names["skills"], list)
        assert isinstance(names["agents"], list)
        assert isinstance(names["hooks"], list)


# ---------------------------------------------------------------------------
# update_project with pip_install and dry_run interaction
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateProjectPipInstall:
    """Cover pip_install + dry_run interaction."""

    def test_pip_install_skipped_in_dry_run(self, initialized_repo: Path) -> None:
        """pip_install=True is ignored in dry_run mode."""
        with patch("subprocess.run") as mock_run:
            result = update_project(initialized_repo, pip_install=True, dry_run=True)

        # subprocess.run should NOT be called in dry_run mode
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# update_project — framework file that doesn't exist yet (line 253 'created' path)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestUpdateCreatesMissingFrameworkFiles:
    """Cover the 'created' branch when framework dest file doesn't exist."""

    def test_framework_file_created_when_missing(self, initialized_repo: Path) -> None:
        """Framework file that doesn't exist is created, not updated."""
        # Remove a framework file
        fw_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        fw_path.unlink()

        result = update_project(initialized_repo)

        # File should be created
        assert fw_path.exists()
        assert any("FRAMEWORK.md" in c for c in result["created"])

    def test_hook_file_created_when_missing(self, initialized_repo: Path) -> None:
        """Hook file that doesn't exist is created."""
        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        hook_path.unlink()

        result = update_project(initialized_repo)

        assert hook_path.exists()
        assert any("session-start.sh" in c for c in result["created"])
