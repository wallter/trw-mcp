"""Edge-case tests for recall_tracking module.

Covers uncovered branches and boundary conditions NOT in test_recall_tracking.py:
- get_recall_stats with empty/missing learning_id (line 102: ``if lid:``)
- get_recall_stats with unknown outcome values (not positive/negative/neutral)
- get_recall_stats with None outcomes from recall entries
- get_recall_stats entries_dir parameter (accepted but unused)
- record_recall fail-open specifically on StateError
- record_outcome timestamp field presence
- get_recall_stats unique_learnings counting across recalls + outcomes
- record_outcome fail-open when the append raises non-OSError
- PRD-FIX-144 NFR03: an unwritable logs directory leaves every tool response unchanged
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from tests._structlog_capture import captured_structlog  # noqa: F401  -- fixture, imported by name
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

    def test_unknown_outcome_counted_nowhere(self, trw_dir: Path) -> None:
        """An outcome value like 'unknown' is neither a recall nor a bucket (PRD-FIX-144 FR05)."""
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
        assert stats["total_recalls"] == 0
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
        assert stats["total_recalls"] == 1  # the receipt only; the outcome row is not a recall

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
    """record_recall is fail-open on any write failure, including StateError."""

    def test_state_error_returns_false(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """StateError during write returns False, not raised."""
        monkeypatch.setattr(
            "trw_mcp.state.recall_tracking._append_rows",
            MagicMock(side_effect=StateError("write failed")),
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
        """A TypeError during the append returns False, not raised."""
        monkeypatch.setattr(
            "trw_mcp.state.recall_tracking._append_rows",
            MagicMock(side_effect=TypeError("unexpected")),
        )

        result = record_outcome("L-001", "positive")
        assert result is False


# ---------------------------------------------------------------------------
# get_recall_stats — reader failure on corrupted file
# ---------------------------------------------------------------------------


class TestGetRecallStatsReaderFailure:
    """get_recall_stats degrades gracefully on a corrupt tracking log."""

    def test_all_lines_corrupt_returns_zeroed_stats(self, trw_dir: Path) -> None:
        """A log of only-corrupt lines yields zeroed stats (every row dropped)."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        tracking_path.write_text("not valid json\n{also bad\n", encoding="utf-8")

        stats = get_recall_stats()
        assert stats["total_recalls"] == 0
        assert stats["unique_learnings"] == 0
        assert stats["positive_outcomes"] == 0
        assert stats["negative_outcomes"] == 0
        assert stats["neutral_outcomes"] == 0

    def test_torn_line_preserves_valid_records(self, trw_dir: Path) -> None:
        """A single torn concurrent append must drop only that row.

        Regression: the strict reader raised StateError on the first malformed
        line, and the broad ``except`` zeroed the ENTIRE aggregate — one torn
        append silently wiped every valid recall/outcome record. The resilient
        reader keeps the valid rows so calibration stats survive.
        """
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        good_a = json.dumps({"learning_id": "L-001", "outcome": None})
        good_b = json.dumps({"learning_id": "L-001", "outcome": "positive"})
        good_c = json.dumps({"learning_id": "L-002", "outcome": "negative"})
        # Middle line is a torn append (a partial record interleaved by a
        # concurrent writer) — valid JSON syntax does not parse to a dict break.
        torn = '{"learning_id": "L-003", "outcome": "posi'
        tracking_path.write_text(
            f"{good_a}\n{good_b}\n{torn}\n{good_c}\n",
            encoding="utf-8",
        )

        stats = get_recall_stats()
        # 3 valid rows survive; the torn row dropped (not a total wipe to 0).
        # Only good_a is a receipt, so it is the one recall (PRD-FIX-144 FR05).
        assert stats["total_recalls"] == 1
        assert stats["unique_learnings"] == 2
        assert stats["positive_outcomes"] == 1
        assert stats["negative_outcomes"] == 1
        assert stats["neutral_outcomes"] == 0

    def test_non_utf8_row_dropped_not_fatal(self, trw_dir: Path) -> None:
        """A non-UTF-8 byte row (torn multi-byte append) drops only that row."""
        tracking_path = trw_dir / _TRACKING_FILE
        tracking_path.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"learning_id": "L-001", "outcome": "positive"}).encode("utf-8")
        # 0xff is never valid UTF-8 — simulates a row split mid multi-byte seq.
        tracking_path.write_bytes(good + b"\n" + b"\xff\xfe garbage\n")

        stats = get_recall_stats()
        assert stats["unique_learnings"] == 1  # the good row survived
        assert stats["total_recalls"] == 0  # an outcome row is not a recall (PRD-FIX-144 FR05)
        assert stats["positive_outcomes"] == 1


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
        # PRD-FIX-144 FR05: 1 receipt; the 4 outcome rows are not recalls.
        assert stats["total_recalls"] == 1


