"""Edge-case tests for recall_tracking module.

Covers uncovered branches and boundary conditions NOT in test_recall_tracking.py:
- get_recall_stats with empty/missing learning_id (line 102: ``if lid:``)
- get_recall_stats with unknown outcome values (not positive/negative/neutral)
- get_recall_stats with None outcomes from recall entries
- get_recall_stats entries_dir parameter (accepted but unused)
- record_recall fail-open specifically on StateError
- record_outcome timestamp field presence
- get_recall_stats unique_learnings counting across recalls + outcomes
- record_outcome fail-open when writer.append_jsonl raises non-OSError
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trw_mcp.exceptions import StateError
from trw_mcp.state.recall_tracking import (
    _TRACKING_FILE,
    get_recall_stats,
    record_outcome,
    record_recall,
)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a temp .trw directory."""
    d = tmp_path / ".trw"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _patch_trw_dir(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect resolve_trw_dir to the temp directory."""
    monkeypatch.setattr(
        "trw_mcp.state.recall_tracking.resolve_trw_dir",
        lambda: trw_dir,
    )


# ---------------------------------------------------------------------------
# get_recall_stats — empty/missing learning_id
# ---------------------------------------------------------------------------


class TestGetRecallStatsLearningIdEdgeCases:
    """Branch: line 102 — ``if lid:`` skips empty learning_ids."""

    def test_empty_learning_id_not_counted_as_unique(self, trw_dir: Path) -> None:
        """Records with empty learning_id are counted in total but not unique."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)

        records = [
            {"learning_id": "", "query": "q1", "timestamp": 1.0, "outcome": None},
            {"learning_id": "L-001", "query": "q2", "timestamp": 2.0, "outcome": None},
        ]
        tracking_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        stats = get_recall_stats()
        assert stats["total_recalls"] == 2
        assert stats["unique_learnings"] == 1  # only "L-001"

    def test_missing_learning_id_key_not_counted(self, trw_dir: Path) -> None:
        """Records missing the learning_id key entirely default to '' via get()."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)

        records = [
            {"query": "q1", "timestamp": 1.0, "outcome": None},  # no learning_id
            {"learning_id": "L-002", "query": "q2", "timestamp": 2.0, "outcome": None},
        ]
        tracking_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        stats = get_recall_stats()
        assert stats["total_recalls"] == 2
        assert stats["unique_learnings"] == 1  # only "L-002"


# ---------------------------------------------------------------------------
# get_recall_stats — unknown / None outcome values
# ---------------------------------------------------------------------------


class TestGetRecallStatsOutcomeEdgeCases:
    """Outcomes not in {positive, negative, neutral} do not increment counters."""

    def test_unknown_outcome_counted_in_total_not_buckets(self, trw_dir: Path) -> None:
        """An outcome value like 'unknown' increments total but no bucket."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)

        records = [
            {"learning_id": "L-001", "outcome": "unknown", "timestamp": 1.0},
            {"learning_id": "L-001", "outcome": "positive", "timestamp": 2.0},
        ]
        tracking_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        stats = get_recall_stats()
        assert stats["total_recalls"] == 2
        assert stats["positive_outcomes"] == 1
        assert stats["negative_outcomes"] == 0
        assert stats["neutral_outcomes"] == 0

    def test_none_outcome_not_counted_in_buckets(self, trw_dir: Path) -> None:
        """Recall entries with outcome=None increment total but no bucket."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)

        records = [
            {"learning_id": "L-001", "query": "q", "timestamp": 1.0, "outcome": None},
        ]
        tracking_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        stats = get_recall_stats()
        assert stats["total_recalls"] == 1
        assert stats["positive_outcomes"] == 0
        assert stats["negative_outcomes"] == 0
        assert stats["neutral_outcomes"] == 0

    def test_missing_outcome_key_not_counted(self, trw_dir: Path) -> None:
        """Records with no outcome key at all don't increment any bucket."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)

        records = [
            {"learning_id": "L-001", "timestamp": 1.0},  # no outcome key
        ]
        tracking_path.write_text(
            "\n".join(json.dumps(r) for r in records) + "\n",
            encoding="utf-8",
        )

        stats = get_recall_stats()
        assert stats["total_recalls"] == 1
        assert stats["positive_outcomes"] == 0
        assert stats["negative_outcomes"] == 0
        assert stats["neutral_outcomes"] == 0


# ---------------------------------------------------------------------------
# get_recall_stats — unique_learnings dedup across recall + outcome records
# ---------------------------------------------------------------------------


class TestGetRecallStatsUniqueLearnings:
    """Verify unique_learnings counts distinct IDs across both record types."""

    def test_same_id_in_recall_and_outcome_counted_once(self, trw_dir: Path) -> None:
        """A learning_id appearing in both recall and outcome entries is one unique."""
        record_recall("L-001", "query")
        record_outcome("L-001", "positive")

        stats = get_recall_stats()
        assert stats["unique_learnings"] == 1
        assert stats["total_recalls"] == 2  # 1 recall + 1 outcome

    def test_many_distinct_ids_all_counted(self, trw_dir: Path) -> None:
        """Each distinct learning_id is counted once."""
        for i in range(5):
            record_recall(f"L-{i:03d}", f"query-{i}")

        stats = get_recall_stats()
        assert stats["unique_learnings"] == 5
        assert stats["total_recalls"] == 5


# ---------------------------------------------------------------------------
# get_recall_stats — entries_dir parameter (unused but accepted)
# ---------------------------------------------------------------------------


class TestGetRecallStatsEntriesDirParam:
    """The entries_dir parameter is accepted but ignored — verify no crash."""

    def test_entries_dir_none_works(self, trw_dir: Path) -> None:
        """Calling with entries_dir=None (default) works normally."""
        record_recall("L-001", "query")
        stats = get_recall_stats(entries_dir=None)
        assert stats["total_recalls"] == 1

    def test_entries_dir_with_path_does_not_crash(self, trw_dir: Path) -> None:
        """Calling with a Path for entries_dir doesn't change behavior."""
        record_recall("L-001", "query")
        stats = get_recall_stats(entries_dir=Path("/some/unused/path"))
        assert stats["total_recalls"] == 1


# ---------------------------------------------------------------------------
# record_recall — StateError fail-open
# ---------------------------------------------------------------------------


class TestRecordRecallStateError:
    """record_recall catches (OSError, StateError) — verify StateError path."""

    def test_state_error_returns_false(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """StateError during write returns False, not raised."""
        mock_writer = MagicMock()
        mock_writer.append_jsonl.side_effect = StateError("write failed")
        monkeypatch.setattr(
            "trw_mcp.state.recall_tracking.FileStateWriter",
            lambda: mock_writer,
        )

        result = record_recall("L-fail", "query")
        assert result is False


# ---------------------------------------------------------------------------
# record_outcome — timestamp field
# ---------------------------------------------------------------------------


class TestRecordOutcomeTimestamp:
    """record_outcome entries include a float timestamp."""

    def test_outcome_entry_has_timestamp(self, trw_dir: Path) -> None:
        """The outcome record written to JSONL contains a float timestamp."""
        record_recall("L-001", "query")
        record_outcome("L-001", "positive")

        tracking_path = trw_dir / _TRACKING_FILE
        lines = tracking_path.read_text().strip().splitlines()
        outcome_record = json.loads(lines[1])
        assert "timestamp" in outcome_record
        assert isinstance(outcome_record["timestamp"], float)


# ---------------------------------------------------------------------------
# record_outcome — fail-open on non-OSError exception
# ---------------------------------------------------------------------------


class TestRecordOutcomeFailOpen:
    """record_outcome uses broad ``except Exception`` — verify coverage."""

    def test_type_error_during_write_returns_false(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A TypeError during append_jsonl returns False, not raised."""
        # First create the file so the existence check passes
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        tracking_path.write_text("", encoding="utf-8")

        mock_writer = MagicMock()
        mock_writer.append_jsonl.side_effect = TypeError("unexpected")
        monkeypatch.setattr(
            "trw_mcp.state.recall_tracking.FileStateWriter",
            lambda: mock_writer,
        )

        result = record_outcome("L-001", "positive")
        assert result is False


# ---------------------------------------------------------------------------
# get_recall_stats — reader failure on corrupted file
# ---------------------------------------------------------------------------


class TestGetRecallStatsReaderFailure:
    """get_recall_stats returns zeroed defaults when reader raises."""

    def test_corrupted_jsonl_returns_zeroed_stats(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If FileStateReader.read_jsonl raises, stats are zeroed defaults."""
        # Create the tracking file so existence check passes
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        tracking_path.write_text("not valid json\n", encoding="utf-8")

        # The real FileStateReader.read_jsonl raises StateError on bad JSON
        # which is caught by the broad except Exception
        mock_reader = MagicMock()
        mock_reader.read_jsonl.side_effect = StateError("bad json")
        monkeypatch.setattr(
            "trw_mcp.state.recall_tracking.FileStateReader",
            lambda: mock_reader,
        )

        stats = get_recall_stats()
        assert stats["total_recalls"] == 0
        assert stats["unique_learnings"] == 0
        assert stats["positive_outcomes"] == 0
        assert stats["negative_outcomes"] == 0
        assert stats["neutral_outcomes"] == 0


# ---------------------------------------------------------------------------
# get_recall_stats — all three outcome types together
# ---------------------------------------------------------------------------


class TestGetRecallStatsAllOutcomes:
    """Verify all three outcome buckets accumulate correctly together."""

    def test_mixed_outcomes_all_counted(self, trw_dir: Path) -> None:
        """positive, negative, and neutral outcomes each count independently."""
        record_recall("L-001", "q")
        record_outcome("L-001", "positive")
        record_outcome("L-001", "negative")
        record_outcome("L-001", "neutral")
        record_outcome("L-001", "positive")

        stats = get_recall_stats()
        assert stats["positive_outcomes"] == 2
        assert stats["negative_outcomes"] == 1
        assert stats["neutral_outcomes"] == 1
        # total = 1 recall + 4 outcomes = 5
        assert stats["total_recalls"] == 5
