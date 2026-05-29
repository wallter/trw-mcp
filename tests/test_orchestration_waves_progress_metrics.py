from __future__ import annotations

from pathlib import Path

import pytest

import trw_mcp.tools.orchestration as orch_mod
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools._orchestration_phase import (
    _compute_reversion_metrics,
    _compute_wave_progress,
)


class TestComputeWaveProgress:
    """Direct tests for _compute_wave_progress (lines 329-383)."""

    def test_empty_waves_returns_none(self, tmp_path: Path) -> None:
        """Returns None when waves list is empty."""
        result = _compute_wave_progress({"waves": []}, tmp_path)
        assert result is None

    def test_non_list_waves_returns_none(self, tmp_path: Path) -> None:
        """Returns None when waves is not a list."""
        result = _compute_wave_progress({"waves": "not-a-list"}, tmp_path)
        assert result is None

    def test_missing_waves_key_returns_none(self, tmp_path: Path) -> None:
        """Returns None when 'waves' key not in wave_data."""
        result = _compute_wave_progress({}, tmp_path)
        assert result is None

    def test_single_complete_wave(self, tmp_path: Path) -> None:
        """Single complete wave returns correct progress."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1", "s2"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 1
        assert result["completed_waves"] == 1
        assert result["active_wave"] is None

    def test_active_wave_detected(self, tmp_path: Path) -> None:
        """Active wave is identified correctly."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": []},
                {"wave": 2, "status": "active", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["active_wave"] == 2
        assert result["completed_waves"] == 1

    def test_partial_wave_counted_as_completed(self, tmp_path: Path) -> None:
        """Partial status waves are counted in completed_waves."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "partial", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["completed_waves"] == 1

    def test_wave_with_shard_manifest(self, tmp_path: Path) -> None:
        """Shard statuses are read from manifest.yaml."""
        shards_dir = tmp_path / "shards"
        shards_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(
            shards_dir / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "complete"},
                    {"id": "s2", "status": "active"},
                    {"id": "s3", "status": "pending"},
                ]
            },
        )

        wave_data = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": ["s1", "s2", "s3"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        wave_detail = result["wave_details"][0]
        assert wave_detail["shards"]["complete"] == 1
        assert wave_detail["shards"]["active"] == 1
        assert wave_detail["shards"]["pending"] == 1

    def test_active_wave_from_shard_status(self, tmp_path: Path) -> None:
        """Wave with active shards is flagged as active even if wave status is pending."""
        shards_dir = tmp_path / "shards"
        shards_dir.mkdir(parents=True)

        writer = FileStateWriter()
        writer.write_yaml(
            shards_dir / "manifest.yaml",
            {
                "shards": [
                    {"id": "s1", "status": "active"},
                ]
            },
        )

        wave_data = {
            "waves": [
                {"wave": 3, "status": "pending", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["active_wave"] == 3

    def test_wave_detail_structure(self, tmp_path: Path) -> None:
        """wave_details has expected keys."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "complete", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        detail = result["wave_details"][0]
        assert "wave" in detail
        assert "status" in detail
        assert "shards" in detail
        assert "total" in detail["shards"]

    def test_non_dict_wave_skipped(self, tmp_path: Path) -> None:
        """Non-dict entries in waves list are skipped gracefully."""
        wave_data = {
            "waves": [
                "not-a-dict",
                {"wave": 1, "status": "complete", "shards": []},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 2
        assert len(result["wave_details"]) == 1

    def test_non_list_shards_in_wave_handled(self, tmp_path: Path) -> None:
        """Non-list shards value in wave is treated as empty."""
        wave_data = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": "not-a-list"},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["wave_details"][0]["shards"]["total"] == 0

    def test_corrupted_shard_manifest_silently_ignored(self, tmp_path: Path) -> None:
        """Corrupted shard manifest doesn't raise — shard_statuses stays empty."""
        shards_dir = tmp_path / "shards"
        shards_dir.mkdir(parents=True)
        (shards_dir / "manifest.yaml").write_text("!!invalid: yaml: [\n", encoding="utf-8")

        wave_data = {
            "waves": [
                {"wave": 1, "status": "pending", "shards": ["s1"]},
            ]
        }
        result = _compute_wave_progress(wave_data, tmp_path)

        assert result is not None
        assert result["total_waves"] == 1
        details = result["wave_details"]
        assert isinstance(details, list)
        assert details[0]["shards"]["pending"] == 1


class TestComputeReversionMetrics:
    """Direct tests for _compute_reversion_metrics (lines 415-430)."""

    def test_no_events_healthy_zero_rate(self) -> None:
        """Empty events yields healthy classification with zero rate."""
        result = _compute_reversion_metrics([])
        assert result["count"] == 0
        assert result["rate"] == 0.0
        assert result["classification"] == "healthy"
        assert result["latest"] is None
        assert result["by_trigger"] == {}

    def test_trigger_classified_key_used_over_trigger(self) -> None:
        """trigger_classified key takes precedence over trigger in by_trigger."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "trigger_classified": "refactor",
                "trigger": "raw-trigger",
            }
        ]
        result = _compute_reversion_metrics(events)
        assert "refactor" in result["by_trigger"]
        assert "raw-trigger" not in result["by_trigger"]

    def test_trigger_fallback_when_no_trigger_classified(self) -> None:
        """Falls back to 'trigger' when 'trigger_classified' absent."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert", "trigger": "scope-creep"},
        ]
        result = _compute_reversion_metrics(events)
        assert "scope-creep" in result["by_trigger"]

    def test_trigger_defaults_to_other_when_both_absent(self) -> None:
        """Falls back to 'other' when neither trigger key present."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
        ]
        result = _compute_reversion_metrics(events)
        assert "other" in result["by_trigger"]

    def test_concerning_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """High reversion rate classified as 'concerning'."""
        cfg = TRWConfig(reversion_rate_concerning=0.0, reversion_rate_elevated=0.0)
        monkeypatch.setattr(orch_mod, "_config", cfg)

        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "concerning"

    def test_elevated_classification(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Moderate reversion rate classified as 'elevated'."""
        cfg = TRWConfig(reversion_rate_concerning=0.9, reversion_rate_elevated=0.1)
        monkeypatch.setattr("trw_mcp.tools._orchestration_phase.get_config", lambda: cfg)

        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "elevated"

    def test_healthy_classification_default(self) -> None:
        """Zero reverts with default thresholds is 'healthy'."""
        events: list[dict[str, object]] = [
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["classification"] == "healthy"

    def test_latest_reversion_populated(self) -> None:
        """latest field contains info from most recent phase_revert event."""
        events: list[dict[str, object]] = [
            {
                "event": "phase_revert",
                "from_phase": "implement",
                "to_phase": "plan",
                "trigger_classified": "refactor",
                "reason": "Found bigger issue",
                "ts": "2026-01-01T00:00:00Z",
            },
            {
                "event": "phase_revert",
                "from_phase": "validate",
                "to_phase": "implement",
                "trigger": "test_failure",
                "reason": "Tests broke",
                "ts": "2026-01-02T00:00:00Z",
            },
        ]
        result = _compute_reversion_metrics(events)
        latest = result["latest"]
        assert latest is not None
        assert latest["from_phase"] == "validate"
        assert latest["to_phase"] == "implement"
        assert latest["reason"] == "Tests broke"

    def test_multiple_triggers_counted(self) -> None:
        """Multiple reverts with same trigger are accumulated."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert", "trigger": "scope"},
            {"event": "phase_revert", "trigger": "scope"},
            {"event": "phase_revert", "trigger": "blocker"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["by_trigger"]["scope"] == 2
        assert result["by_trigger"]["blocker"] == 1
        assert result["count"] == 3

    def test_rate_calculation_with_mixed_events(self) -> None:
        """Rate = revert_count / (revert_count + phase_enter_count)."""
        events: list[dict[str, object]] = [
            {"event": "phase_revert"},
            {"event": "phase_revert"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
            {"event": "phase_enter"},
        ]
        result = _compute_reversion_metrics(events)
        assert result["rate"] == pytest.approx(0.4, abs=0.001)
        assert result["count"] == 2
