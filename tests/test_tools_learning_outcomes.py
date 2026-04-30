"""Tests for outcome correlation and Q-value updates after recall."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import (
    compute_initial_q_value,
    correlate_recalls,
    process_outcome,
    process_outcome_for_event,
)
from trw_mcp.state.persistence import FileStateReader

from tests._tools_learning_shared import _CFG, _entries_dir, _get_tools, set_project_root

class TestOutcomeCorrelation:
    """Tests for PRD-CORE-004 Phase 1c — automatic outcome correlation."""

    def test_process_outcome_updates_q_values(self, tmp_path: Path, reader: FileStateReader) -> None:
        """_process_outcome updates Q-values for recently recalled learnings."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outcome correlation q update test",
            detail="Should have Q-value updated",
            impact=0.5,
        )
        lid = result["learning_id"]

        # Recall the learning (creates receipt)
        tools["trw_recall"].fn(query="outcome correlation q update")

        # Process a positive outcome
        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        assert lid in updated

        # Verify Q-value was updated on disk
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert float(str(data.get("q_value", 0.5))) > 0.5
                assert int(str(data.get("q_observations", 0))) == 1
                break

    def test_process_outcome_writes_history(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Outcome processing appends to outcome_history."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Outcome history write test",
            detail="Should have outcome_history entry",
            impact=0.5,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="outcome history write")

        trw_dir = tmp_path / _CFG.trw_dir
        process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                history = data.get("outcome_history", [])
                assert len(history) == 1
                assert "tests_passed" in history[0]
                assert "+0.8" in history[0]
                break

    def test_process_outcome_caps_history(
        self,
        tmp_path: Path,
        reader: FileStateReader,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """outcome_history is capped to learning_outcome_history_cap."""
        # Set cap to 3 for testing — process_outcome reads get_config() in _correlation
        cfg = TRWConfig(learning_outcome_history_cap=3)
        monkeypatch.setattr("trw_mcp.scoring._correlation.get_config", lambda: cfg)

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="History cap test entry",
            detail="Check history capping",
            impact=0.5,
        )
        lid = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir

        # Process 5 outcomes
        for i in range(5):
            tools["trw_recall"].fn(query="history cap test")
            process_outcome(trw_dir, reward=0.8, event_label=f"event_{i}")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                history = data.get("outcome_history", [])
                assert len(history) <= 3
                # Should keep the most recent
                assert "event_4" in history[-1]
                break

    def test_process_outcome_no_receipts(self, tmp_path: Path) -> None:
        """_process_outcome returns empty list when no receipts exist."""
        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")
        assert updated == []

    def test_correlate_recalls_time_window(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Only receipts within the correlation window are included."""
        trw_dir = tmp_path / _CFG.trw_dir
        # PRD-QUAL-032: correlate_recalls reads from logs/recall_tracking.jsonl
        receipt_dir = trw_dir / "logs"
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_tracking.jsonl"

        now = datetime.now(timezone.utc)
        # Recent receipt (2 minutes ago)
        recent = {
            "ts": (now - timedelta(minutes=2)).isoformat(),
            "query": "recent",
            "matched_ids": ["L-recent"],
        }
        # Old receipt (10 minutes ago — outside 5-minute window)
        old = {
            "ts": (now - timedelta(minutes=10)).isoformat(),
            "query": "old",
            "matched_ids": ["L-old"],
        }
        receipt_path.write_text(
            json.dumps(recent) + "\n" + json.dumps(old) + "\n",
            encoding="utf-8",
        )

        results = correlate_recalls(trw_dir, window_minutes=5)
        lids = [lid for lid, _ in results]
        assert "L-recent" in lids
        assert "L-old" not in lids

    def test_correlate_recalls_recency_discount(self, tmp_path: Path) -> None:
        """More recent receipts get higher recency discount."""
        trw_dir = tmp_path / _CFG.trw_dir
        # PRD-QUAL-032: correlate_recalls reads from logs/recall_tracking.jsonl
        receipt_dir = trw_dir / "logs"
        receipt_dir.mkdir(parents=True)
        receipt_path = receipt_dir / "recall_tracking.jsonl"

        now = datetime.now(timezone.utc)
        # Very recent (1 minute ago)
        very_recent = {
            "ts": (now - timedelta(minutes=1)).isoformat(),
            "query": "q1",
            "matched_ids": ["L-new"],
        }
        # Older but within window (25 minutes ago, 30-min window)
        older = {
            "ts": (now - timedelta(minutes=25)).isoformat(),
            "query": "q2",
            "matched_ids": ["L-older"],
        }
        receipt_path.write_text(
            json.dumps(very_recent) + "\n" + json.dumps(older) + "\n",
            encoding="utf-8",
        )

        results = correlate_recalls(trw_dir, window_minutes=30)
        discount_map = dict(results)
        assert discount_map["L-new"] > discount_map["L-older"]
        assert discount_map["L-new"] > 0.9  # nearly full credit
        assert discount_map["L-older"] >= 0.5  # at least minimum

    def test_correlate_recalls_empty(self, tmp_path: Path) -> None:
        """No receipt file returns empty list."""
        trw_dir = tmp_path / _CFG.trw_dir
        assert correlate_recalls(trw_dir, window_minutes=30) == []

    def test_process_outcome_for_event_known_type(self, tmp_path: Path) -> None:
        """process_outcome_for_event triggers for known event types."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Event type correlation test",
            detail="Should correlate with tests_passed",
            impact=0.5,
        )
        lid = result["learning_id"]

        # Recall to create receipt
        tools["trw_recall"].fn(query="event type correlation")

        # Fire known event type
        updated = process_outcome_for_event("tests_passed")
        assert lid in updated

    def test_process_outcome_for_event_unknown_type(self, tmp_path: Path) -> None:
        """process_outcome_for_event returns empty for unknown event types."""
        updated = process_outcome_for_event("some_random_event")
        assert updated == []

    def test_process_outcome_for_event_error_keyword(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Events with error keywords get negative reward."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Error keyword correlation test",
            detail="Should get negative reward from error event",
            impact=0.5,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="error keyword correlation")

        updated = process_outcome_for_event("build_error_occurred")
        assert lid in updated

        # Verify Q-value decreased
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                assert float(str(data.get("q_value", 0.5))) < compute_initial_q_value(0.5)
                break

    def test_negative_reward_decreases_q(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Negative reward events decrease Q-value."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Negative reward q decrease test",
            detail="Q should decrease",
            impact=0.7,
        )
        lid = result["learning_id"]

        tools["trw_recall"].fn(query="negative reward q decrease")

        trw_dir = tmp_path / _CFG.trw_dir
        process_outcome(trw_dir, reward=-0.5, event_label="tests_failed")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                q_val = float(str(data.get("q_value", 0.5)))
                assert q_val < compute_initial_q_value(0.7)
                break

    def test_multiple_outcomes_converge(self, tmp_path: Path, reader: FileStateReader) -> None:
        """Multiple positive outcomes increase Q-value progressively."""
        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Convergence outcome test",
            detail="Q should increase with repeated positive outcomes",
            impact=0.5,
        )
        lid = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir
        for _ in range(5):
            tools["trw_recall"].fn(query="convergence outcome test")
            process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == lid:
                q_val = float(str(data.get("q_value", 0.5)))
                assert q_val > 0.6  # moved toward 0.8
                assert int(str(data.get("q_observations", 0))) >= 5
                break

    def test_only_matched_learnings_updated(self, tmp_path: Path) -> None:
        """Only learnings in recent receipts have Q-values updated."""
        tools = _get_tools()
        r1 = tools["trw_learn"].fn(
            summary="Kangaroo marsupial pouch habitat",
            detail="Should be updated",
            impact=0.5,
        )
        r2 = tools["trw_learn"].fn(
            summary="Submarine deep ocean vessel pressure",
            detail="Should NOT be updated",
            impact=0.5,
        )

        # Only recall the first entry — query must be specific enough
        # to avoid matching the second entry via shared tokens
        tools["trw_recall"].fn(query="kangaroo marsupial pouch")

        trw_dir = tmp_path / _CFG.trw_dir
        updated = process_outcome(trw_dir, reward=0.8, event_label="tests_passed")

        assert r1["learning_id"] in updated
        assert r2["learning_id"] not in updated
