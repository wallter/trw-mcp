"""Tests for the ``trw-mcp uninstall`` CLI subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from trw_mcp.server._subcommands import _run_uninstall


@pytest.mark.unit
class TestUninstall:
    """Unit tests for _run_uninstall handler."""

    def test_dry_run_lists_files(self, tmp_path: Path) -> None:
        """Dry run lists TRW files without deleting."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".trw" / "config.yaml").write_text("test: true")
        (tmp_path / ".mcp.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=True, yes=False)
        _run_uninstall(args)

        assert (tmp_path / ".trw").exists()  # Not deleted
        assert (tmp_path / ".mcp.json").exists()

    def test_yes_removes_files(self, tmp_path: Path) -> None:
        """With --yes, removes files without prompting."""
        (tmp_path / ".trw").mkdir()
        (tmp_path / ".mcp.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()
        assert not (tmp_path / ".mcp.json").exists()

    def test_no_trw_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Empty project prints no-files message."""
        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=False)
        _run_uninstall(args)
        assert "No TRW files found" in capsys.readouterr().out

    def test_partial_removal(self, tmp_path: Path) -> None:
        """Only removes files that exist."""
        (tmp_path / ".trw").mkdir()  # Only .trw, no .mcp.json

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()

    def test_removes_claude_subdirs(self, tmp_path: Path) -> None:
        """Removes .claude/skills, .claude/agents, .claude/hooks but preserves .claude/ itself."""
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        (claude_dir / "skills").mkdir()
        (claude_dir / "skills" / "trw-review-pr").mkdir(parents=True)
        (claude_dir / "skills" / "trw-review-pr" / "SKILL.md").write_text("# Skill")
        (claude_dir / "agents").mkdir()
        (claude_dir / "agents" / "reviewer.md").write_text("# Agent")
        (claude_dir / "hooks").mkdir()
        (claude_dir / "hooks" / "lib-trw.sh").write_text("#!/bin/bash")
        # User file outside TRW-managed dirs
        (claude_dir / "settings.json").write_text("{}")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (claude_dir / "skills").exists()
        assert not (claude_dir / "agents").exists()
        assert not (claude_dir / "hooks").exists()
        # .claude/ itself and user files preserved
        assert claude_dir.exists()
        assert (claude_dir / "settings.json").exists()

    def test_default_target_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Defaults to current directory when target_dir is '.'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".trw").mkdir()

        args = argparse.Namespace(target_dir=".", dry_run=False, yes=True)
        _run_uninstall(args)

        assert not (tmp_path / ".trw").exists()

    def test_dry_run_shows_file_count(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Dry run shows directory file count in output."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        (trw_dir / "config.yaml").write_text("key: val")
        (trw_dir / "index.yaml").write_text("entries: []")

        args = argparse.Namespace(target_dir=str(tmp_path), dry_run=True, yes=False)
        _run_uninstall(args)

        out = capsys.readouterr().out
        assert "2 files" in out
        assert "--dry-run" in out
