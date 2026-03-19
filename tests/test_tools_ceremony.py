"""Tests for PRD-CORE-019: Session ceremony composite tools.

Covers:
- trw_session_start: recall + status bundling, partial failure resilience
- trw_deliver: reflect + checkpoint + claude_md_sync + index_sync bundling
- _find_active_run helper
- _do_checkpoint, _do_reflect, _do_instruction_sync, _do_index_sync internals
- _do_auto_progress: PRD auto-progression during delivery (GAP-PROC-001)
- Integration tests for partial failure resilience (Sprint 13, GAP-TEST-003)
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from trw_mcp.state._paths import find_active_run
from trw_mcp.tools._deferred_delivery import (
    _do_auto_progress,
    _do_index_sync,
    _launch_deferred,
    _log_deferred_result,
    _release_deferred_lock,
    _run_deferred_steps,
    _try_acquire_deferred_lock,
)
from trw_mcp.tools.ceremony import (
    _do_instruction_sync,
    _do_reflect,
    _get_run_status,
)
from trw_mcp.tools.checkpoint import _do_checkpoint

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
        "total_entries: 1\n",
        encoding="utf-8",
    )

    return tmp_path


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Create a minimal run directory structure."""
    d = tmp_path / ".trw" / "runs" / "task" / "20260211T120000Z-test"
    meta = d / "meta"
    meta.mkdir(parents=True)
    (meta / "run.yaml").write_text(
        "run_id: test-run\nstatus: active\nphase: implement\ntask: test-task\n",
        encoding="utf-8",
    )
    # Create empty events.jsonl
    (meta / "events.jsonl").write_text("", encoding="utf-8")
    return d


# --- find_active_run ---


class TestFindActiveRun:
    """Helper function for locating active runs."""

    def test_returns_none_when_no_runs_root(self, tmp_path: Path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", "nonexistent")
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
                result = find_active_run()
        assert result is None

    def test_finds_run_directory(self, tmp_path: Path, run_dir: Path) -> None:
        from trw_mcp.models.config import TRWConfig

        cfg = TRWConfig()
        object.__setattr__(cfg, "runs_root", ".trw/runs")
        with patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path):
            with patch("trw_mcp.state._paths.get_config", return_value=cfg):
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
        self,
        trw_project: Path,
        run_dir: Path,
    ) -> None:
        """PRD-FIX-021: _do_reflect must not create 'Success: X (Nx)' learnings."""
        events_path = run_dir / "meta" / "events.jsonl"
        # Many success events of the same type -> triggers success_patterns
        success_events = [{"ts": f"2026-02-11T12:0{i}:00Z", "event": "shard_complete", "data": {}} for i in range(8)]
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
            assert not summary.startswith("Success:"), f"Telemetry noise learning created: {summary}"

    def test_no_telemetry_noise_learnings_from_repeated_ops(
        self,
        trw_project: Path,
        run_dir: Path,
    ) -> None:
        """PRD-FIX-021: _do_reflect must not create 'Repeated operation: X' learnings."""
        events_path = run_dir / "meta" / "events.jsonl"
        # Repeated same op -> triggers repeated_ops detection
        repeated_events = [{"ts": f"2026-02-11T12:0{i}:00Z", "event": "checkpoint", "data": {}} for i in range(6)]
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
            assert not summary.startswith("Repeated operation:"), f"Telemetry noise learning created: {summary}"


# --- _do_instruction_sync ---


