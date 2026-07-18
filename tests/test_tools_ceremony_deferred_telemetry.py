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
        """Critical path reports 2 completed steps and launches the deferred batch."""
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
        # The deferred-step count is no longer re-emitted per deliver response
        # (token-bloat trim — it is a compile-time constant). Its anti-drift
        # guarantee lives in ``test_deferred_step_count_matches_executed_steps``.
        assert result["deferred"] == "launched"
        assert result["success"] is True

    def test_deferred_step_count_matches_executed_steps(
        self,
        tmp_path: Path,
    ) -> None:
        """DEFERRED_STEP_COUNT equals the number of steps _run_deferred_steps actually runs.

        This is the anti-drift guard for the truthfulness fix: it drives the
        REAL orchestrator with every step stubbed and asserts that the set of
        per-step result keys equals the DEFERRED_STEPS roster. If someone adds a
        ``_timed_step`` call without adding it to the roster (or vice versa),
        this test fails — so the reported ``deferred_steps`` count can never
        again claim a number different from what runs.
        """
        import structlog

        from trw_mcp.tools._deferred_delivery import (
            DEFERRED_STEP_COUNT,
            DEFERRED_STEPS,
        )

        trw_dir = _make_deferred_trw_dir(tmp_path)
        stubs = _stub_all_deferred_steps()
        with (
            patch(
                "trw_mcp.tools._deferred_delivery._step_delivery_metrics",
                return_value={"status": "skipped"},
            ),
            _apply_stubs(stubs),
            structlog.testing.capture_logs() as cap_logs,
        ):
            results = _run_deferred_steps(trw_dir, None, {})

        # Every roster name produced a result key.
        executed = {k for k in results if k not in ("timestamp", "elapsed_seconds", "watchdog")}
        assert executed == set(DEFERRED_STEPS)
        assert len(DEFERRED_STEPS) == DEFERRED_STEP_COUNT
        # index_sync runs by default (skip_index_sync defaults False).
        assert "index_sync" in executed

        # The self-reported completion log derives ``steps`` from the roster, so
        # it can never again claim a number different from what actually ran
        # (the old ``len(results) - 2`` reported 12 while 13 steps executed).
        complete = next(e for e in cap_logs if e["event"] == "deferred_deliver_complete")
        assert complete["steps"] == DEFERRED_STEP_COUNT
        assert complete["steps"] == len(executed)


@pytest.mark.integration
class TestStepTelemetryTornEvents:
    """The telemetry step's events.jsonl read tolerates torn concurrent appends."""

    def test_torn_events_line_does_not_fail_telemetry_step(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A torn append in events.jsonl drops one line, not the whole step.

        ``_step_telemetry`` reads the run's events.jsonl only for advisory
        analytics — ``tools_invoked`` and the ceremony-score inputs. The strict
        ``FileStateReader.read_jsonl`` raised ``StateError`` on the first
        malformed line, and ``_run_step`` records any step exception as a failed
        step — so a single torn concurrent append wiped the entire telemetry
        emission (tool count, ceremony score, and the session_summary write that
        feeds trw_quality_dashboard). The resilient reader skips just the torn
        line, so the surrounding intact events are still counted (regression
        guard).
        """
        from unittest.mock import MagicMock

        from trw_mcp.tools._deferred_steps_telemetry import _step_telemetry

        run_dir = tmp_path / ".trw" / "runs" / "task" / "20260211T120000Z-test"
        meta = run_dir / "meta"
        meta.mkdir(parents=True)
        (meta / "run.yaml").write_text(
            "run_id: r\nstatus: active\nphase: implement\ntask: t\n",
            encoding="utf-8",
        )
        intact_a = '{"ts": "2026-02-11T12:00:00Z", "type": "tool_call"}\n'
        torn = '{"ts": "2026-02-11T12:01:00Z", "type": "tool_ca\n'  # truncated mid-object
        intact_b = '{"ts": "2026-02-11T12:02:00Z", "type": "checkpoint"}\n'
        (meta / "events.jsonl").write_text(intact_a + torn + intact_b, encoding="utf-8")

        trw_dir = tmp_path / ".trw"
        (trw_dir / "context").mkdir(parents=True, exist_ok=True)

        recorded: list[Any] = []
        mock_client = MagicMock()
        mock_client.record_event = recorded.append

        # Patch heavy collaborators at their source modules (imported at call
        # time inside _step_telemetry), so the test stays hermetic and free of
        # real telemetry / installation side effects.
        monkeypatch.setattr(
            "trw_mcp.telemetry.client.TelemetryClient.from_config",
            classmethod(lambda cls: mock_client),
        )
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_installation_id",
            lambda: "test-inst",
        )
        monkeypatch.setattr(
            "trw_mcp.state._paths.resolve_trw_dir",
            lambda: trw_dir,
        )
        monkeypatch.setattr(
            "trw_mcp.state.analytics.report.compute_ceremony_score",
            lambda *a, **k: {"score": 0, "build_passed": False},
        )
        monkeypatch.setattr(
            "trw_mcp.telemetry.pipeline.TelemetryPipeline.get_instance",
            classmethod(lambda cls: MagicMock()),
        )

        result = _step_telemetry(run_dir)

        assert result["status"] == "success"
        # The torn middle line is dropped; both intact events are counted.
        session_end = next(e for e in recorded if type(e).__name__ == "SessionEndEvent")
        assert session_end.tools_invoked == 2
