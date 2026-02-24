"""Tests for PRD-CORE-019: Session ceremony composite tools.

Covers:
- trw_session_start: recall + status bundling, partial failure resilience
- trw_deliver: reflect + checkpoint + claude_md_sync + index_sync bundling
- _find_active_run helper
- _do_checkpoint, _do_reflect, _do_claude_md_sync, _do_index_sync internals
- _do_auto_progress: PRD auto-progression during delivery (GAP-PROC-001)
- Integration tests for partial failure resilience (Sprint 13, GAP-TEST-003)
"""

from __future__ import annotations

from typing import Any

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.state._paths import find_active_run
from trw_mcp.tools.ceremony import (
    _do_auto_progress,
    _do_checkpoint,
    _do_claude_md_sync,
    _do_index_sync,
    _do_reflect,
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


# --- find_active_run ---


class TestFindActiveRun:
    """Helper function for locating active runs."""

    def test_returns_none_when_no_task_root(self, tmp_path: Path) -> None:
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths._config") as mock_config:
                mock_config.task_root = "nonexistent"
                result = find_active_run()
        assert result is None

    def test_finds_run_directory(self, tmp_path: Path, run_dir: Path) -> None:
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths._config") as mock_config:
                mock_config.task_root = "docs"
                result = find_active_run()
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
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
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

    def test_no_telemetry_noise_learnings_from_success_patterns(
        self, trw_project: Path, run_dir: Path,
    ) -> None:
        """PRD-FIX-021: _do_reflect must not create 'Success: X (Nx)' learnings."""
        events_path = run_dir / "meta" / "events.jsonl"
        # Many success events of the same type -> triggers success_patterns
        success_events = [
            {"ts": f"2026-02-11T12:0{i}:00Z", "event": "shard_complete", "data": {}}
            for i in range(8)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in success_events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        before_count = len(list(entries_dir.glob("*.yaml")))

        _do_reflect(trw_dir, run_dir)

        after_entries = list(entries_dir.glob("*.yaml"))
        new_entries = after_entries[before_count:]
        for f in new_entries:
            from trw_mcp.state.persistence import FileStateReader
            data = FileStateReader().read_yaml(f)
            summary = str(data.get("summary", ""))
            assert not summary.startswith("Success:"), (
                f"Telemetry noise learning created: {summary}"
            )

    def test_no_telemetry_noise_learnings_from_repeated_ops(
        self, trw_project: Path, run_dir: Path,
    ) -> None:
        """PRD-FIX-021: _do_reflect must not create 'Repeated operation: X' learnings."""
        events_path = run_dir / "meta" / "events.jsonl"
        # Repeated same op -> triggers repeated_ops detection
        repeated_events = [
            {"ts": f"2026-02-11T12:0{i}:00Z", "event": "checkpoint", "data": {}}
            for i in range(6)
        ]
        events_path.write_text(
            "\n".join(json.dumps(e) for e in repeated_events) + "\n",
            encoding="utf-8",
        )
        trw_dir = trw_project / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        before_count = len(list(entries_dir.glob("*.yaml")))

        _do_reflect(trw_dir, run_dir)

        after_entries = list(entries_dir.glob("*.yaml"))
        new_entries = after_entries[before_count:]
        for f in new_entries:
            from trw_mcp.state.persistence import FileStateReader
            data = FileStateReader().read_yaml(f)
            summary = str(data.get("summary", ""))
            assert not summary.startswith("Repeated operation:"), (
                f"Telemetry noise learning created: {summary}"
            )


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

    def test_deliver_includes_ceremony_sections(self, trw_project: Path) -> None:
        """trw_deliver path produces CLAUDE.md with ceremony content."""
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
        ):
            result = _do_claude_md_sync(trw_dir)
        assert result["status"] == "success"

        claude_md = trw_project / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # Ceremony sections present
        assert "### Execution Phases" in content
        assert "### Tool Lifecycle" in content
        assert "### Example Flows" in content
        assert "`trw_session_start`" in content
        assert "`trw_deliver`" in content
        # Value-oriented opener present
        assert "TRW tools help you build effectively" in content
        # No unreplaced placeholders
        assert "{{imperative_opener}}" not in content
        assert "{{ceremony_phases}}" not in content
        assert "{{ceremony_table}}" not in content
        assert "{{ceremony_flows}}" not in content
        assert "{{closing_reminder}}" not in content


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


# --- Integration tests: partial failure resilience (GAP-TEST-003) ---


def _make_ceremony_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> dict[str, Any]:
    """Create a FastMCP server with ceremony tools and patched project root."""
    from fastmcp import FastMCP
    from trw_mcp.tools.ceremony import register_ceremony_tools

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))

    srv = FastMCP("test")
    register_ceremony_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


