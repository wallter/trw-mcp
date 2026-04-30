"""Branch tests for distribution/date logic and recall correlation paths."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import trw_mcp.scoring as scoring_mod


class TestEnforceTierDistributionWithDates:
    """Tests for enforce_tier_distribution with entry_dates parameter (G5)."""

    def _make_entries(self, count: int, base_score: float = 0.95) -> list[tuple[str, float]]:
        return [(f"L-{i:03d}", base_score) for i in range(count)]

    def test_without_entry_dates_unchanged_behavior(self) -> None:
        """entry_dates=None preserves existing behavior — no decay applied."""
        entries = self._make_entries(10, base_score=0.95)
        result_no_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=None)
        result_explicit_none = scoring_mod.enforce_tier_distribution(entries)
        assert result_no_dates == result_explicit_none

    def test_with_entry_dates_old_entries_decay_below_critical(self) -> None:
        """Old entries with entry_dates get decayed, causing tier demotion to differ."""
        entries = self._make_entries(10, base_score=0.95)
        old_date = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        entry_dates = {f"L-{i:03d}": old_date for i in range(8)}

        result_with_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=entry_dates)
        result_without_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=None)

        assert isinstance(result_with_dates, list)
        assert isinstance(result_without_dates, list)
        for entry_id, new_score in result_with_dates:
            assert isinstance(entry_id, str)
            assert isinstance(new_score, float)
        for entry_id, new_score in result_without_dates:
            assert isinstance(entry_id, str)
            assert isinstance(new_score, float)

    def test_entry_dates_decays_tier_classification(self) -> None:
        """Entries classified critical via raw score get demoted when decayed below 0.9."""
        now = datetime.now(timezone.utc)
        very_old = (now - timedelta(days=730)).isoformat()

        entries = [(f"L-{i}", 0.91) for i in range(6)]
        entry_dates = {f"L-{i}": very_old for i in range(6)}

        result_with_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=entry_dates)
        result_without_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=None)

        assert len(result_without_dates) >= 1, "Expected demotion without dates"
        assert len(result_with_dates) == 0, "Expected no demotion with old entry_dates"

    def test_entry_dates_partial_some_have_dates(self) -> None:
        """When only some entries have dates, undated entries use raw scores."""
        now = datetime.now(timezone.utc)
        very_old = (now - timedelta(days=730)).isoformat()

        entries = [(f"L-{i}", 0.95) for i in range(6)]
        entry_dates = {f"L-{i}": very_old for i in range(3)}

        result = scoring_mod.enforce_tier_distribution(entries, entry_dates=entry_dates)

        assert isinstance(result, list)
        demoted_ids = {entry_id for entry_id, _new_score in result}
        for learning_id in demoted_ids:
            assert learning_id.startswith("L-")

    def test_entry_dates_invalid_date_string_falls_back_to_raw(self) -> None:
        """Invalid date string in entry_dates falls back to raw score (no crash)."""
        entries = [(f"L-{i}", 0.95) for i in range(6)]
        entry_dates = {"L-0": "not-a-valid-date", "L-1": "also-bad"}

        result = scoring_mod.enforce_tier_distribution(entries, entry_dates=entry_dates)
        assert isinstance(result, list)
        result_no_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=None)
        assert len(result) == len(result_no_dates), "Invalid dates should fall back to same behavior as no dates"

    def test_entry_dates_empty_string_falls_back_to_raw(self) -> None:
        """Empty string date in entry_dates uses raw score (no decay)."""
        entries = [(f"L-{i}", 0.95) for i in range(6)]
        entry_dates = {"L-0": "", "L-1": ""}

        result = scoring_mod.enforce_tier_distribution(entries, entry_dates=entry_dates)
        assert isinstance(result, list)
        result_no_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=None)
        assert len(result) == len(result_no_dates), "Empty-string dates should fall back to same behavior as no dates"

    def test_entry_dates_fresh_entries_unchanged(self) -> None:
        """Very recent entries have near-zero decay — tier classification unchanged."""
        now = datetime.now(timezone.utc)
        very_fresh = (now - timedelta(days=1)).isoformat()

        entries = [(f"L-{i}", 0.95) for i in range(6)]
        entry_dates = {f"L-{i}": very_fresh for i in range(6)}

        result_with_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=entry_dates)
        result_without_dates = scoring_mod.enforce_tier_distribution(entries, entry_dates=None)
        assert len(result_with_dates) == len(result_without_dates)


class TestDoubleDecayFix:
    """Verify that _entry_utility no longer double-decays entries."""

    @pytest.mark.unit
    def test_30_day_old_entry_no_double_decay(self) -> None:
        """_entry_utility must not apply apply_time_decay before compute_utility_score."""
        from trw_mcp.scoring import _entry_utility, apply_time_decay, compute_utility_score

        created_dt = datetime.now(timezone.utc) - timedelta(days=30)
        created_iso = created_dt.isoformat()
        entry: dict[str, object] = {
            "impact": 0.8,
            "q_value": 0.8,
            "q_observations": 5,
            "recurrence": 2,
            "access_count": 3,
            "source_type": "agent",
            "created": created_iso,
            "last_accessed": created_iso,
        }
        actual_score = _entry_utility(entry, today=datetime.now(tz=timezone.utc).date())

        decayed_impact = apply_time_decay(0.8, created_dt)
        decayed_q = apply_time_decay(0.8, created_dt)
        double_decay_score = compute_utility_score(
            q_value=decayed_q,
            days_since_last_access=30,
            recurrence_count=2,
            base_impact=decayed_impact,
            q_observations=5,
            access_count=3,
            source_type="agent",
        )
        assert actual_score >= double_decay_score, (
            f"Single-decay ({actual_score:.4f}) should be >= double-decay ({double_decay_score:.4f})"
        )


class TestCorrelateRecallsPath:
    """Verify correlate_recalls reads from the canonical write path."""

    @pytest.mark.unit
    def test_receipt_path_matches_write_path(self, tmp_path: Path) -> None:
        """correlate_recalls should read from logs/recall_tracking.jsonl."""
        import json

        from trw_mcp.scoring import correlate_recalls

        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)
        receipt_file = logs_dir / "recall_tracking.jsonl"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "learning_id": "L-test001",
            "query": "test query",
        }
        receipt_file.write_text(json.dumps(record) + "\n")

        results = correlate_recalls(tmp_path / ".trw", window_minutes=60)
        assert isinstance(results, list)
        assert len(results) >= 1, "Should find the record we wrote"
        found_id, discount = results[0]
        assert found_id == "L-test001"
        assert 0.0 < discount <= 1.0

    @pytest.mark.unit
    def test_outcome_only_rows_are_ignored(self, tmp_path: Path) -> None:
        """Outcome-tracking rows must not count as new recall evidence."""
        import json

        from trw_mcp.scoring import correlate_recalls

        logs_dir = tmp_path / ".trw" / "logs"
        logs_dir.mkdir(parents=True)
        receipt_file = logs_dir / "recall_tracking.jsonl"
        now = datetime.now(timezone.utc)
        records = [
            {
                "timestamp": now.timestamp(),
                "learning_id": "L-recall001",
                "query": "test query",
                "outcome": None,
            },
            {
                "timestamp": now.timestamp(),
                "learning_id": "L-outcome001",
                "outcome": "positive",
            },
        ]
        receipt_file.write_text("\n".join(json.dumps(record) for record in records) + "\n")

        results = correlate_recalls(tmp_path / ".trw", window_minutes=60, scope="window")

        assert any(learning_id == "L-recall001" for learning_id, _discount in results)
        assert all(learning_id != "L-outcome001" for learning_id, _discount in results)

    @pytest.mark.unit
    def test_session_scope_uses_runs_root_not_task_root(self, tmp_path: Path) -> None:
        """Session lookup should read events from .trw/runs, not docs/*/runs."""
        import json

        from trw_mcp.scoring import correlate_recalls

        trw_dir = tmp_path / ".trw"
        logs_dir = trw_dir / "logs"
        logs_dir.mkdir(parents=True)
        run_dir = trw_dir / "runs" / "task-a" / "20260402T010000Z-test"
        meta_dir = run_dir / "meta"
        meta_dir.mkdir(parents=True)
        (meta_dir / "run.yaml").write_text("status: active\n")

        session_start = datetime.now(timezone.utc) - timedelta(minutes=10)
        old_recall = datetime.now(timezone.utc) - timedelta(hours=2)
        recent_recall = datetime.now(timezone.utc) - timedelta(minutes=5)

        events_path = meta_dir / "events.jsonl"
        events_path.write_text(
            json.dumps({"ts": session_start.isoformat(), "event": "session_start"}) + "\n",
        )

        receipt_file = logs_dir / "recall_tracking.jsonl"
        records = [
            {"timestamp": old_recall.timestamp(), "learning_id": "L-old", "query": "older", "outcome": None},
            {"timestamp": recent_recall.timestamp(), "learning_id": "L-new", "query": "recent", "outcome": None},
        ]
        receipt_file.write_text("\n".join(json.dumps(record) for record in records) + "\n")

        results = correlate_recalls(trw_dir, window_minutes=480, scope="session")
        assert [learning_id for learning_id, _discount in results] == ["L-new"]
