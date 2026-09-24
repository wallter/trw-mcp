"""Listing helpers read through the checkout's store and its pinned namespace (PRD-CORE-280 FR01 slice c)."""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.lifecycle.correction import LearningPatch

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state import _memory_lookups, _store_selection

pytestmark = pytest.mark.unit

_PIN = "project:pinned"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> FakeMemoryStore:
    fake = FakeMemoryStore()
    fake.put("Kept", _PIN, {"entry_id": "L-a", "importance": 0.9})
    fake.put("Low", _PIN, {"entry_id": "L-b", "importance": 0.1})
    fake.put("Canary", _PIN, {"entry_id": "L-c", "metadata": {"system_canary": "true"}})
    fake.put("Elsewhere", "default", {"entry_id": "L-d"})
    monkeypatch.setattr(_store_selection, "selected_store", lambda _dir: (fake, _PIN))
    monkeypatch.setattr(_memory_lookups, "passive_learnings_allowed", lambda: True)
    return fake


def test_active_learnings_come_from_the_pinned_namespace(store: FakeMemoryStore, tmp_path: Path) -> None:
    listed = _memory_lookups.list_active_learnings(tmp_path, min_impact=0.5)

    assert [row["id"] for row in listed] == ["L-a"]
    assert [args[:2] for name, args in store.calls if name == "list_entries"] == [(_PIN, "active")]


def test_entries_by_status_and_count_skip_canaries(store: FakeMemoryStore, tmp_path: Path) -> None:
    store.correct("L-b", LearningPatch(status="resolved"))

    assert [row["id"] for row in _memory_lookups.list_entries_by_status(tmp_path, status="resolved")] == ["L-b"]
    assert _memory_lookups.count_entries(tmp_path) == 2
