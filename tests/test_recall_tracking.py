"""Tests for recall_tracking module — PRD-CORE-034 outcome-based calibration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trw_mcp.state.recall_tracking import (
    _TRACKING_FILE,
    get_recall_stats,
    record_outcome,
    record_recall,
)


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Set up a temp .trw directory and patch resolve_trw_dir."""
    d = tmp_path / ".trw"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def patch_trw_dir(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect resolve_trw_dir to the temp directory."""
    monkeypatch.setattr(
        "trw_mcp.state.recall_tracking.resolve_trw_dir",
        lambda: trw_dir,
    )


# --- record_recall ---


def test_record_recall_creates_entry(trw_dir: Path) -> None:
    result = record_recall("L-abc123", "testing patterns")
    assert result is True

    tracking_path = trw_dir / _TRACKING_FILE
    assert tracking_path.exists()

    lines = tracking_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["learning_id"] == "L-abc123"
    assert record["query"] == "testing patterns"
    assert record["outcome"] is None
    assert isinstance(record["timestamp"], float)


def test_record_recall_creates_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_recall works even when the logs/ directory doesn't yet exist."""
    new_trw = tmp_path / "fresh_trw"
    new_trw.mkdir()
    monkeypatch.setattr(
        "trw_mcp.state.recall_tracking.resolve_trw_dir",
        lambda: new_trw,
    )
    result = record_recall("L-xyz", "query here")
    assert result is True
    assert (new_trw / _TRACKING_FILE).exists()


def test_record_recall_multiple_entries(trw_dir: Path) -> None:
    record_recall("L-001", "query one")
    record_recall("L-002", "query two")

    tracking_path = trw_dir / _TRACKING_FILE
    lines = tracking_path.read_text().strip().splitlines()
    assert len(lines) == 2


# --- record_outcome ---


def test_record_outcome_appends_entry(trw_dir: Path) -> None:
    # First create the file via record_recall
    record_recall("L-abc123", "test query")

    result = record_outcome("L-abc123", "positive")
    assert result is True

    tracking_path = trw_dir / _TRACKING_FILE
    lines = tracking_path.read_text().strip().splitlines()
    assert len(lines) == 2
    outcome_record = json.loads(lines[1])
    assert outcome_record["learning_id"] == "L-abc123"
    assert outcome_record["outcome"] == "positive"


def test_record_outcome_returns_false_when_no_file(trw_dir: Path) -> None:
    result = record_outcome("L-nonexistent", "positive")
    assert result is False


def test_record_outcome_negative(trw_dir: Path) -> None:
    record_recall("L-test", "query")
    result = record_outcome("L-test", "negative")
    assert result is True


def test_record_outcome_neutral(trw_dir: Path) -> None:
    record_recall("L-test", "query")
    result = record_outcome("L-test", "neutral")
    assert result is True


# --- get_recall_stats ---


def test_get_recall_stats_empty_when_no_file(trw_dir: Path) -> None:
    stats = get_recall_stats()
    assert stats["total_recalls"] == 0
    assert stats["unique_learnings"] == 0
    assert stats["positive_outcomes"] == 0
    assert stats["negative_outcomes"] == 0
    assert stats["neutral_outcomes"] == 0


def test_get_recall_stats_with_data(trw_dir: Path) -> None:
    # 3 recalls for 2 unique learnings
    record_recall("L-001", "query one")
    record_recall("L-001", "query one again")
    record_recall("L-002", "query two")

    # 2 positive, 1 negative outcome events
    record_recall("L-001", "q")  # need file to exist before outcome
    record_outcome("L-001", "positive")
    record_outcome("L-001", "positive")
    record_outcome("L-002", "negative")

    stats = get_recall_stats()
    assert stats["unique_learnings"] == 2
    assert stats["positive_outcomes"] == 2
    assert stats["negative_outcomes"] == 1
    assert stats["neutral_outcomes"] == 0
    # total = 4 recall entries + 3 outcome entries = 7
    assert stats["total_recalls"] == 7


def test_get_recall_stats_neutral_outcome(trw_dir: Path) -> None:
    record_recall("L-neutral", "test")
    record_outcome("L-neutral", "neutral")

    stats = get_recall_stats()
    assert stats["neutral_outcomes"] == 1
    assert stats["positive_outcomes"] == 0
    assert stats["negative_outcomes"] == 0


# --- fail-open behavior ---


def test_record_recall_fail_open(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """record_recall must not raise on write error."""

    monkeypatch.setattr(
        "trw_mcp.state.recall_tracking.resolve_trw_dir",
        lambda: Path("/nonexistent/path/that/cannot/be/created"),
    )
    # Should return False, not raise
    result = record_recall("L-fail", "query")
    assert result is False


def test_get_recall_stats_fail_open(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """get_recall_stats must not raise on unexpected errors."""
    monkeypatch.setattr(
        "trw_mcp.state.recall_tracking.resolve_trw_dir",
        lambda: (_ for _ in ()).throw(RuntimeError("unexpected")),  # type: ignore[arg-type]
    )
    stats = get_recall_stats()
    assert stats["total_recalls"] == 0