class TestDoClaudeMdSync:
    """CLAUDE.md sync during delivery ceremony."""

    def test_creates_or_updates_claude_md(self, trw_project: Path) -> None:
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
        ):
            result = _do_instruction_sync(trw_dir)
        assert result["status"] == "success"
        assert "learnings_promoted" in result

    def test_deliver_includes_ceremony_sections(self, trw_project: Path) -> None:
        """trw_deliver path produces CLAUDE.md via canonical execute_claude_md_sync.

        PRD-CORE-061: Progressive disclosure suppresses full ceremony sections
        from CLAUDE.md — they are now delivered via /trw-ceremony-guide skill.
        The quick-reference card replaces the full ceremony content.
        """
        trw_dir = trw_project / ".trw"
        with (
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=trw_project),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.claude_md.resolve_project_root", return_value=trw_project),
        ):
            result = _do_instruction_sync(trw_dir)
        assert result["status"] == "success"

        claude_md = trw_project / "CLAUDE.md"
        content = claude_md.read_text(encoding="utf-8")
        # Quick-reference card present (progressive disclosure replacement)
        assert "## TRW Behavioral Protocol (Auto-Generated)" in content
        assert "`trw_session_start()`" in content
        assert "`trw_deliver()`" in content
        assert "`trw_checkpoint(message)`" in content
        assert "`trw_learn(summary, detail)`" in content
        # Value-oriented opener present
        assert "TRW tools help you build effectively" in content
        # Progressive disclosure pointer present
        assert "/trw-ceremony-guide" in content
        # No unreplaced placeholders
        assert "{{imperative_opener}}" not in content
        assert "{{ceremony_quick_ref}}" not in content
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
            "---\nprd:\n  id: PRD-CORE-001\n  title: Test\n  status: done\n  priority: P0\n  category: CORE\n---\n",
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
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope:\n  - PRD-CORE-099\n",
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
        with patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path):
            result = _do_auto_progress(run_dir)
        assert result["status"] == "success"
        assert result["applied"] == 0


# --- Integration tests: partial failure resilience (GAP-TEST-003) ---


from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


