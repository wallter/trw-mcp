"""``trw-mcp export --scope learnings`` reads the store (PRD-CORE-280 FR05).

A row written straight to the store (never mirrored to ``learnings/entries/*.yaml``,
which the retired ``_collect_learnings`` YAML reader used to require) must still
be exported, carrying ``namespace``, ``origin_project`` and ``remote_id``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._memory_store_fake import FakeMemoryStore
from tests._test_export_support import _setup_project, _store_entry
from trw_mcp.export import export_data
from trw_mcp.state import _store_selection
from trw_mcp.state._origin_project import ORIGIN_PROJECT_KEY


@pytest.fixture(autouse=True)
def _route_memory(fake_memory_store: FakeMemoryStore) -> FakeMemoryStore:
    """Export reads ``selected_store``; the fake is this checkout's store (PRD-CORE-280 e1)."""
    return fake_memory_store


def test_a_store_only_row_is_exported_with_no_yaml_mirror(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    entries_dir = project / ".trw" / "learnings" / "entries"

    _store_entry(project / ".trw", summary="Store-only learning", entry_id="L-store1")

    assert list(entries_dir.glob("*.yaml")) == []  # never written to the YAML mirror

    result = export_data(project, "learnings")
    learnings = result.get("learnings")
    assert isinstance(learnings, list)
    assert len(learnings) == 1
    assert learnings[0]["id"] == "L-store1"
    assert learnings[0]["summary"] == "Store-only learning"


def test_exported_row_carries_namespace_origin_project_and_remote_id(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    trw_dir = project / ".trw"
    store, namespace = _store_selection.selected_store(trw_dir)
    store.put(
        "Synced learning",
        namespace,
        {
            "entry_id": "L-synced1",
            "importance": 0.7,
            "metadata": {ORIGIN_PROJECT_KEY: "other-repo"},
        },
    )
    # remote_id is stamped by a sync merge, not by memory_store; apply it directly
    # through the store so the export path is exercised against a realistic row.
    entry = store.get("L-synced1")
    assert entry is not None
    store.apply_synced(namespace, entry.model_copy(update={"remote_id": "R-abc123"}))

    result = export_data(project, "learnings")
    learnings = result.get("learnings")
    assert isinstance(learnings, list)
    row = next(r for r in learnings if r["id"] == "L-synced1")
    assert row["namespace"] == namespace
    assert row["origin_project"] == "other-repo"
    assert row["remote_id"] == "R-abc123"


def test_a_row_with_no_origin_project_metadata_reports_an_empty_string(tmp_path: Path) -> None:
    project = _setup_project(tmp_path)
    _store_entry(project / ".trw", summary="Locally written", entry_id="L-local1")

    result = export_data(project, "learnings")
    learnings = result.get("learnings")
    assert isinstance(learnings, list)
    row = next(r for r in learnings if r["id"] == "L-local1")
    assert row["origin_project"] == ""
    assert row["remote_id"] is None


def test_export_pages_past_the_list_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round 1 P1: a namespace larger than one list call is exported whole, not truncated."""
    project = _setup_project(tmp_path)
    for i in range(5):
        _store_entry(project / ".trw", summary=f"Paged learning {i}", entry_id=f"L-page{i}")
    monkeypatch.setattr("trw_mcp.state._constants.DEFAULT_LIST_LIMIT", 2)

    learnings = export_data(project, "learnings").get("learnings")

    assert isinstance(learnings, list)
    assert sorted(r["id"] for r in learnings) == [f"L-page{i}" for i in range(5)]


def test_export_keeps_non_active_learnings_with_their_status(tmp_path: Path) -> None:
    """Round 1 P1: the YAML export carried every status; only canary decoys are left out."""
    from trw_memory.models.memory import MemoryStatus

    project = _setup_project(tmp_path)
    trw_dir = project / ".trw"
    store, namespace = _store_selection.selected_store(trw_dir)
    _store_entry(trw_dir, summary="Retired learning", entry_id="L-old1")
    entry = store.get("L-old1")
    assert entry is not None
    store.apply_synced(namespace, entry.model_copy(update={"status": MemoryStatus.OBSOLETE}))

    learnings = export_data(project, "learnings").get("learnings")

    assert isinstance(learnings, list)
    assert [(r["id"], r["status"]) for r in learnings] == [("L-old1", "obsolete")]


def test_csv_export_carries_namespace_origin_project_and_remote_id(tmp_path: Path) -> None:
    import csv
    import io

    project = _setup_project(tmp_path)
    trw_dir = project / ".trw"
    store, namespace = _store_selection.selected_store(trw_dir)
    store.put("Synced learning", namespace, {"entry_id": "L-csv1", "metadata": {ORIGIN_PROJECT_KEY: "other-repo"}})
    entry = store.get("L-csv1")
    assert entry is not None
    store.apply_synced(namespace, entry.model_copy(update={"remote_id": "R-csv"}))

    text = export_data(project, "learnings", fmt="csv").get("learnings_csv")

    assert isinstance(text, str)
    row = next(r for r in csv.DictReader(io.StringIO(text)) if r["id"] == "L-csv1")
    assert (row["namespace"], row["origin_project"], row["remote_id"]) == (namespace, "other-repo", "R-csv")
