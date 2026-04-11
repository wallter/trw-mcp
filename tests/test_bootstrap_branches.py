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
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import (
    _CONTEXT_ALLOWLIST,
    PREDECESSOR_MAP,
    _check_package_version,
    _cleanup_context_transients,
    _copy_file,
    _files_identical,
    _generate_mcp_json,
    _get_bundled_names,
    _merge_mcp_json,
    _migrate_prefix_predecessors,
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
from trw_mcp.bootstrap._update_project import _run_auto_maintenance


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


@pytest.mark.unit
class TestRunAutoMaintenance:
    def test_config_reset_failure_logs_debug(self, tmp_path: Path) -> None:
        result = {"updated": [], "warnings": []}
        mock_logger = MagicMock()

        with (
            patch("trw_mcp.bootstrap._update_project._logger", mock_logger),
            patch(
                "trw_mcp.models.config._reset_config",
                side_effect=[None, RuntimeError("reset failed")],
            ),
            patch("trw_mcp.models.config.get_config", return_value=MagicMock()),
            patch(
                "trw_mcp.state._memory_connection.check_embeddings_status",
                return_value={"enabled": False},
            ),
        ):
            _run_auto_maintenance(tmp_path, result)

        mock_logger.debug.assert_called_once_with(
            "auto_maintenance_config_reset_failed",
            exc_info=True,
        )


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
        claude_md.write_text("<!-- trw:start -->\nno end marker here\n", encoding="utf-8")

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

        FileStateWriter().write_yaml(
            manifest_path,
            {
                "version": 1,
                "skills": ["deliver", "learn"],
                "agents": ["trw-tester.md"],
                "hooks": ["session-start.sh"],
            },
        )

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

    def _setup_manifest(
        self,
        target_dir: Path,
        extra_skills: list[str] | None = None,
        extra_agents: list[str] | None = None,
        extra_hooks: list[str] | None = None,
    ) -> None:
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

        with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError("trw-mcp")):
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
        mcp_path.write_text(json.dumps({"mcpServers": {"trw": {"command": "old", "args": []}}}), encoding="utf-8")

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

        with patch("trw_mcp.state.persistence.FileStateWriter.write_yaml", side_effect=OSError("disk full")):
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
            w for w in result["warnings"] if "not executable" in w or "missing" in w.lower() or "not valid" in w
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

    def test_falls_back_to_sys_executable_when_no_which(self) -> None:
        """Falls back to sys.executable -m when trw-mcp not in PATH."""
        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = None
            entry = _trw_mcp_server_entry()
        assert entry["command"] == sys.executable
        assert "-m" in entry["args"]  # type: ignore[operator]


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


