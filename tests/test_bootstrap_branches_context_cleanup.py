"""Split bootstrap branch coverage for context cleanup paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.bootstrap import _CONTEXT_ALLOWLIST, _cleanup_context_transients, update_project

from ._bootstrap_test_support import fake_git_repo, initialized_repo  # noqa: F401


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
        with patch("trw_mcp.bootstrap._version_migration.os.unlink", side_effect=OSError("permission denied")):
            _cleanup_context_transients(tmp_path, result)

        assert len(result["errors"]) == 1
        assert "permission denied" in result["errors"][0]
        assert result["cleaned"] == []

    def test_cleanup_skips_symlinked_context_directory(self, tmp_path: Path) -> None:
        """A redirected context parent is advisory state, never a deletion target."""
        external = tmp_path / "external"
        external.mkdir()
        victim = external / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "context").symlink_to(external, target_is_directory=True)

        result: dict[str, list[str]] = {"cleaned": [], "errors": [], "warnings": []}
        _cleanup_context_transients(tmp_path, result)

        assert victim.read_text(encoding="utf-8") == "keep"
        assert result["cleaned"] == []
        assert any("Skipped unsafe context cleanup" in warning for warning in result["warnings"])

    def test_cleanup_skips_unreadable_context_directory(self, tmp_path: Path) -> None:
        context = tmp_path / ".trw" / "context"
        context.mkdir(parents=True)
        result: dict[str, list[str]] = {"cleaned": [], "errors": [], "warnings": []}

        with patch("trw_mcp.bootstrap._version_migration.os.listdir", side_effect=OSError("read failed")):
            _cleanup_context_transients(tmp_path, result)

        assert result["cleaned"] == []
        assert result["errors"] == []
        assert any("Skipped unreadable context cleanup" in warning for warning in result["warnings"])

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

        for name in _CONTEXT_ALLOWLIST:
            assert (context / name).exists(), f"Allowlisted file deleted: {name}"
        for name in stale_files:
            assert not (context / name).exists(), f"Stale file not removed: {name}"
        assert len(result["cleaned"]) == len(stale_files)
        assert "cleaned" in result