@pytest.mark.integration
class TestSessionStartPartialFailure:
    """trw_session_start resilience when sub-operations fail."""

    def test_returns_result_when_recall_fails(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
                return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0},
            ),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch(
                "trw_mcp.tools.ceremony._step_checkpoint",
                side_effect=Exception("checkpoint boom"),
            ),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
            patch("trw_mcp.tools.ceremony.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["checkpoint"]["status"] == "failed"
        assert result["claude_md_sync"]["status"] == "success"

    def test_index_sync_failure_does_not_block_auto_progress(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If index_sync raises in deferred path, auto_progress still runs.

        Since index_sync and auto_progress are now deferred steps, we test
        ``_run_deferred_steps`` directly for fail-open behavior.
        """
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                side_effect=Exception("index_sync boom"),
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_auto_prune",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_consolidation",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_tier_sweep",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_auto_progress",
                return_value={"status": "skipped", "reason": "no_run"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_publish_learnings",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_outcome_correlation",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_recall_outcome",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_telemetry",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_batch_send",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_trust_increment",
                return_value={"status": "skipped"},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._step_ceremony_feedback",
                return_value={"status": "skipped"},
            ),
        ):
            _run_deferred_steps(trw_dir, None, {})

        # Read the deferred log to check results
        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        log_entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert log_entry["results"]["index_sync"]["status"] == "failed"
        assert "index_sync boom" in log_entry["results"]["index_sync"]["error"]
        # auto_progress should still have run (skipped because no run)
        assert log_entry["results"]["auto_progress"]["status"] == "skipped"

    def test_skip_reflect_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                return_value={"status": "success", "index": {}, "roadmap": {}},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True)

        assert result["reflect"]["status"] == "skipped"
        assert result["success"] is True

    def test_skip_index_sync_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skip_index_sync=True skips index sync in deferred path.

        Since index_sync is now a deferred step, we test
        ``_run_deferred_steps`` directly with ``skip_index_sync=True``.
        """
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        # Stub all deferred steps to no-ops
        _noop = {"status": "skipped"}
        with (
            patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=_noop),
            patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=_noop),
        ):
            _run_deferred_steps(trw_dir, None, {}, skip_index_sync=True)

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        log_entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert log_entry["results"]["index_sync"]["status"] == "skipped"
        assert log_entry["success"] is True

    def test_event_logging_during_delivery(
        self,
        tmp_path: Path,
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
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 2
        event_types = [json.loads(line)["event"] for line in lines]
        assert "reflection_complete" in event_types
        assert "checkpoint" in event_types


# --- trw_session_start update advisory wiring (PRD-INFRA-014) ---


@pytest.mark.integration
class TestSessionStartUpdateAdvisory:
    """Verify check_for_update() wiring in trw_session_start."""

    def test_update_advisory_included_when_update_available(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
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
#
# With the deferred delivery architecture, telemetry / outcome / batch steps
# run in ``_run_deferred_steps`` on a background thread.  These tests call
# ``_run_deferred_steps`` directly (synchronously) so they are deterministic
# and do not depend on thread timing.


@contextlib.contextmanager
def _apply_stubs(stubs: dict[str, Any]) -> Generator[None, None, None]:
    """Enter all ``patch`` context managers in *stubs* as a single block."""
    with contextlib.ExitStack() as stack:
        for p in stubs.values():
            stack.enter_context(p)
        yield


def _make_deferred_trw_dir(tmp_path: Path) -> Path:
    """Create the minimal .trw structure needed by ``_run_deferred_steps``."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "logs").mkdir(parents=True, exist_ok=True)
    return trw_dir


def _stub_all_deferred_steps() -> dict[str, Any]:
    """Return a dict of ``patch`` context managers that stub every deferred step.

    Returns a dict keyed by step name so callers can override specific steps.
    """
    noop: dict[str, object] = {"status": "skipped"}
    return {
        "_step_auto_prune": patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=noop),
        "_step_consolidation": patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=noop),
        "_step_tier_sweep": patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
        "_do_index_sync": patch("trw_mcp.tools._deferred_delivery._do_index_sync", return_value=noop),
        "_step_auto_progress": patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
        "_step_publish_learnings": patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
        "_step_outcome_correlation": patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
        "_step_recall_outcome": patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
        "_step_telemetry": patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
        "_step_batch_send": patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
        "_step_trust_increment": patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
        "_step_ceremony_feedback": patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
    }


def _read_deferred_log(trw_dir: Path) -> dict[str, Any]:
    """Read the single deferred-deliver.jsonl entry written by ``_run_deferred_steps``."""
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    assert log_path.exists(), "deferred-deliver.jsonl was not written"
    lines = [ln for ln in log_path.read_text(encoding="utf-8").strip().splitlines() if ln]
    return json.loads(lines[-1])  # type: ignore[no-any-return]


@pytest.mark.integration
class TestDeliverTelemetryIntegration:
    """Tests for deferred steps (outcome correlation, telemetry, batch_send, etc.).

    These previously tested results in the synchronous ``trw_deliver`` return
    dict.  Now that these steps run via ``_run_deferred_steps``, we invoke that
    function directly and inspect the deferred-deliver.jsonl audit log.
    """

    def test_deliver_calls_process_outcome_for_event(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 6.5: process_outcome_for_event is called via deferred path."""
        trw_dir = _make_deferred_trw_dir(tmp_path)
        called_with: list[str] = []

        def _fake_process(event_type: str, event_data: Any = None) -> list[str]:
            called_with.append(event_type)
            return ["L-test001"]

        stubs = _stub_all_deferred_steps()
        # Override outcome_correlation to use the real step with our mock
        del stubs["_step_outcome_correlation"]

        with patch("trw_mcp.scoring.process_outcome_for_event", side_effect=_fake_process):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["outcome_correlation"]["status"] == "success"
        assert log_entry["results"]["outcome_correlation"]["updated"] == 1
        assert "trw_deliver_complete" in called_with

    def test_deliver_emits_session_end_event(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 7: TelemetryClient.record_event called with SessionEndEvent."""
        from unittest.mock import MagicMock

        trw_dir = _make_deferred_trw_dir(tmp_path)

        mock_client = MagicMock()
        mock_client.record_event = MagicMock()
        mock_client.flush = MagicMock()

        stubs = _stub_all_deferred_steps()
        del stubs["_step_telemetry"]

        with patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=mock_client):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["telemetry"]["status"] == "success"
        assert mock_client.record_event.call_count >= 2
        call_args_list = mock_client.record_event.call_args_list
        event_types = [type(c.args[0]).__name__ for c in call_args_list if c.args]
        assert "SessionEndEvent" in event_types

    def test_deliver_emits_ceremony_compliance_event(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 7: TelemetryClient.record_event called with CeremonyComplianceEvent."""
        from unittest.mock import MagicMock

        trw_dir = _make_deferred_trw_dir(tmp_path)

        mock_client = MagicMock()
        mock_client.record_event = MagicMock()
        mock_client.flush = MagicMock()

        stubs = _stub_all_deferred_steps()
        del stubs["_step_telemetry"]

        with patch("trw_mcp.telemetry.client.TelemetryClient.from_config", return_value=mock_client):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["telemetry"]["status"] == "success"
        call_args_list = mock_client.record_event.call_args_list
        event_types = [type(c.args[0]).__name__ for c in call_args_list if c.args]
        assert "CeremonyComplianceEvent" in event_types

    def test_deliver_calls_batch_sender(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 8: BatchSender.from_config().send() is called."""
        from unittest.mock import MagicMock

        trw_dir = _make_deferred_trw_dir(tmp_path)

        mock_sender = MagicMock()
        mock_sender.send = MagicMock(
            return_value={
                "sent": 0,
                "failed": 0,
                "remaining": 0,
                "skipped_reason": "offline_mode",
            }
        )

        stubs = _stub_all_deferred_steps()
        del stubs["_step_batch_send"]

        with patch("trw_mcp.telemetry.sender.BatchSender.from_config", return_value=mock_sender):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert "batch_send" in log_entry["results"]
        mock_sender.send.assert_called_once()

    def test_deliver_calls_record_outcome(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 6.6: record_outcome is called for tracked recalls with positive outcome."""
        trw_dir = _make_deferred_trw_dir(tmp_path)

        # Set up a recall_tracking.jsonl with an unresolved record
        tracking_path = trw_dir / "logs" / "recall_tracking.jsonl"
        tracking_path.write_text(
            json.dumps({"learning_id": "L-test001", "ts": "2026-02-22T00:00:00Z", "outcome": None}) + "\n",
            encoding="utf-8",
        )

        # Create a run_dir for the tracking path check
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

        stubs = _stub_all_deferred_steps()
        del stubs["_step_recall_outcome"]

        with (
            patch("trw_mcp.state.recall_tracking.record_outcome", side_effect=_fake_record_outcome),
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.state.recall_tracking.get_recall_stats", return_value={"unique_learnings": 1}),
        ):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, run_dir, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["recall_outcome"]["status"] == "success"
        assert log_entry["results"]["recall_outcome"]["recorded"] >= 1
        assert ("L-test001", "positive") in recorded

    def test_deliver_outcome_correlation_failopen(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 6.5: process_outcome_for_event raising does not block other deferred steps."""
        trw_dir = _make_deferred_trw_dir(tmp_path)

        stubs = _stub_all_deferred_steps()
        del stubs["_step_outcome_correlation"]

        with patch(
            "trw_mcp.scoring.process_outcome_for_event",
            side_effect=RuntimeError("correlation boom"),
        ):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["outcome_correlation"]["status"] == "failed"
        assert "correlation boom" in log_entry["results"]["outcome_correlation"]["error"]
        # Other deferred steps should still have run
        assert "batch_send" in log_entry["results"]

    def test_deliver_telemetry_failopen(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 7: TelemetryClient.from_config raising does not block other deferred steps."""
        trw_dir = _make_deferred_trw_dir(tmp_path)

        stubs = _stub_all_deferred_steps()
        del stubs["_step_telemetry"]

        with patch(
            "trw_mcp.telemetry.client.TelemetryClient.from_config",
            side_effect=RuntimeError("telemetry boom"),
        ):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["telemetry"]["status"] == "failed"
        assert "telemetry boom" in log_entry["results"]["telemetry"]["error"]
        # batch_send should still run
        assert "batch_send" in log_entry["results"]

    def test_deliver_batch_send_failopen(
        self,
        tmp_path: Path,
    ) -> None:
        """Step 8: BatchSender.from_config raising does not block other deferred steps."""
        trw_dir = _make_deferred_trw_dir(tmp_path)

        stubs = _stub_all_deferred_steps()
        del stubs["_step_batch_send"]

        with patch(
            "trw_mcp.telemetry.sender.BatchSender.from_config",
            side_effect=RuntimeError("batch boom"),
        ):
            with _apply_stubs(stubs):
                _run_deferred_steps(trw_dir, None, {})

        log_entry = _read_deferred_log(trw_dir)
        assert log_entry["results"]["batch_send"]["status"] == "failed"
        assert "batch boom" in log_entry["results"]["batch_send"]["error"]
        # Other steps still ran
        assert log_entry["results"]["telemetry"]["status"] == "skipped"

    def test_step_tier_sweep_includes_impact_tier_distribution(
        self,
        tmp_path: Path,
    ) -> None:
        """PRD-FIX-052-FR07: _step_tier_sweep result includes impact_tier_distribution dict."""
        from unittest.mock import MagicMock

        from trw_mcp.tools._deferred_delivery import _step_tier_sweep

        trw_dir = _make_deferred_trw_dir(tmp_path)
        fake_sweep_result = MagicMock()
        fake_sweep_result.promoted = 0
        fake_sweep_result.demoted = 1
        fake_sweep_result.purged = 0
        fake_sweep_result.errors = 0

        fake_distribution: dict[str, int] = {
            "critical": 2,
            "high": 5,
            "medium": 10,
            "low": 3,
        }

        with (
            patch(
                "trw_mcp.state.tiers.TierManager.sweep",
                return_value=fake_sweep_result,
            ),
            patch(
                "trw_mcp.state.tiers.TierManager.assign_impact_tiers",
                return_value=fake_distribution,
            ),
        ):
            result = _step_tier_sweep(trw_dir)

        assert result["status"] == "success"
        assert "impact_tier_distribution" in result, "FR07: distribution must be in result"
        dist = result["impact_tier_distribution"]
        assert isinstance(dist, dict)
        assert set(dist.keys()) == {"critical", "high", "medium", "low"}
        assert dist["critical"] == 2
        assert dist["high"] == 5
        assert dist["medium"] == 10
        assert dist["low"] == 3

    def test_deliver_critical_steps_completed_count(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Critical path reports 3 steps; deferred_steps reports 11."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
        (trw_dir / "context").mkdir(parents=True, exist_ok=True)

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch(
                "trw_mcp.tools.ceremony._do_reflect",
                return_value={"status": "success", "events_analyzed": 0, "learnings_produced": 0},
            ),
            patch(
                "trw_mcp.tools.ceremony._do_instruction_sync",
                return_value={"status": "success", "learnings_promoted": 0, "path": "", "total_lines": 0},
            ),
        ):
            result = tools["trw_deliver"].fn(skip_reflect=True, skip_index_sync=True)

        # Critical path: reflect (skipped) + checkpoint (skipped) + claude_md_sync = 3
        assert result["critical_steps_completed"] == 3
        assert result["deferred_steps"] == 11
        assert result["deferred"] == "launched"
        assert result["success"] is True


# --- TestSessionStartWithQuery: query parameter for focused hybrid recall ---


@pytest.mark.integration
class TestSessionStartWithQuery:
    """trw_session_start(query=...) focused hybrid recall tests."""

    def test_query_empty_is_default_behavior(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty string query uses default wildcard — no 'query' key in result."""
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
        assert "query" not in result

    def test_query_triggers_focused_recall(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-empty query makes 2 adapter_recall calls, returns 'query' key."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        call_log: list[dict[str, Any]] = []

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
        ) -> list[dict[str, object]]:
            call_log.append({"query": query, "min_impact": min_impact})
            if query == "*":
                return [{"id": "L-base001", "summary": "Baseline", "impact": 0.8}]
            return [{"id": "L-focus001", "summary": "Focused", "impact": 0.4}]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            result = tools["trw_session_start"].fn(query="authentication JWT")

        assert result["query"] == "authentication JWT"
        assert int(str(result["query_matched"])) >= 1
        # Should have made 2 recall calls (focused + baseline)
        assert len(call_log) >= 2
        assert any(c["query"] == "authentication JWT" for c in call_log)
        assert any(c["query"] == "*" for c in call_log)

    def test_query_deduplicates_across_recalls(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same ID from both recalls appears only once in merged results."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        shared_entry: dict[str, object] = {"id": "L-shared", "summary": "Shared", "impact": 0.8}
        focused_only: dict[str, object] = {"id": "L-focus", "summary": "Focused", "impact": 0.4}
        baseline_only: dict[str, object] = {"id": "L-base", "summary": "Baseline", "impact": 0.9}

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
        ) -> list[dict[str, object]]:
            if query == "*":
                return [shared_entry, baseline_only]
            return [focused_only, shared_entry]

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            result = tools["trw_session_start"].fn(query="auth")

        learnings = result["learnings"]
        assert isinstance(learnings, list)
        ids = [str(e.get("id", "")) for e in learnings]
        # L-shared should appear only once despite being in both recalls
        assert ids.count("L-shared") == 1
        # Focused results come first
        assert ids.index("L-focus") < ids.index("L-base")

    def test_query_recall_failure_falls_back(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Exception in recall is handled gracefully — result still returned."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools.ceremony.resolve_trw_dir",
                side_effect=Exception("recall boom"),
            ),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=None),
        ):
            result = tools["trw_session_start"].fn(query="auth")

        assert result["success"] is False
        assert any("recall" in e for e in result["errors"])
        assert result["learnings"] == []
        # Run status still present
        assert "run" in result

    def test_query_merged_into_auto_recall(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 6 ar_query includes user query tokens + phase context."""
        tools = _make_ceremony_server(monkeypatch, tmp_path)
        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        (trw_dir / "context").mkdir(parents=True)

        # Track what query Step 6 auto-recall receives
        step6_queries: list[str] = []

        def _fake_recall(
            _trw_d: Any,
            *,
            query: str = "*",
            min_impact: float = 0.0,
            max_results: int = 25,
            compact: bool = False,
            tags: Any = None,
            status: Any = None,
        ) -> list[dict[str, object]]:
            # Step 1 calls use compact=True, Step 6 uses compact=False
            if not compact:
                step6_queries.append(query)
            return []

        # Create a run dir so phase context is available
        run_dir = tmp_path / "docs" / "task" / "runs" / "20260228T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: implement\ntask_name: auth-feature\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        with (
            patch("trw_mcp.tools.ceremony.resolve_trw_dir", return_value=trw_dir),
            patch("trw_mcp.tools.ceremony.find_active_run", return_value=run_dir),
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_fake_recall),
        ):
            # Enable auto-recall for this test
            monkeypatch.setattr("trw_mcp.tools.ceremony._config.auto_recall_enabled", True)
            _result = tools["trw_session_start"].fn(query="JWT validation")

        # Step 6 should have been called with user tokens merged in
        assert len(step6_queries) >= 1
        ar_query = step6_queries[0]
        # User query tokens should appear in auto-recall query
        assert "JWT" in ar_query or "validation" in ar_query


# --- Deferred delivery infrastructure tests ---


class TestDeferredLock:
    """Non-blocking file lock prevents concurrent deferred batches."""

    def test_acquire_and_release(self, tmp_path: Path) -> None:
        """Lock can be acquired and released cleanly."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd = _try_acquire_deferred_lock(trw_dir)
        assert fd is not None
        _release_deferred_lock(fd)

    def test_second_acquire_fails_while_held(self, tmp_path: Path) -> None:
        """Second acquire returns None while first lock is held."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd1 = _try_acquire_deferred_lock(trw_dir)
        assert fd1 is not None
        try:
            fd2 = _try_acquire_deferred_lock(trw_dir)
            assert fd2 is None, "Should not acquire lock while held"
        finally:
            _release_deferred_lock(fd1)

    def test_reacquire_after_release(self, tmp_path: Path) -> None:
        """Lock can be re-acquired after release."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd1 = _try_acquire_deferred_lock(trw_dir)
        assert fd1 is not None
        _release_deferred_lock(fd1)

        fd2 = _try_acquire_deferred_lock(trw_dir)
        assert fd2 is not None
        _release_deferred_lock(fd2)


class TestDeferredLogResult:
    """Deferred results are logged to an audit file."""

    def test_writes_jsonl_entry(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        results = {"consolidation": {"status": "success"}}
        errors: list[str] = []
        _log_deferred_result(trw_dir, results, errors)

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["success"] is True
        assert "consolidation" in entry["results"]

    def test_logs_errors_gracefully(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        _log_deferred_result(trw_dir, {}, ["consolidation: boom"])

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        entry = json.loads(log_path.read_text().strip())
        assert entry["success"] is False
        assert "consolidation: boom" in entry["errors"]


class TestRunDeferredSteps:
    """Deferred steps execute with fail-open semantics and file locking."""

    def test_skips_when_lock_held(self, tmp_path: Path) -> None:
        """If lock is already held, deferred steps skip entirely."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        fd = _try_acquire_deferred_lock(trw_dir)
        assert fd is not None
        try:
            # Should skip — lock is held
            _run_deferred_steps(trw_dir, None, {})
            # No log should be written since it skipped
            log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
            assert not log_path.exists()
        finally:
            _release_deferred_lock(fd)

    def test_all_steps_fail_open(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each deferred step can fail without blocking others."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        # Patch all step functions to raise
        step_names = [
            "_step_auto_prune",
            "_step_consolidation",
            "_step_tier_sweep",
            "_do_index_sync",
            "_step_auto_progress",
            "_step_publish_learnings",
            "_step_outcome_correlation",
            "_step_recall_outcome",
            "_step_telemetry",
            "_step_batch_send",
            "_step_trust_increment",
            "_step_ceremony_feedback",
        ]
        for name in step_names:
            monkeypatch.setattr(
                f"trw_mcp.tools._deferred_delivery.{name}",
                lambda *a, name=name, **kw: (_ for _ in ()).throw(Exception(f"{name} boom")),
            )

        _run_deferred_steps(trw_dir, None, {})

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["success"] is False
        assert len(entry["errors"]) > 0


class TestLaunchDeferred:
    """Background thread launcher with deduplication."""

    def test_returns_launched(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Launching deferred steps returns 'launched'."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        # Patch all deferred steps to no-ops
        step_names = [
            "_step_auto_prune",
            "_step_consolidation",
            "_step_tier_sweep",
            "_do_index_sync",
            "_step_auto_progress",
            "_step_publish_learnings",
            "_step_outcome_correlation",
            "_step_recall_outcome",
            "_step_telemetry",
            "_step_batch_send",
            "_step_trust_increment",
            "_step_ceremony_feedback",
        ]
        for name in step_names:
            monkeypatch.setattr(
                f"trw_mcp.tools._deferred_delivery.{name}",
                lambda *a, **kw: {"status": "mocked"},
            )

        import trw_mcp.tools.ceremony as cer

        # Reset global thread state
        monkeypatch.setattr(cer, "_deferred_thread", None)

        status = _launch_deferred(trw_dir, None, {})
        assert status == "launched"

        # Wait for thread to complete
        with cer._deferred_lock:
            if cer._deferred_thread is not None:
                cer._deferred_thread.join(timeout=10)

    def test_skips_when_thread_alive(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Second launch returns 'skipped_already_running' while first is active."""
        import threading

        import trw_mcp.tools.ceremony as cer

        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        # Create a fake long-running thread
        barrier = threading.Event()

        def slow_worker() -> None:
            barrier.wait(timeout=10)

        fake_thread = threading.Thread(target=slow_worker, daemon=True)
        fake_thread.start()
        monkeypatch.setattr(cer, "_deferred_thread", fake_thread)

        try:
            status = _launch_deferred(trw_dir, None, {})
            assert status == "skipped_already_running"
        finally:
            barrier.set()
            fake_thread.join(timeout=5)