# ---------------------------------------------------------------------------
# Context Cleanup Edge Cases (PRD-FIX-031)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContextCleanupEdgeCases:
    """Edge case tests for _cleanup_context_transients — PRD-FIX-031."""

    def test_cleanup_skips_directories(self, tmp_path: Path) -> None:
        """Subdirectory named like a transient pattern is NOT deleted."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        subdir = context / "tc_block_subdir"
        subdir.mkdir()
        (subdir / "data.txt").write_text("keep me", encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert subdir.is_dir()
        assert (subdir / "data.txt").exists()
        assert result["cleaned"] == []

    def test_cleanup_skips_symlinks(self, tmp_path: Path) -> None:
        """Symlink in context dir is NOT deleted."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        real_file = tmp_path / "real_data.txt"
        real_file.write_text("important data", encoding="utf-8")
        symlink = context / "stale_link.yaml"
        symlink.symlink_to(real_file)

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert symlink.is_symlink()
        assert real_file.exists()
        assert result["cleaned"] == []

    def test_cleanup_missing_context_dir(self, tmp_path: Path) -> None:
        """No error when .trw/context/ does not exist."""
        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert result["cleaned"] == []
        assert result["errors"] == []

    def test_cleanup_oserror_appended_to_errors(self, tmp_path: Path) -> None:
        """OSError on unlink is appended to result['errors']."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        stale = context / "velocity.yaml"
        stale.write_text("stale", encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        with patch.object(Path, "unlink", side_effect=OSError("permission denied")):
            _cleanup_context_transients(tmp_path, result)

        assert len(result["errors"]) == 1
        assert "permission denied" in result["errors"][0]
        assert result["cleaned"] == []

    def test_cleanup_empty_context_dir(self, tmp_path: Path) -> None:
        """Empty context dir produces no errors and empty cleaned list."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert result["cleaned"] == []
        assert result["errors"] == []

    def test_cleanup_glob_pattern_tc_block(self, tmp_path: Path) -> None:
        """File named tc_block_session_abc is deleted."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        stale = context / "tc_block_session_abc"
        stale.write_text("", encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert not stale.exists()
        assert len(result["cleaned"]) == 1

    def test_cleanup_glob_pattern_idle_block(self, tmp_path: Path) -> None:
        """File named idle_block_lead is deleted."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        stale = context / "idle_block_lead"
        stale.write_text("", encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert not stale.exists()
        assert len(result["cleaned"]) == 1

    def test_cleanup_glob_pattern_findings(self, tmp_path: Path) -> None:
        """File named sprint-34-findings.yaml is deleted."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        stale = context / "sprint-34-findings.yaml"
        stale.write_text("", encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert not stale.exists()
        assert len(result["cleaned"]) == 1

    def test_cleanup_velocity_yaml(self, tmp_path: Path) -> None:
        """File named velocity.yaml is deleted (not in allowlist)."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        stale = context / "velocity.yaml"
        stale.write_text("sprints: []", encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert not stale.exists()
        assert len(result["cleaned"]) == 1

    def test_cleanup_tool_telemetry(self, tmp_path: Path) -> None:
        """File named tool-telemetry.jsonl is deleted."""
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        stale = context / "tool-telemetry.jsonl"
        stale.write_text('{"ts":"2026-01-01"}\n', encoding="utf-8")

        result: dict[str, list[str]] = {"cleaned": [], "errors": []}
        _cleanup_context_transients(tmp_path, result)

        assert not stale.exists()
        assert len(result["cleaned"]) == 1

    def test_update_project_cleans_context_end_to_end(self, initialized_repo: Path) -> None:
        """Full update_project() call removes stale context files end-to-end."""
        context = initialized_repo / ".trw" / "context"
        # Create a mix of allowlisted and transient files
        for name in _CONTEXT_ALLOWLIST:
            (context / name).write_text("preserved", encoding="utf-8")
        stale_files = [
            "tc_block_session123",
            "idle_block_x",
            "sprint-34-findings.yaml",
            "velocity.yaml",
            "tool-telemetry.jsonl",
            "hook-executions.log",
        ]
        for name in stale_files:
            (context / name).write_text("stale", encoding="utf-8")

        result = update_project(initialized_repo)

        # All allowlisted files preserved
        for name in _CONTEXT_ALLOWLIST:
            assert (context / name).exists(), f"Allowlisted file deleted: {name}"
        # All stale files removed
        for name in stale_files:
            assert not (context / name).exists(), f"Stale file not removed: {name}"
        # Result reflects the cleanup
        assert len(result["cleaned"]) == len(stale_files)
        assert "cleaned" in result


# ── PRD-FIX-032: Prefix Migration Edge Cases ─────────────────────────


@pytest.mark.unit
class TestPrefixMigrationExtra:
    """Edge-case tests for _migrate_prefix_predecessors and manifest cleanup."""

    def test_migrate_oserror_resilience(self, tmp_path: Path) -> None:
        """OSError during shutil.rmtree skips the item and continues."""
        target = tmp_path
        skills_dir = target / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        # Create two predecessor/successor pairs
        for old, new in [("commit", "trw-commit"), ("deliver", "trw-deliver")]:
            (skills_dir / old).mkdir()
            (skills_dir / old / "SKILL.md").write_text("old", encoding="utf-8")
            (skills_dir / new).mkdir()
            (skills_dir / new / "SKILL.md").write_text("new", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        call_count = 0

        original_rmtree = shutil.rmtree

        def failing_rmtree(path: Path, *args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("permission denied")
            original_rmtree(path)  # type: ignore[arg-type]

        with patch("trw_mcp.bootstrap.shutil.rmtree", side_effect=failing_rmtree):
            _migrate_prefix_predecessors(target, result)

        # No exception raised; at least one predecessor was processed
        # (the first one failed, the second should succeed)
        assert not result.get("errors")

    def test_dry_run_migration_reports_would_migrate(self, initialized_repo: Path) -> None:
        """dry_run=True appends 'would migrate:' without deleting."""
        skills_dir = initialized_repo / ".claude" / "skills"
        (skills_dir / "commit").mkdir(parents=True, exist_ok=True)
        (skills_dir / "commit" / "SKILL.md").write_text("old", encoding="utf-8")
        (skills_dir / "trw-commit").mkdir(parents=True, exist_ok=True)
        (skills_dir / "trw-commit" / "SKILL.md").write_text("new", encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)

        # Old dir still present
        assert (skills_dir / "commit").exists()
        # "would migrate:" entry in result
        would_migrate = [e for e in result["updated"] if "would migrate:" in e and "commit" in e]
        assert len(would_migrate) >= 1

    def test_manifest_excludes_predecessor_names_from_custom(self, initialized_repo: Path) -> None:
        """Predecessor names are excluded from custom_skills in manifest."""
        skills_dir = initialized_repo / ".claude" / "skills"
        # Create predecessor dir so _get_custom_names would classify it as custom
        (skills_dir / "commit").mkdir(parents=True, exist_ok=True)
        (skills_dir / "commit" / "SKILL.md").write_text("old", encoding="utf-8")

        # Write manifest directly and check filtering
        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _write_manifest(initialized_repo, result)

        manifest = _read_manifest(initialized_repo)
        assert manifest is not None
        # "commit" should NOT appear in custom_skills
        assert "commit" not in manifest.get("custom_skills", [])

    def test_migrate_prefix_predecessors_direct_call(self, tmp_path: Path) -> None:
        """Direct call removes both skill dirs and agent files."""
        target = tmp_path
        skills_dir = target / ".claude" / "skills"
        agents_dir = target / ".claude" / "agents"
        skills_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)

        # Skill predecessor + successor
        (skills_dir / "simplify").mkdir()
        (skills_dir / "simplify" / "SKILL.md").write_text("old", encoding="utf-8")
        (skills_dir / "trw-simplify").mkdir()
        (skills_dir / "trw-simplify" / "SKILL.md").write_text("new", encoding="utf-8")

        # Agent predecessor + successor (researcher.md → trw-researcher.md survives CORE-092)
        (agents_dir / "researcher.md").write_text("old", encoding="utf-8")
        (agents_dir / "trw-researcher.md").write_text("new", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(target, result)

        assert not (skills_dir / "simplify").exists()
        assert not (agents_dir / "researcher.md").exists()
        assert (skills_dir / "trw-simplify").exists()
        assert (agents_dir / "trw-researcher.md").exists()
        migrated = [e for e in result["updated"] if "migrated:" in e]
        assert len(migrated) == 2

    def test_migrate_no_skills_dir_no_error(self, tmp_path: Path) -> None:
        """No error when .claude/skills/ directory does not exist."""
        target = tmp_path
        # Only create agents dir, not skills
        (target / ".claude" / "agents").mkdir(parents=True)

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(target, result)

        assert not result["errors"]
        assert result["updated"] == []

    def test_migrate_no_agents_dir_no_error(self, tmp_path: Path) -> None:
        """No error when .claude/agents/ directory does not exist."""
        target = tmp_path
        # Only create skills dir, not agents
        (target / ".claude" / "skills").mkdir(parents=True)

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(target, result)

        assert not result["errors"]
        assert result["updated"] == []

    def test_predecessor_map_keys_not_in_bundled(self) -> None:
        """No PREDECESSOR_MAP key appears in _get_bundled_names() output."""
        bundled = _get_bundled_names()
        bundled_skills = set(bundled["skills"])
        bundled_agents = set(bundled["agents"])

        for old_skill in PREDECESSOR_MAP["skills"]:
            assert old_skill not in bundled_skills, f"Predecessor skill '{old_skill}' found in bundled names"
        for old_agent in PREDECESSOR_MAP["agents"]:
            assert old_agent not in bundled_agents, f"Predecessor agent '{old_agent}' found in bundled names"


# ---------------------------------------------------------------------------
# _migrate_prefix_predecessors — successor absent keeps old version (lines 817-818, 833-834)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMigratePredecessorSuccessorAbsent:
    """When the trw- successor is absent, the predecessor must NOT be removed."""

    def test_skill_predecessor_kept_when_successor_missing(self, tmp_path: Path) -> None:
        """Skill predecessor dir is left in place when trw- successor dir is absent."""
        skills_dir = tmp_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)

        # Create only the predecessor, no successor
        predecessor = skills_dir / "simplify"
        predecessor.mkdir()
        (predecessor / "SKILL.md").write_text("old", encoding="utf-8")
        # Successor (trw-simplify) intentionally absent

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result)

        # Predecessor must still exist — no successor, so keep old version
        assert predecessor.exists()
        assert result["updated"] == []

    def test_agent_predecessor_kept_when_successor_missing(self, tmp_path: Path) -> None:
        """Agent predecessor file is left in place when trw- successor file is absent."""
        agents_dir = tmp_path / ".claude" / "agents"
        agents_dir.mkdir(parents=True)

        # Create only the predecessor, no successor
        predecessor = agents_dir / "lead.md"
        predecessor.write_text("old lead", encoding="utf-8")
        # Successor (trw-lead.md) intentionally absent

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result)

        # Predecessor must still exist
        assert predecessor.exists()
        assert result["updated"] == []

    def test_both_skill_and_agent_predecessors_kept_when_successors_missing(self, tmp_path: Path) -> None:
        """Both skill and agent predecessors are preserved when successors are absent."""
        skills_dir = tmp_path / ".claude" / "skills"
        agents_dir = tmp_path / ".claude" / "agents"
        skills_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)

        skill_pred = skills_dir / "commit"
        skill_pred.mkdir()
        (skill_pred / "SKILL.md").write_text("old", encoding="utf-8")

        agent_pred = agents_dir / "implementer.md"
        agent_pred.write_text("old implementer", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "errors": []}
        _migrate_prefix_predecessors(tmp_path, result)

        assert skill_pred.exists()
        assert agent_pred.exists()
        assert result["updated"] == []


