"""Tests for recall_tracking module — PRD-CORE-034 outcome-based calibration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._layout import requires_non_root
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


def test_record_outcome_creates_missing_log(trw_dir: Path) -> None:
    """PRD-FIX-144 FR03: feedback given before any recall is still recorded."""
    assert not (trw_dir / _TRACKING_FILE).exists()
    result = record_outcome("L-nonexistent", "positive", source="explicit_feedback")
    assert result is True
    rows = [json.loads(line) for line in (trw_dir / _TRACKING_FILE).read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["outcome"] == "positive"
    assert rows[0]["source"] == "explicit_feedback"
    assert rows[0]["session_id"] and rows[0]["process_session_id"]


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
    # PRD-FIX-144 FR05: only the 4 receipts are recalls; outcome rows are not.
    assert stats["total_recalls"] == 4


def test_get_recall_stats_neutral_outcome(trw_dir: Path) -> None:
    record_recall("L-neutral", "test")
    record_outcome("L-neutral", "neutral")

    stats = get_recall_stats()
    assert stats["neutral_outcomes"] == 1
    assert stats["positive_outcomes"] == 0
    assert stats["negative_outcomes"] == 0


# --- fail-open behavior ---


@requires_non_root
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
        lambda: (_ for _ in ()).throw(RuntimeError("unexpected")),
    )
    stats = get_recall_stats()
    assert stats["total_recalls"] == 0


# --- PRD-FIX-144: joinable receipts, outcome rows, end-to-end session ---


def _rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_receipt_carries_session_and_surface_keys(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR01: session_id, process_session_id and surface on every receipt."""
    from trw_mcp.state._session_id import _get_process_session_id, resolve_effective_session_id

    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    assert record_recall("L-abc", "testing patterns") is True
    assert record_recall("L-def", "file.py", surface="before_edit_hint") is True

    first, second = _rows(trw_dir / _TRACKING_FILE)
    # The four pre-change keys keep their names and values.
    assert first["learning_id"] == "L-abc"
    assert first["query"] == "testing patterns"
    assert first["outcome"] is None
    assert isinstance(first["timestamp"], float)
    assert first["session_id"] == resolve_effective_session_id(trw_dir)
    assert first["process_session_id"] == _get_process_session_id()
    assert first["surface"] == "recall"
    assert "files_context" not in first
    assert second["surface"] == "before_edit_hint"


