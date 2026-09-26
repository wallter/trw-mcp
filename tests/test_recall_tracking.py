"""Tests for recall_tracking module — PRD-CORE-034 outcome-based calibration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests._layout import requires_non_root
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state.recall_tracking import (
    _TRACKING_FILE,
    record_recall,
)


@pytest.fixture
def fake_memory_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    """Override of ``tests._memory_fixtures.fake_memory_store`` pinned to "default".

    See ``tests/test_tools_learning_recall_modes.py`` for the same workaround:
    ``store_learning`` writes under the shared fixture's ``FAKE_NAMESPACE``,
    which ``FakeMemoryStore.recall()`` (only searches "default") never sees.
    """
    from trw_mcp.state import _store_selection

    store = FakeMemoryStore()
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, "default"))
    return store


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


def _memory_fields(store: FakeMemoryStore, learning_id: str) -> dict[str, object]:
    """Read the feedback and Q fields straight off the fake store's row."""
    entry = next(e for (_ns, eid), e in store.rows.items() if eid == learning_id)
    return {"outcome_history": list(entry.outcome_history), "impact": entry.importance}


def test_fix_verification_session_end_to_end(
    trw_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_memory_store: FakeMemoryStore
) -> None:
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
    server = make_test_server("ceremony", "learning", "code", "build")

    def tool(name: str) -> Any:
        return extract_tool_fn(server, name)

    assert tool("trw_session_start")()["success"] is True
    lid_a = tool("trw_learn")(
        summary="app.py startup must load config before routes", detail="app.py reads config first.", impact=0.7
    )["learning_id"]
    lid_b = tool("trw_learn")(
        summary="app.py handlers must not block the event loop", detail="Offload app.py blocking IO.", impact=0.6
    )["learning_id"]
    before = {lid: _memory_fields(fake_memory_store, lid) for lid in (lid_a, lid_b)}

    tool("trw_recall")(query="app.py")
    hint = tool("trw_code")(mode="hint", files="app.py")["hints"][0]
    assert {lid_a, lid_b} <= {item["id"] for item in hint["learnings"]}
    tool("trw_build_check")(tests_passed=False, test_count=3, failure_count=1)
    tool("trw_build_check")(tests_passed=True, test_count=3)
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
    # 3. PRD-CORE-293: no writer produces an outcome row anymore (explicit
    # feedback and the outcome-correlation deferred step are both retired).
    assert outcomes == []
    # 4. Two session observations, in call order, with no learning id.
    observations = _rows(logs / "session_outcomes.jsonl")
    assert [r["tests_passed"] for r in observations] == [False, True]
    assert all("learning_id" not in r for r in observations)
    # 5. Every receipt joins to a session observation (context, not an outcome).
    observed_sessions = {r["session_id"] for r in observations}
    assert all(r["session_id"] in observed_sessions for r in receipts)
    # 6. No writer moves the feedback/Q counters; impact and outcome history untouched.
    after = {lid: _memory_fields(fake_memory_store, lid) for lid in (lid_a, lid_b)}
    for lid in (lid_a, lid_b):
        assert after[lid] == before[lid], lid
    for lid in (lid_a, lid_b):
        for field in ("outcome_history", "impact"):
            assert after[lid][field] == before[lid][field], (lid, field)
