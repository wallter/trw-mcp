"""Tests for PRD-CORE-019: Session ceremony composite tools.

Covers:
- trw_session_start: recall + status bundling, partial failure resilience
- trw_deliver: reflect + checkpoint + claude_md_sync + index_sync bundling
- _find_active_run helper
- _do_checkpoint, _do_reflect, _do_claude_md_sync, _do_index_sync internals
- _do_auto_progress: PRD auto-progression during delivery (GAP-PROC-001)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.tools.ceremony import (
    _do_auto_progress,
    _do_checkpoint,
    _do_claude_md_sync,
    _do_index_sync,
    _do_reflect,
    _find_active_run,
    _get_run_status,
)


# --- Fixtures ---


@pytest.fixture()
def trw_project(tmp_path: Path) -> Path:
    """Create a minimal .trw/ project structure."""
    trw_dir = tmp_path / ".trw"
    learnings_dir = trw_dir / "learnings" / "entries"
    learnings_dir.mkdir(parents=True)
    (trw_dir / "reflections").mkdir()
    (trw_dir / "context").mkdir()

    # Create a sample learning entry
    (learnings_dir / "2026-02-10-sample.yaml").write_text(
        "id: L-sample001\nsummary: Test learning\ndetail: Some detail\n"
        "status: active\nimpact: 0.8\ntags:\n  - testing\n"
        "access_count: 0\nq_observations: 0\nq_value: 0.5\n"
        "source_type: agent\nsource_identity: ''\n",
        encoding="utf-8",
    )

    # Create index.yaml
    (trw_dir / "learnings" / "index.yaml").write_text(
        "total_entries: 1\n", encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure."""
    d = tmp_path / "docs" / "task" / "runs" / "20260211T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask_name: test-task\n",
        encoding="utf-8",
    )
    # Create empty events.jsonl
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


# --- _find_active_run ---


class TestFindActiveRun:
    """Helper function for locating active runs."""

    def test_returns_none_when_no_task_root(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir):
            result = _find_active_run()
        assert result is None

    def test_finds_run_directory(self, tmp_path: Path, run_dir: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        with patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir):
            with patch("trw_mcp.tools.ceremony._config") as mock_config:
                mock_config.task_root = "docs"
                result = _find_active_run()
        assert result is not None
        assert "20260211T120000Z-test" in str(result)


# --- _get_run_status ---


class TestGetRunStatus:
    """Status extraction from run directory."""

    def test_extracts_status(self, run_dir: Path) -> None:
        result = _get_run_status(run_dir)
        assert result["phase"] == "implement"
        assert result["status"] == "active"
        assert result["task_name"] == "test-task"

    def test_handles_missing_run_yaml(self, tmp_path: Path) -> None:
        result = _get_run_status(tmp_path)
        assert result["active_run"] == str(tmp_path)


# --- _do_checkpoint ---


class TestDoCheckpoint:
    """Checkpoint creation during delivery."""

    def test_creates_checkpoint_file(self, run_dir: Path) -> None:
        _do_checkpoint(run_dir, "delivery")
        cp_path = run_dir / "meta" / "checkpoints.jsonl"
        assert cp_path.exists()
        data = json.loads(cp_path.read_text(encoding="utf-8").strip())
        assert data["message"] == "delivery"
        assert "ts" in data

    def test_appends_checkpoint_event(self, run_dir: Path) -> None:
        _do_checkpoint(run_dir, "delivery")
        events_path = run_dir / "meta" / "events.jsonl"
        lines = [l for l in events_path.read_text(encoding="utf-8").strip().split("\n") if l]
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event"] == "checkpoint"


# --- _do_reflect ---


class TestDoReflect:
    """Reflection during delivery ceremony."""

    def test_returns_success_with_empty_events(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        result = _do_reflect(trw_dir, None)
        assert result["status"] == "success"
        assert result["events_analyzed"] == 0

    def test_analyzes_events_from_run(self, trw_project: Path, run_dir: Path) -> None:
        # Add some events
        events_path = run_dir / "meta" / "events.jsonl"
        events = [
            {"ts": "2026-02-11T12:00:00Z", "event": "phase_enter", "data": {"phase": "implement"}},
            {"ts": "2026-02-11T12:01:00Z", "event": "shard_complete", "data": {}},
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        result = _do_reflect(trw_dir, run_dir)
        assert result["status"] == "success"
        assert result["events_analyzed"] == 2


# --- _do_claude_md_sync ---


class TestDoClaudeMdSync:
    """CLAUDE.md sync during delivery ceremony."""

    def test_creates_or_updates_claude_md(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
        ):
            result = _do_claude_md_sync(trw_dir)
        assert result["status"] == "success"
        assert "learnings_promoted" in result


# --- _do_index_sync ---


class TestDoIndexSync:
    """INDEX.md/ROADMAP.md sync during delivery ceremony."""

    def test_syncs_index_and_roadmap(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-001.md").write_text(
            "---\nprd:\n  id: PRD-CORE-001\n  title: Test\n"
            "  status: done\n  priority: P0\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_index_sync()
        assert result["status"] == "success"
        assert (prds_dir.parent / "INDEX.md").exists()
        assert (prds_dir.parent / "ROADMAP.md").exists()


# --- _do_auto_progress ---


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
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "skipped"
        assert result["reason"] == "prds_dir_not_found"

    def test_progresses_implemented_to_done_on_deliver(self, tmp_path: Path) -> None:
        # Set up PRD
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-099.md").write_text(
            "---\nprd:\n  id: PRD-CORE-099\n  title: Test\n"
            "  status: implemented\n  priority: P1\n  category: CORE\n---\n"
            "# PRD-CORE-099\nSome content for density.\n",
            encoding="utf-8",
        )
        # Set up run with prd_scope
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\n"
            "prd_scope:\n  - PRD-CORE-099\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] >= 1
        # Verify file was updated
        content = (prds_dir / "PRD-CORE-099.md").read_text(encoding="utf-8")
        assert "status: done" in content

    def test_returns_zero_applied_for_terminal_statuses(self, tmp_path: Path) -> None:
        prds_dir = tmp_path / "docs" / "requirements-aare-f" / "prds"
        prds_dir.mkdir(parents=True)
        (prds_dir / "PRD-CORE-099.md").write_text(
            "---\nprd:\n  id: PRD-CORE-099\n  title: Test\n"
            "  status: done\n  priority: P1\n  category: CORE\n---\n",
            encoding="utf-8",
        )
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\n"
            "prd_scope:\n  - PRD-CORE-099\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] == 0