def test_total_recalls_counts_receipts_only(trw_dir: Path) -> None:
    """FR05: 3 receipts + positive + negative + unknown-valued row -> 3 recalls."""
    path = trw_dir / _TRACKING_FILE
    path.parent.mkdir(parents=True)
    rows = [
        {"learning_id": "L-1", "query": "q", "timestamp": 1.0, "outcome": None},
        {"learning_id": "L-1", "query": "q", "timestamp": 2.0, "outcome": None},
        {"learning_id": "L-1", "query": "q", "timestamp": 3.0, "outcome": ""},
        {"learning_id": "L-1", "outcome": "positive", "timestamp": 4.0},
        {"learning_id": "L-1", "outcome": "negative", "timestamp": 5.0},
        {"learning_id": "L-1", "outcome": "credited_by_build", "timestamp": 6.0},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    stats = get_recall_stats()
    assert stats["total_recalls"] == 3
    assert stats["positive_outcomes"] == 1
    assert stats["negative_outcomes"] == 1


def test_rows_join_on_process_session_id_when_a_run_is_pinned_mid_session(
    trw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A trw_init after a recall changes session_id; process_session_id holds."""
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    record_recall("L-join", "query")
    monkeypatch.setattr("trw_mcp.state._paths.get_pinned_run", lambda: trw_dir.parent / "runs" / "run-pinned")
    record_outcome("L-join", "positive", source="explicit_feedback")

    receipt, outcome = _rows(trw_dir / _TRACKING_FILE)
    assert outcome["session_id"] == "run-pinned"
    assert receipt["session_id"] != outcome["session_id"]
    assert receipt["process_session_id"] == outcome["process_session_id"]


def _memory_fields(trw_dir: Path, learning_id: str) -> dict[str, object]:
    """Read the feedback and Q columns straight from memory.db."""
    # trw_memory (already loaded by the store this test opened) has installed its
    # pysqlite3 shim, so this import resolves to the same driver the store uses.
    import sqlite3

    dbs = sorted(trw_dir.rglob("memory.db"))
    assert dbs, "memory.db not found"
    conn = sqlite3.connect(str(dbs[0]))
    try:
        row = conn.execute(
            # impact is stored as the ``importance`` column.
            "SELECT helpful_count, unhelpful_count, q_value, q_observations, outcome_history, importance "
            "FROM memories WHERE id = ?",
            (learning_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, learning_id
    keys = ("helpful_count", "unhelpful_count", "q_value", "q_observations", "outcome_history", "impact")
    return dict(zip(keys, row, strict=True))


def test_fix_verification_session_end_to_end(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-FIX-144 section 2 Fix Verification, driven through the registered tools."""
    from tests.conftest import extract_tool_fn, make_test_server

    project = trw_dir.parent
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(project))
    monkeypatch.setenv("TRW_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setenv("TRW_DEDUP_ENABLED", "false")
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    (trw_dir / "learnings" / "entries").mkdir(parents=True, exist_ok=True)
    (trw_dir / "context").mkdir(exist_ok=True)
    logs = trw_dir / "logs"
    server = make_test_server("ceremony", "learning", "before_edit_hint", "build")

    def tool(name: str) -> Any:
        return extract_tool_fn(server, name)

    assert tool("trw_session_start")()["success"] is True
    lid_a = tool("trw_learn")(
        summary="app.py startup must load config before routes", detail="app.py reads config first.", impact=0.7
    )["learning_id"]
    lid_b = tool("trw_learn")(
        summary="app.py handlers must not block the event loop", detail="Offload app.py blocking IO.", impact=0.6
    )["learning_id"]
    before = {lid: _memory_fields(trw_dir, lid) for lid in (lid_a, lid_b)}

    tool("trw_recall")(query="app.py")
    hint = tool("trw_before_edit_hint")(file_path="app.py")
    assert {lid_a, lid_b} <= {item["id"] for item in hint["learnings"]}
    tool("trw_build_check")(tests_passed=False, test_count=3, failure_count=1)
    tool("trw_build_check")(tests_passed=True, test_count=3)
    assert tool("trw_learn_update")(learning_id=lid_a, feedback="helpful")["status"] == "updated"
    assert tool("trw_learn_update")(learning_id=lid_b, feedback="unhelpful")["status"] == "updated"
    tool("trw_deliver")()

    tracking = _rows(logs / "recall_tracking.jsonl")
    receipts = [r for r in tracking if r.get("outcome") is None]
    outcomes = [r for r in tracking if r.get("outcome") is not None]
    assert receipts
    # 1. Every receipt is joinable; the hint's receipts carry the edited file.
    assert all(r.get("session_id") and r.get("process_session_id") for r in receipts)
    hint_receipts = [r for r in receipts if r.get("surface") == "before_edit_hint"]
    assert {r["learning_id"] for r in hint_receipts} >= {lid_a, lid_b}
    assert all(r["files_context"] == ["app.py"] and r["query"] == "app.py" for r in hint_receipts)
    # 2. The surface log shows the hint with its file.
    surface = _rows(logs / "surface_tracking.jsonl")
    assert any(r["surface_type"] == "before_edit_hint" and r["files_context"] == ["app.py"] for r in surface)
    # 3. Exactly two outcome rows, both explicit feedback, keyed like the receipts.
    receipt_sessions = {r["session_id"] for r in receipts}
    assert [(r["learning_id"], r["outcome"]) for r in outcomes] == [(lid_a, "positive"), (lid_b, "negative")]
    assert all(r["source"] == "explicit_feedback" and r["session_id"] in receipt_sessions for r in outcomes)
    # 4. Two session observations, in call order, with no learning id.
    observations = _rows(logs / "session_outcomes.jsonl")
    assert [r["tests_passed"] for r in observations] == [False, True]
    assert all("learning_id" not in r for r in observations)
    # 5. Every receipt joins to a session observation (context, not an outcome).
    observed_sessions = {r["session_id"] for r in observations}
    assert all(r["session_id"] in observed_sessions for r in receipts)
    # 6. Counters moved by exactly 1; Q, outcome history and impact untouched.
    after = {lid: _memory_fields(trw_dir, lid) for lid in (lid_a, lid_b)}
    assert after[lid_a]["helpful_count"] == int(str(before[lid_a]["helpful_count"])) + 1
    assert after[lid_b]["unhelpful_count"] == int(str(before[lid_b]["unhelpful_count"])) + 1
    assert after[lid_a]["unhelpful_count"] == before[lid_a]["unhelpful_count"]
    assert after[lid_b]["helpful_count"] == before[lid_b]["helpful_count"]
    for lid in (lid_a, lid_b):
        for field in ("q_value", "q_observations", "outcome_history", "impact"):
            assert after[lid][field] == before[lid][field], (lid, field)
