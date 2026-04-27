"""PRD-CORE-144 FR07 (telemetry) + FR08 (replay helper) coverage."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import structlog
from structlog.testing import capture_logs

from trw_mcp.sync.outcomes import write_synced_marker
from trw_mcp.tools._deferred_steps_learning import _step_delivery_metrics
from trw_mcp.tools.replay import replay_outcomes


def _mk_run(runs_root: Path, task: str, run_id: str) -> Path:
    run_dir = runs_root / task / run_id
    (run_dir / "meta").mkdir(parents=True, exist_ok=True)
    (run_dir / "meta" / "run.yaml").write_text("session_metrics:\n  status: success\n", encoding="utf-8")
    return run_dir


class TestReplayGate:
    def test_replay_without_env_is_noop(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("TRW_ALLOW_REPLAY", raising=False)
        runs_root = tmp_path / "runs"
        run_dir = _mk_run(runs_root, "t", "r")
        write_synced_marker(run_dir, run_id="r", sync_hash="h", target_label="x")

        result = replay_outcomes(tmp_path)
        assert result["gated"] is True
        assert result["replayed"] == 0
        # Marker untouched
        assert (run_dir / "meta" / "synced.json").exists()

    def test_replay_with_env_deletes_all_markers(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
        runs_root = tmp_path / "runs"
        r1 = _mk_run(runs_root, "t", "r1")
        r2 = _mk_run(runs_root, "t", "r2")
        write_synced_marker(r1, run_id="r1", sync_hash="h1", target_label="x")
        write_synced_marker(r2, run_id="r2", sync_hash="h2", target_label="x")

        result = replay_outcomes(tmp_path)
        assert result["gated"] is False
        assert result["replayed"] == 2
        assert not (r1 / "meta" / "synced.json").exists()
        assert not (r2 / "meta" / "synced.json").exists()

    def test_replay_respects_since_cutoff(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
        runs_root = tmp_path / "runs"
        r1 = _mk_run(runs_root, "t", "r1")  # will be old
        r2 = _mk_run(runs_root, "t", "r2")  # will be new

        old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        new_ts = datetime.now(timezone.utc).isoformat()
        (r1 / "meta" / "synced.json").write_text(
            json.dumps({"synced_at": old_ts, "sync_hash": "h1", "run_id": "r1"}),
            encoding="utf-8",
        )
        (r2 / "meta" / "synced.json").write_text(
            json.dumps({"synced_at": new_ts, "sync_hash": "h2", "run_id": "r2"}),
            encoding="utf-8",
        )

        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        result = replay_outcomes(tmp_path, since=cutoff)
        assert result["replayed"] == 1
        assert (r1 / "meta" / "synced.json").exists()
        assert not (r2 / "meta" / "synced.json").exists()

    def test_replay_no_runs_dir_is_safe(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("TRW_ALLOW_REPLAY", "1")
        result = replay_outcomes(tmp_path)
        assert result == {"gated": False, "replayed": 0, "scanned": 0, "since": ""}


class TestDeliveryExposureTelemetry:
    def test_telemetry_event_emitted(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        structlog.configure(processors=[structlog.processors.JSONRenderer()])

        with patch.dict("os.environ", {"TRW_SESSION_ID": "t-1"}), capture_logs() as cap:
            _step_delivery_metrics(trw_dir, None)

        events = [e for e in cap if e.get("event") == "delivery_exposure_telemetry"]
        assert len(events) >= 1
        ev = events[0]
        assert ev["session_id_populated"] is True
        assert "recall_pull_rate" in ev
        assert "nudge_count" in ev
        assert "learning_ids_count" in ev