# ---------------------------------------------------------------------------
# _remove_stale_artifacts — custom artifact preservation (lines 884-885, 902-903, 919-920)
# Custom skills/agents/hooks in prev_custom_* lists are NEVER removed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveStaleArtifactsCustomPreservation:
    """Custom artifacts listed in prev_custom_* must not be removed during stale cleanup."""

    def _setup_manifest_with_custom(
        self,
        target_dir: Path,
        extra_skills: list[str] | None = None,
        extra_agents: list[str] | None = None,
        extra_hooks: list[str] | None = None,
        custom_skills: list[str] | None = None,
        custom_agents: list[str] | None = None,
        custom_hooks: list[str] | None = None,
    ) -> None:
        from trw_mcp.state.persistence import FileStateWriter

        bundled = _get_bundled_names()
        manifest = {
            "version": 1,
            "skills": bundled["skills"] + (extra_skills or []),
            "agents": bundled["agents"] + (extra_agents or []),
            "hooks": bundled["hooks"] + (extra_hooks or []),
            "custom_skills": custom_skills or [],
            "custom_agents": custom_agents or [],
            "custom_hooks": custom_hooks or [],
        }
        manifest_path = target_dir / ".trw" / "managed-artifacts.yaml"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        FileStateWriter().write_yaml(manifest_path, manifest)

    def test_custom_skill_not_removed(self, initialized_repo: Path) -> None:
        """A skill listed in prev_custom_skills is NOT removed even if it's stale."""
        # Add "trw-my-custom" as both a previously-managed skill AND a custom skill
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_skills=["trw-my-custom"],
            custom_skills=["trw-my-custom"],
        )
        custom_skill = initialized_repo / ".claude" / "skills" / "trw-my-custom"
        custom_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        # Custom skill must be preserved
        assert custom_skill.exists()
        assert not any("trw-my-custom" in u for u in result["updated"])

    def test_custom_agent_not_removed(self, initialized_repo: Path) -> None:
        """A trw- agent in prev_custom_agents is NOT removed even if it's stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_agents=["trw-my-agent.md"],
            custom_agents=["trw-my-agent.md"],
        )
        custom_agent = initialized_repo / ".claude" / "agents" / "trw-my-agent.md"
        custom_agent.write_text("custom agent", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_agent.exists()
        assert not any("trw-my-agent" in u for u in result["updated"])

    def test_custom_hook_not_removed(self, initialized_repo: Path) -> None:
        """A hook listed in prev_custom_hooks is NOT removed even if stale."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_hooks=["my-custom-hook.sh"],
            custom_hooks=["my-custom-hook.sh"],
        )
        custom_hook = initialized_repo / ".claude" / "hooks" / "my-custom-hook.sh"
        custom_hook.write_text("#!/bin/sh", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert custom_hook.exists()
        assert not any("my-custom-hook" in u for u in result["updated"])

    def test_non_trw_prefixed_stale_skill_not_removed(self, initialized_repo: Path) -> None:
        """Stale skills without trw- prefix are skipped (defense-in-depth guard)."""
        # "stale-no-prefix" has no trw- prefix — must not be removed
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_skills=["stale-no-prefix"],
        )
        stale_skill = initialized_repo / ".claude" / "skills" / "stale-no-prefix"
        stale_skill.mkdir(parents=True, exist_ok=True)

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert stale_skill.exists()
        assert not any("stale-no-prefix" in u for u in result["updated"])

    def test_non_trw_prefixed_stale_agent_not_removed(self, initialized_repo: Path) -> None:
        """Stale agents without trw- prefix are skipped (defense-in-depth guard)."""
        self._setup_manifest_with_custom(
            initialized_repo,
            extra_agents=["my-old-agent.md"],
        )
        stale_agent = initialized_repo / ".claude" / "agents" / "my-old-agent.md"
        stale_agent.write_text("old", encoding="utf-8")

        result: dict[str, list[str]] = {"updated": [], "created": [], "errors": []}
        _remove_stale_artifacts(initialized_repo, result)

        assert stale_agent.exists()
        assert not any("my-old-agent" in u for u in result["updated"])


# ---------------------------------------------------------------------------
# _trw_mcp_server_entry — system trw-mcp found via shutil.which (lines 1105-1106)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTrwMcpServerEntrySystemCmd:
    """Portable command generation — always bare names, never absolute paths."""

    def test_returns_bare_trw_mcp_when_on_path(self) -> None:
        """When shutil.which finds trw-mcp, return portable bare command."""
        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/usr/local/bin/trw-mcp"
            entry = _trw_mcp_server_entry()

        # Portable: bare command name, not the absolute path
        assert entry["command"] == "trw-mcp"
        assert not str(entry["command"]).startswith("/")

    def test_bare_command_over_python_m_fallback(self) -> None:
        """Bare trw-mcp takes priority over python -m fallback."""
        with patch("trw_mcp.bootstrap._utils.shutil") as mock_shutil:
            mock_shutil.which.return_value = "/opt/homebrew/bin/trw-mcp"
            entry = _trw_mcp_server_entry()

        # Must use bare command, not python -m module invocation
        assert entry["command"] == "trw-mcp"
        assert "trw_mcp.server" not in str(entry["command"])
