"""Tests for ceremony sync and PRD auto-progress helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from trw_mcp.tools._deferred_delivery import _do_auto_progress, _do_index_sync
from trw_mcp.tools.ceremony import _do_instruction_sync


class TestDoClaudeMdSync:
    """CLAUDE.md sync during delivery ceremony."""

    def test_creates_or_updates_claude_md(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
        ):
            result = _do_instruction_sync(trw_dir)
        assert result["status"] == "success"
        assert "learnings_promoted" in result

    def test_deliver_includes_ceremony_sections(self, trw_project: Path) -> None:
        """trw_deliver path produces CLAUDE.md via canonical execute_claude_md_sync."""
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.state.claude_md.resolve_trw_dir", return_value=trw_dir),
        ):
            result = _do_instruction_sync(trw_dir)
        assert result["status"] == "success"

        claude_md = trw_project / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        assert "## TRW Behavioral Protocol (Auto-Generated)" in content
        assert "`trw_session_start()`" in content
        assert "`trw_deliver()`" in content
        assert "`trw_checkpoint(message)`" in content
        assert "`trw_learn(summary, detail)`" in content
        assert "orchestration" in content
        assert "/trw-ceremony-guide" in content
        assert "{{imperative_opener}}" not in content
        assert "{{ceremony_quick_ref}}" not in content
        assert "{{ceremony_phases}}" not in content
        assert "{{ceremony_table}}" not in content
        assert "{{ceremony_flows}}" not in content
        assert "{{closing_reminder}}" not in content


class TestDoIndexSync:
    """INDEX.md/ROADMAP.md sync during delivery ceremony."""

    def test_syncs_index_and_roadmap(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-001.md").write_text(
            "---\nprd:\n  id: PRD-CORE-001\n  title: Test\n  status: done\n  priority: P0\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            result = _do_index_sync()
        assert result["status"] == "success"
        assert (prds_dir.parent / "INDEX.md").exists()
        assert (prds_dir.parent / "ROADMAP.md").exists()


class TestDoAutoProgress:
    """PRD auto-progression during delivery ceremony (GAP-PROC-001)."""

    def test_skips_when_no_active_run(self) -> None:
        result = _do_auto_progress(None)
        assert result["status"] == "skipped"
        assert result["reason"] == "no_active_run"

    def test_skips_when_prds_dir_missing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "run"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "skipped"
        assert result["reason"] == "prds_dir_not_found"

    def test_progresses_implemented_to_done_on_deliver(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-099.md").write_text(
            "---\nprd:\n  id: PRD-CORE-099\n  title: Test\n"
            "  status: implemented\n  priority: P1\n  category: CORE\n---\n"
            "# PRD-CORE-099\nSome content for density.\n",
            encoding="utf-8",
        )
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope:\n  - PRD-CORE-099\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] >= 1
        content = (prds_dir / "PRD-CORE-099.md").read_text(encoding="utf-8")
        assert "status: done" in content

    def test_returns_zero_applied_for_terminal_statuses(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-099.md").write_text(
            "---\nprd:\n  id: PRD-CORE-099\n  title: Test\n  status: done\n  priority: P1\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope:\n  - PRD-CORE-099\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] == 0
