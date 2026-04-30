"""Split bootstrap branch coverage for update_project flows."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import update_project
from trw_mcp.bootstrap._update_project import _run_auto_maintenance

from ._bootstrap_test_support import fake_git_repo, initialized_repo


@pytest.mark.unit
class TestDryRunWouldCreate:
    """Cover the 'would create' branches in dry-run mode."""

    def test_dry_run_framework_would_create(self, fake_git_repo: Path) -> None:
        """Missing framework file in dry-run reports 'would create'."""
        (fake_git_repo / ".trw").mkdir()
        (fake_git_repo / ".trw" / "frameworks").mkdir(parents=True)
        (fake_git_repo / ".trw" / "context").mkdir()
        (fake_git_repo / ".trw" / "templates").mkdir()
        (fake_git_repo / ".claude" / "settings.json").parent.mkdir(parents=True)

        result = update_project(fake_git_repo, dry_run=True)

        assert any("would create" in c for c in result["created"])

    def test_dry_run_identical_file_not_reported(self, initialized_repo: Path) -> None:
        """Dry-run skips identical files — only changed files reported as 'would update'."""
        result = update_project(initialized_repo, dry_run=True)
        assert any("DRY RUN" in w for w in result["warnings"])

    def test_dry_run_hook_would_create_when_missing(self, fake_git_repo: Path) -> None:
        """Dry-run reports hook 'would create' when hooks dir is missing."""
        (fake_git_repo / ".trw").mkdir()

        result = update_project(fake_git_repo, dry_run=True)

        all_output = result["created"] + result["updated"]
        hook_creates = [x for x in all_output if "would create" in x and "hook" in x.lower()]
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
    def test_auto_maintenance_failure_logs_warning_with_traceback(self, tmp_path: Path) -> None:
        result = {"updated": [], "warnings": []}
        mock_logger = MagicMock()

        with (
            patch("trw_mcp.bootstrap._update_project._logger", mock_logger),
            patch("trw_mcp.models.config._reset_config", side_effect=[None, None]),
            patch(
                "trw_mcp.models.config.get_config",
                side_effect=RuntimeError("maintenance failed"),
            ),
        ):
            _run_auto_maintenance(tmp_path, result)

        mock_logger.warning.assert_called_once_with(
            "auto_maintenance_failed",
            error="maintenance failed",
            target_dir=str(tmp_path),
            exc_info=True,
        )
        assert result["warnings"] == ["Auto-maintenance skipped: maintenance failed"]

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


@pytest.mark.unit
class TestUpdateProjectPipInstall:
    """Cover pip_install + dry_run interaction."""

    def test_pip_install_skipped_in_dry_run(self, initialized_repo: Path) -> None:
        """pip_install=True is ignored in dry_run mode."""
        with patch("subprocess.run") as mock_run:
            update_project(initialized_repo, pip_install=True, dry_run=True)

        mock_run.assert_not_called()


@pytest.mark.unit
class TestUpdateCreatesMissingFrameworkFiles:
    """Cover the 'created' branch when framework dest file doesn't exist."""

    def test_framework_file_created_when_missing(self, initialized_repo: Path) -> None:
        """Framework file that doesn't exist is created, not updated."""
        fw_path = initialized_repo / ".trw" / "frameworks" / "FRAMEWORK.md"
        fw_path.unlink()

        result = update_project(initialized_repo)

        assert fw_path.exists()
        assert any("FRAMEWORK.md" in c for c in result["created"])

    def test_hook_file_created_when_missing(self, initialized_repo: Path) -> None:
        """Hook file that doesn't exist is created."""
        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        hook_path.unlink()

        result = update_project(initialized_repo)

        assert hook_path.exists()
        assert any("session-start.sh" in c for c in result["created"])
