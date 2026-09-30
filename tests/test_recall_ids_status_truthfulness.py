"""recall(ids=[...]) does not report an existing row as missing just because of its status (INC-119 b)."""

from __future__ import annotations

from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.models.config import get_config
from trw_mcp.tools._recall_impl import recall_by_ids


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    path.mkdir()
    return path


def _put(store: FakeMemoryStore, entry_id: str, status: MemoryStatus) -> None:
    store.rows[("default", entry_id)] = MemoryEntry(
        id=entry_id, content=f"rule for {entry_id}", namespace="default", status=status
    )


def test_missing_ids_explains_that_the_status_filter_may_be_why(
    trw_dir: Path, fake_memory_store: FakeMemoryStore
) -> None:
    _put(fake_memory_store, "L-active", MemoryStatus.ACTIVE)
    _put(fake_memory_store, "L-done", MemoryStatus.RESOLVED)
    result = dict(recall_by_ids(trw_dir, get_config(), ["L-active", "L-done", "L-nope"]))
    assert [row["id"] for row in result["learnings"]] == ["L-active"]
    assert result["missing_ids"] == [
        "L-done",
        "L-nope",
    ]  # FR01: a refused row stays indistinguishable from an absent one
    note = str(result["ids_note"])
    assert "status='resolved'" in note and "status=active" in note
    assert "other_status_ids" not in result  # nothing confirms that L-done exists


def test_asking_for_the_right_status_returns_the_row(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    _put(fake_memory_store, "L-done", MemoryStatus.RESOLVED)
    result = dict(recall_by_ids(trw_dir, get_config(), ["L-done"], status="resolved"))
    assert [row["id"] for row in result["learnings"]] == ["L-done"]
    assert "missing_ids" not in result and "ids_note" not in result


def test_a_truly_missing_id_is_unchanged(trw_dir: Path, fake_memory_store: FakeMemoryStore) -> None:
    result = dict(recall_by_ids(trw_dir, get_config(), ["L-nope"]))
    assert result["missing_ids"] == ["L-nope"] and "ids_note" in result
