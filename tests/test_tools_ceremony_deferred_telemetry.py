"""Integration tests for deferred delivery telemetry and outcome steps."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests._tools_ceremony_support import (
    _apply_stubs,
    _make_deferred_trw_dir,
    _read_deferred_log,
    _stub_all_deferred_steps,
)
from trw_mcp.tools._deferred_delivery import _run_deferred_steps


@pytest.mark.integration
class TestDeliverTelemetryIntegration:
    """Tests for deferred steps (outcome correlation, telemetry, batch_send, etc.)."""

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
        event_types = [type(call.args[0]).__name__ for call in call_args_list if call.args]
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
        event_types = [type(call.args[0]).__name__ for call in call_args_list if call.args]
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
        tracking_path = trw_dir / "logs" / "recall_tracking.jsonl"
        tracking_path.write_text(
            '{"learning_id": "L-test001", "ts": "2026-02-22T00:00:00Z", "outcome": null}\n',
            encoding="utf-8",
        )

        run_dir = tmp_path / "docs" / "task" / "runs" / "20260222T000000Z-test"
        (run_dir / "meta").mkdir(parents=True)
        (run_dir / "meta" / "run.yaml").write_text(
            "run_id: test\nstatus: active\nphase: deliver\nprd_scope: []\n",
            encoding="utf-8",
        )
        (run_dir / "meta" / "events.jsonl").write_text("", encoding="utf-8")

        recorded: list[tuple[str, str]] = []

        def _fake_record_outcome(learning_id: str, outcome: str) -> None:
            recorded.append((learning_id, outcome))

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
            patch("trw_mcp.state.tiers.TierManager.sweep", return_value=fake_sweep_result),
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
        from tests._ceremony_helpers import make_ceremony_server as _make_ceremony_server

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

        assert result["critical_steps_completed"] == 2
        assert result["deferred_steps"] == 11
        assert result["deferred"] == "launched"
        assert result["success"] is True