@pytest.mark.integration
class TestSessionStartPartialFailure:
    """trw_session_start resilience when sub-operations fail."""

    def test_returns_result_when_recall_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If recall raises, status step still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools.ceremony.resolve_trw_dir",
                side_effect=Exception("recall boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony.find_active_run",
                return_value=None,
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert len(result["errors"]) >= 1
        assert "recall" in result["errors"][0]
        # Run status should still be present
        assert "run" in result

    def test_returns_result_when_status_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If status check raises, recall still runs and result is returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony.find_active_run",
                side_effect=Exception("status boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is False
        assert any("status" in e for e in result["errors"])
        # Learnings should still be populated (even if empty)
        assert "learnings" in result

    def test_success_when_all_steps_work(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both recall and status succeed."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn()

        assert result["success"] is True
        assert result["errors"] == []
        assert "timestamp" in result


@pytest.mark.integration
class TestDeliverPartialFailure:
    """trw_deliver resilience when sub-operations fail."""

    def test_reflect_failure_does_not_block_checkpoint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If reflect raises, checkpoint still runs."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                side_effect=Exception("reflect boom"),
            ),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["reflect"]["status"] == "failed"
        # Checkpoint should have run
        assert result["checkpoint"]["status"] == "success"

    def test_checkpoint_failure_does_not_block_sync(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If checkpoint raises, claude_md_sync still runs."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0,
                              "learnings_produced": 0},
            ),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.tools.ceremony._do_checkpoint",
                side_effect=Exception("checkpoint boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["checkpoint"]["status"] == "failed"
        assert result["claude_md_sync"]["status"] == "success"

    def test_index_sync_failure_does_not_block_auto_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If index_sync raises, auto_progress still runs."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0,
                              "learnings_produced": 0},
            ),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                side_effect=Exception("index_sync boom"),
            ),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["index_sync"]["status"] == "failed"
        # auto_progress should still have run (skipped because no run)
        assert result["auto_progress"]["status"] == "skipped"

    def test_skip_reflect_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skip_reflect=True skips the reflect step entirely."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True)

        assert result["reflect"]["status"] == "skipped"
        assert result["success"] is True

    def test_skip_index_sync_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skip_index_sync=True skips index sync step."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0,
                              "learnings_produced": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_claude_md_sync",
                return_value={"status": "success", "learnings_promoted": 0,
                              "path": "", "total_lines": 0},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_index_sync=True)

        assert result["index_sync"]["status"] == "skipped"
        assert result["success"] is True

    def test_event_logging_during_delivery(
        self, tmp_path: Path,
    ) -> None:
        """Verify events are logged to events.jsonl during delivery sub-steps."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "reflections").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260214T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        # Run reflect + checkpoint directly
        _do_reflect(trw_dir, run_dir)
        _do_checkpoint(run_dir, "test-delivery")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [
            line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line
        ]
        assert len(lines) >= 2
        event_types = [json.loads(line)["event"] for line in lines]
        assert "reflection_complete" in event_types
        assert "checkpoint" in event_types


# --- trw_session_start update advisory wiring (PRD-INFRA-014) ---


@pytest.mark.integration
class TestSessionStartUpdateAdvisory:
    """Verify check_for_update() wiring in trw_session_start."""

    def test_update_advisory_included_when_update_available(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When check_for_update returns available=True, advisory is in results."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={
                    "available": True,
                    "current": "0.4.0",
                    "latest": "0.5.0",
                    "channel": "latest",
                    "advisory": "TRW v0.5.0 available (you have v0.4.0). ",
                },
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert "update_advisory" in result
        assert "0.5.0" in str(result["update_advisory"])

    def test_no_update_advisory_when_up_to_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When check_for_update returns available=False, advisory key is absent."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                return_value={
                    "available": False,
                    "current": "0.5.0",
                    "latest": "0.5.0",
                    "channel": "latest",
                    "advisory": None,
                },
            ),
        ):
            result = tools["trw_session_start"].fn()

        assert result.get("update_advisory") is None

    def test_update_check_failure_is_fail_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If check_for_update raises, session start still succeeds."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.state.auto_upgrade.check_for_update",
                side_effect=Exception("network boom"),
            ),
        ):
            result = tools["trw_session_start"].fn()

        # Update check failure must NOT appear in errors or block result
        assert "update_advisory" not in result or result.get("update_advisory") is None
        assert "timestamp" in result


# --- TestDeliverTelemetryIntegration ---


def _make_deliver_with_stubs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    run_dir: Path | None = None,
) -> dict[str, Any]:
    """Build a ceremony server and patch heavy sub-steps for deliver tests."""
    tools = _make_ceremony_server(monkeypatch, tmp_path)
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "reflections").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.find_active_run", lambda: run_dir)
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_reflect",
        lambda *_a, **_kw: {"status": "success", "events_analyzed": 0, "learnings_produced": 0},
    )
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_claude_md_sync",
        lambda *_a, **_kw: {"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
    )
    monkeypatch.setattr(
        "trw_mcp.tools.ceremony._do_index_sync",
        lambda *_a, **_kw: {"status": "success", "index": {}, "roadmap": {}},
    )
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_project_root", lambda: tmp_path)
    return tools


@pytest.mark.integration
class TestDeliverTelemetryIntegration:
    """Tests for Steps 6.5, 6.6, 7, 8 wired into trw_deliver (G1-G6)."""

    def test_deliver_calls_process_outcome_for_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 6.5: process_outcome_for_event is called and outcome_correlation is populated."""
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        # The function is imported locally inside trw_deliver: `from trw_mcp.scoring import process_outcome_for_event`
        # Patching trw_mcp.scoring.process_outcome_for_event intercepts the local import.
        called_with: list[str] = []

        def _fake_process(event_type: str, event_data: Any = None) -> list[str]:
            called_with.append(event_type)
            return ["L-test001"]

        with patch("trw_mcp.scoring.process_outcome_for_event", side_effect=_fake_process):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["outcome_correlation"]["status"] == "success"
        assert result["outcome_correlation"]["updated"] == 1
        assert "trw_deliver_complete" in called_with

    def test_deliver_emits_session_end_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 7: TelemetryClient.record_event called with SessionEndEvent."""
        from unittest.mock import MagicMock
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        mock_client = MagicMock()
        mock_client.record_event = MagicMock()
        mock_client.flush = MagicMock()

        with patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=mock_client):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["telemetry"]["status"] == "success"
        assert mock_client.record_event.call_count >= 2
        # Verify SessionEndEvent was among the calls
        from trw_mcp.telemetry.models import SessionEndEvent
        call_args_list = mock_client.record_event.call_args_list
        event_types = [type(c.args[0]).__name__ for c in call_args_list if c.args]
        assert "SessionEndEvent" in event_types

    def test_deliver_emits_ceremony_compliance_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 7: TelemetryClient.record_event called with CeremonyComplianceEvent."""
        from unittest.mock import MagicMock
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        mock_client = MagicMock()
        mock_client.record_event = MagicMock()
        mock_client.flush = MagicMock()

        with patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=mock_client):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["telemetry"]["status"] == "success"
        from trw_mcp.telemetry.models import CeremonyComplianceEvent
        call_args_list = mock_client.record_event.call_args_list
        event_types = [type(c.args[0]).__name__ for c in call_args_list if c.args]
        assert "CeremonyComplianceEvent" in event_types

    def test_deliver_calls_batch_sender(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 8: BatchSender.from_config().send() is called."""
        from unittest.mock import MagicMock
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        mock_sender = MagicMock()
        mock_sender.send = MagicMock(return_value={"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "offline_mode"})

        with patch("trw_mcp.telemetry.sender.BatchSender.from_config", return_value=mock_sender):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert "batch_send" in result
        mock_sender.send.assert_called_once()

    def test_deliver_calls_record_outcome(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 6.6: record_outcome is called for tracked recalls with positive outcome."""
        from unittest.mock import MagicMock
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        # Set up a recall_tracking.jsonl with an unresolved record
        trw_dir = tmp_path / ".trw"
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        import json as _json
        tracking_path = logs_dir / "recall_tracking.jsonl"
        tracking_path.write_text(
            _json.dumps({"learning_id": "L-test001", "ts": "2026-02-22T00:00:00Z", "outcome": None}) + "\n",
            encoding="utf-8",
        )

        # Also need a run_dir for the tracking path check
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260222T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        recorded: list[tuple[str, str]] = []

        def _fake_record_outcome(lid: str, outcome: str) -> None:
            recorded.append((lid, outcome))

        with (
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.recall_tracking.record_outcome", side_effect=_fake_record_outcome),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["recall_outcome"]["status"] == "success"
        assert result["recall_outcome"]["recorded"] >= 1
        assert ("L-test001", "positive") in recorded

    def test_deliver_outcome_correlation_failopen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 6.5: process_outcome_for_event raising does not block deliver."""
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        with patch(
            "trw_mcp.scoring.process_outcome_for_event",
            side_effect=RuntimeError("correlation boom"),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        # outcome_correlation failed but deliver still returns a result
        assert result["outcome_correlation"]["status"] == "failed"
        assert "correlation boom" in result["outcome_correlation"]["error"]
        # Other steps should still complete
        assert "reflect" in result

    def test_deliver_telemetry_failopen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 7: TelemetryClient.from_config raising does not block deliver."""
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        with patch(
            "trw_mcp.telemetry.client.TelemetryClient.from_config",
            side_effect=RuntimeError("telemetry boom"),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["telemetry"]["status"] == "failed"
        assert "telemetry boom" in result["telemetry"]["error"]
        # batch_send should still run
        assert "batch_send" in result

    def test_deliver_batch_send_failopen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 8: BatchSender.from_config raising does not block deliver."""
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        with patch(
            "trw_mcp.telemetry.sender.BatchSender.from_config",
            side_effect=RuntimeError("batch boom"),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        assert result["batch_send"]["status"] == "failed"
        assert "batch boom" in result["batch_send"]["error"]
        # Result still returned (not raised)
        assert "timestamp" in result

    def test_deliver_steps_completed_count_is_9(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When all 9 steps succeed, steps_completed == 9."""
        from unittest.mock import MagicMock
        tools = _make_deliver_with_stubs(monkeypatch, tmp_path)

        mock_client = MagicMock()
        mock_client.record_event = MagicMock()
        mock_client.flush = MagicMock()

        mock_sender = MagicMock()
        mock_sender.send = MagicMock(return_value={"sent": 0, "failed": 0, "remaining": 0, "skipped_reason": "offline_mode"})

        with (
            patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=mock_client),
            patch("trw_mcp.telemetry.sender.BatchSender.from_config", return_value=mock_sender),
            # Stub publish_learnings to succeed
            patch("trw_mcp.telemetry.publisher.publish_learnings", return_value={"status": "success"}),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        # With skip_reflect + skip_index_sync, reflect/index are skipped (not errored)
        # Steps that succeed contribute to steps_completed = 9 - len(errors)
        assert result["steps_completed"] == 9 - len(result["errors"])
