"""Split bootstrap branch coverage for update_project flows."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.bootstrap import update_project
from trw_mcp.bootstrap._update_project import _run_auto_maintenance

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401


@pytest.mark.unit
class TestDryRunReportsTheScratchDiff:
    """PRD-INFRA-190 FR02: a dry run reports repo-relative paths from a scratch run's diff."""

    def test_missing_artifacts_are_reported_created_and_not_written(self, fake_git_repo: Path) -> None:
        (fake_git_repo / ".trw").mkdir()
        (fake_git_repo / ".trw" / "managed-artifacts.yaml").write_text(
            "version: 2\ncontent_hashes: {}\n", encoding="utf-8"
        )

        result = update_project(fake_git_repo, dry_run=True)

        from trw_mcp.bootstrap import _DATA_DIR

        hook = next(p.name for p in sorted((_DATA_DIR / "hooks").glob("*.sh")))
        assert f".claude/hooks/{hook}" in result["created"]
        assert ".claude/agents/trw-implementer.md" in result["created"]
        assert any(p.startswith(".claude/skills/") for p in result["created"])
        assert "CLAUDE.md" in result["created"]
        assert ".mcp.json" in result["created"]
        # Nothing the report names was written. (Whole-tree byte identity is
        # pinned out-of-process in test_update_project_determinism.py: this
        # harness reroutes resolve_trw_dir to the fixture root.)
        for rel in (".claude", "CLAUDE.md", ".mcp.json"):
            assert not (fake_git_repo / rel).exists()

    def test_dry_run_warns_and_reports_nothing_for_a_current_install(self, initialized_repo: Path) -> None:
        update_project(initialized_repo)
        result = update_project(initialized_repo, dry_run=True)
        assert any("DRY RUN" in w for w in result["warnings"])
        assert ".claude/agents/trw-implementer.md" not in result["updated"] + result["created"]
        assert ".mcp.json" not in result["updated"]

    @pytest.mark.parametrize("content", [json.dumps({"mcpServers": {"other": {}}}), "not-json{{{"])
    def test_mcp_json_without_trw_entry_is_reported_updated(self, initialized_repo: Path, content: str) -> None:
        mcp_path = initialized_repo / ".mcp.json"
        mcp_path.write_text(content, encoding="utf-8")

        result = update_project(initialized_repo, dry_run=True)

        assert ".mcp.json" in result["updated"]
        assert mcp_path.read_text(encoding="utf-8") == content


@pytest.mark.unit
class TestUpdateOSErrorPaths:
    """Cover OSError branches in update_project copy loops."""

    def test_framework_copy_oserror(self, initialized_repo: Path) -> None:
        """OSError during framework file copy adds to errors."""
        with patch("shutil.copy2", side_effect=OSError("disk full")):
            result = update_project(initialized_repo)
        assert any("Failed to copy" in e or "Failed to snapshot" in e for e in result["errors"])

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
        assert any("Failed to copy" in e or "Failed to snapshot" in e for e in result["errors"])

    def test_skill_copy_oserror(self, initialized_repo: Path) -> None:
        """OSError during skill file copy adds to errors."""
        from trw_mcp.bootstrap import _DATA_DIR

        if not (_DATA_DIR / "skills").is_dir():
            pytest.skip("no bundled skills")
        # An identical destination is never rewritten (FR03), so remove one to force a copy.
        (initialized_repo / ".claude" / "skills" / "trw-learn" / "SKILL.md").unlink()

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

    def test_agent_write_oserror(self, initialized_repo: Path) -> None:
        """OSError while writing a resolved agent adds to errors.

        sub_5ctrrLJ: agents are now materialized via the resolve-and-write path
        (``_install_one_agent`` → ``Path.write_text``), not ``shutil.copy2``, so
        the failure is scoped to the ``.claude/agents/`` destination write.
        """
        from trw_mcp.bootstrap import _DATA_DIR

        if not (_DATA_DIR / "agents").is_dir():
            pytest.skip("no bundled agents")
        # An identical destination is never rewritten (FR03), so remove one to force a write.
        (initialized_repo / ".claude" / "agents" / "trw-implementer.md").unlink()

        original_write = Path.write_text

        def fail_agent_write(self: Path, *args: object, **kwargs: object) -> int:
            if f"{'/.claude/agents/'}" in str(self) and self.suffix == ".md":
                raise OSError("agent write failed")
            return original_write(self, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(Path, "write_text", fail_agent_write):
            result = update_project(initialized_repo)
        assert any("Failed to write" in e for e in result["errors"])


@pytest.mark.unit
class TestRunAutoMaintenance:
    def test_auto_maintenance_failure_logs_warning_with_traceback(self, tmp_path: Path) -> None:
        result = {"updated": [], "warnings": []}
        mock_logger = MagicMock()

        with (
            patch("trw_mcp.bootstrap._update_external._logger", mock_logger),
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
            patch("trw_mcp.bootstrap._update_external._logger", mock_logger),
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
        """pip_install=True is only named under would_run in dry_run mode."""
        with patch("trw_mcp.bootstrap._update_project._pip_install_package") as mock_pip:
            result = update_project(initialized_repo, pip_install=True, dry_run=True)

        mock_pip.assert_not_called()
        assert "pip_install" in result["would_run"]


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

    def test_hook_file_missing_since_last_manifest_stays_deleted(self, initialized_repo: Path) -> None:
        """PRD-INFRA-192 FR10: a manifest-recorded hook that vanished stays deleted (tombstoned).

        Superseded the old "always recreate a missing hook" contract, which was
        the HOOK-RESURRECT defect: a hook the user deliberately deleted came
        back on the next update-project. Coverage for the genuinely-never-
        provisioned case (no manifest record at all) lives in
        test_bootstrap_tombstones.py.
        """
        hook_path = initialized_repo / ".claude" / "hooks" / "session-start.sh"
        hook_path.unlink()

        result = update_project(initialized_repo)

        assert not result["errors"], result["errors"]
        assert not hook_path.exists()
        assert not any("session-start.sh" in c for c in result["created"])
