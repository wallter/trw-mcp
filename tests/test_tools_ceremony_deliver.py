"""Integration tests for trw_deliver ceremony flows."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from trw_mcp.tools._deferred_delivery import _run_deferred_steps
from trw_mcp.tools.ceremony import _do_reflect
from trw_mcp.tools.checkpoint import _do_checkpoint

from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server


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
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["reflect"]["status"] == "failed"
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
            patch("trw_mcp.state._paths.resolve_project_root", return_value=tmp_path),
        ):
            result = tools["trw_deliver"].fn()

        assert result["success"] is False
        assert result["checkpoint"]["status"] == "failed"
        assert result["claude_md_sync"]["status"] == "skipped"

    def test_index_sync_failure_does_not_block_auto_progress(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If index_sync raises in deferred path, auto_progress still runs."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        with (
            patch(
                "trw_mcp.tools._deferred_delivery._do_index_sync",
                side_effect=Exception("index_sync boom"),
            ),
            patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value={"status": "skipped"}),
            patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value={"status": "skipped"}),
            patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value={"status": "skipped"}),
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
            patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value={"status": "skipped"}),
            patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value={"status": "skipped"}),
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

        log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
        assert log_path.exists()
        log_entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert log_entry["results"]["index_sync"]["status"] == "failed"
        assert "index_sync boom" in log_entry["results"]["index_sync"]["error"]
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

    def test_trw_deliver_marks_deliver_called_without_active_run(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression (L-ePWz): trw_deliver must flip deliver_called=True without active run."""
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

        assert result["checkpoint"]["status"] == "skipped"
        assert result["checkpoint"]["reason"] == "no_active_run"

        state_path = trw_dir / "context" / "ceremony-state.json"
        assert state_path.exists(), "ceremony-state.json must exist after trw_deliver"
        state_data = json.loads(state_path.read_text(encoding="utf-8"))
        assert state_data.get("deliver_called") is True, (
            "deliver_called must be True after trw_deliver even without active run"
        )

    def test_skip_index_sync_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skip_index_sync=True skips index sync in deferred path."""
        trw_dir = tmp_path / ".trw"
        (trw_dir / "logs").mkdir(parents=True)

        noop = {"status": "skipped"}
        with (
            patch("trw_mcp.tools._deferred_delivery._step_auto_prune", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_consolidation", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_tier_sweep", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_auto_progress", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_publish_learnings", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_outcome_correlation", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_recall_outcome", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_telemetry", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_batch_send", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_trust_increment", return_value=noop),
            patch("trw_mcp.tools._deferred_delivery._step_ceremony_feedback", return_value=noop),
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

        _do_reflect(trw_dir, run_dir)
        _do_checkpoint(run_dir, "test-delivery")

        events_path = run_dir / "meta" / "events.jsonl"
        lines = [line for line in events_path.read_text(encoding="utf-8").strip().split("\n") if line]
        assert len(lines) >= 2
        event_types = [json.loads(line)["event"] for line in lines]
        assert "reflection_complete" in event_types
        assert "checkpoint" in event_types