# ---------------------------------------------------------------------------
# PRD-FIX-144 NFR03 — fail-open writes, diagnostics to structlog debug
# ---------------------------------------------------------------------------

#: Values that differ between two otherwise identical calls for reasons unrelated
#: to the logs directory: timings, call-count-driven ceremony nudges, and the
#: recall ranking score (and so tokens_used), which the first pass's access and
#: feedback writes to memory.db legitimately move.
_VOLATILE_KEYS = frozenset({"step_durations_ms", "ceremony_status", "nudge_content", "reversion_prompt", "tokens_used"})
_VOLATILE_ENTRY_KEYS = frozenset({"preference_score"})


def _drive_four_tools(server: Any, lid: str) -> dict[str, dict[str, Any]]:
    from tests.conftest import extract_tool_fn

    return {
        "trw_recall": extract_tool_fn(server, "trw_recall")(query="app.py startup"),
        "trw_before_edit_hint": extract_tool_fn(server, "trw_before_edit_hint")(file_path="app.py"),
        "trw_build_check": extract_tool_fn(server, "trw_build_check")(tests_passed=True, test_count=2),
        "trw_learn_update": extract_tool_fn(server, "trw_learn_update")(learning_id=lid, feedback="helpful"),
    }


def _stable(response: dict[str, Any]) -> dict[str, Any]:
    stable = {k: v for k, v in response.items() if k not in _VOLATILE_KEYS}
    if isinstance(stable.get("learnings"), list):
        stable["learnings"] = [
            {k: v for k, v in entry.items() if k not in _VOLATILE_ENTRY_KEYS} if isinstance(entry, dict) else entry
            for entry in stable["learnings"]
        ]
    return stable


@pytest.mark.skipif(os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions and a non-root user")
def test_unwritable_logs_leave_responses_unchanged(
    trw_dir: Path, monkeypatch: pytest.MonkeyPatch, captured_structlog: list[dict[str, Any]]
) -> None:
    from tests.conftest import extract_tool_fn, make_test_server

    monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.delenv("TRW_SURFACE_ROLE", raising=False)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    server = make_test_server("learning", "before_edit_hint", "build")
    lid = extract_tool_fn(server, "trw_learn")(
        summary="app.py startup must load config first", detail="app.py reads config.", impact=0.7
    )["learning_id"]

    writable = _drive_four_tools(server, lid)
    logs = trw_dir / "logs"
    rows_before = {p.name: p.read_bytes() for p in logs.glob("*.jsonl")}
    assert {"recall_tracking.jsonl", "session_outcomes.jsonl"} <= set(rows_before)
    captured_structlog.clear()
    # Unwritable = no new file can be created AND no existing log can be appended.
    existing = [p for p in logs.iterdir() if p.is_file()]
    for path in existing:
        path.chmod(0o400)
    logs.chmod(0o500)
    try:
        unwritable = _drive_four_tools(server, lid)
    finally:
        logs.chmod(0o700)
        for path in existing:
            path.chmod(0o600)

    for tool, response in writable.items():
        assert set(unwritable[tool]) == set(response), tool
        assert _stable(unwritable[tool]) == _stable(response), tool
    # The new writes really failed (nothing appended) ...
    assert (logs / "recall_tracking.jsonl").read_bytes() == rows_before["recall_tracking.jsonl"]
    assert (logs / "session_outcomes.jsonl").read_bytes() == rows_before["session_outcomes.jsonl"]
    # ... and each was reported as its named debug event.
    by_event = {e.get("event"): e.get("log_level") for e in captured_structlog}
    for event in (
        "recall_record_failed",
        "before_edit_exposure_record_failed",
        "session_observation_record_failed",
        "outcome_record_failed",
    ):
        assert by_event.get(event) == "debug", (event, by_event.get(event))
