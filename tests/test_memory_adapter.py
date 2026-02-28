"""Tests for the trw-memory adapter layer (state/memory_adapter.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import get_config
from trw_mcp.state import memory_adapter
from trw_mcp.state.memory_adapter import (
    count_entries,
    ensure_migrated,
    find_entry_by_id,
    find_yaml_path_for_entry,
    get_backend,
    list_active_learnings,
    list_entries_by_status,
    recall_learnings,
    reset_backend,
    store_learning,
    update_access_tracking,
    update_learning,
)
from trw_mcp.state.persistence import FileStateWriter


@pytest.fixture(autouse=True)
def _isolate_backend() -> None:  # type: ignore[misc]
    """Reset the module-level backend singleton between tests."""
    reset_backend()
    yield  # type: ignore[misc]
    reset_backend()


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    """Create a minimal .trw structure for adapter tests."""
    d = tmp_path / ".trw"
    d.mkdir()
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir()
    return d


@pytest.fixture
def trw_dir_with_entries(trw_dir: Path) -> Path:
    """Create a .trw structure with sample YAML learning entries."""
    entries_dir = trw_dir / "learnings" / "entries"
    writer = FileStateWriter()
    writer.write_yaml(entries_dir / "2026-01-01-test-learning.yaml", {
        "id": "L-test0001",
        "summary": "Test learning about Python",
        "detail": "Python is a great language",
        "tags": ["python", "testing"],
        "evidence": [],
        "impact": 0.8,
        "status": "active",
        "source_type": "agent",
        "source_identity": "test",
        "created": "2026-01-01",
        "updated": "2026-01-01",
        "access_count": 0,
        "q_value": 0.5,
        "q_observations": 0,
        "recurrence": 1,
    })
    writer.write_yaml(entries_dir / "2026-01-02-second-learning.yaml", {
        "id": "L-test0002",
        "summary": "Testing gotcha with mocking",
        "detail": "Always patch at the import site",
        "tags": ["testing", "gotcha"],
        "evidence": ["test_foo.py"],
        "impact": 0.6,
        "status": "active",
        "source_type": "human",
        "source_identity": "Tyler",
        "created": "2026-01-02",
        "updated": "2026-01-02",
        "access_count": 3,
        "q_value": 0.7,
        "q_observations": 2,
        "recurrence": 2,
    })
    writer.write_yaml(entries_dir / "2026-01-03-obsolete-entry.yaml", {
        "id": "L-test0003",
        "summary": "Obsolete learning",
        "detail": "No longer relevant",
        "tags": ["old"],
        "evidence": [],
        "impact": 0.4,
        "status": "obsolete",
        "source_type": "agent",
        "source_identity": "",
        "created": "2026-01-03",
        "updated": "2026-01-03",
        "access_count": 0,
        "q_value": 0.3,
        "q_observations": 0,
        "recurrence": 1,
    })
    return trw_dir


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

class TestEnsureMigrated:
    def test_migrates_yaml_entries(self, trw_dir_with_entries: Path) -> None:
        """YAML entries are migrated to SQLite on first call."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        db_path = trw_dir_with_entries / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            result = ensure_migrated(trw_dir_with_entries, backend)
            assert result["migrated"] == 3
            assert result["skipped"] == 0
            assert backend.count() == 3
        finally:
            backend.close()

    def test_sentinel_prevents_remigration(self, trw_dir_with_entries: Path) -> None:
        """Second call is a no-op when sentinel exists."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        db_path = trw_dir_with_entries / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            ensure_migrated(trw_dir_with_entries, backend)
            result = ensure_migrated(trw_dir_with_entries, backend)
            assert result == {"migrated": 0, "skipped": 0}
        finally:
            backend.close()

    def test_fresh_project_writes_sentinel(self, trw_dir: Path) -> None:
        """Empty entries dir still writes sentinel."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        # Remove entries dir to simulate fresh project
        entries_dir = trw_dir / "learnings" / "entries"
        for f in entries_dir.glob("*"):
            f.unlink()
        entries_dir.rmdir()

        db_path = trw_dir / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            result = ensure_migrated(trw_dir, backend)
            assert result == {"migrated": 0, "skipped": 0}
            assert (trw_dir / "memory" / ".migrated").exists()
        finally:
            backend.close()

    def test_field_mapping_correctness(self, trw_dir_with_entries: Path) -> None:
        """Migrated entries map fields correctly (summary→content, impact→importance)."""
        from trw_memory.storage.sqlite_backend import SQLiteBackend

        db_path = trw_dir_with_entries / "memory" / "memory.db"
        backend = SQLiteBackend(db_path)
        try:
            ensure_migrated(trw_dir_with_entries, backend)
            entry = backend.get("L-test0001")
            assert entry is not None
            assert entry.content == "Test learning about Python"
            assert entry.importance == 0.8
            assert "python" in entry.tags
        finally:
            backend.close()


# ---------------------------------------------------------------------------
# get_backend tests
# ---------------------------------------------------------------------------

class TestGetBackend:
    def test_singleton_returns_same_instance(self, trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PROJECT_ROOT", str(trw_dir.parent))
        b1 = get_backend(trw_dir)
        b2 = get_backend(trw_dir)
        assert b1 is b2

    def test_auto_migrates_on_first_access(self, trw_dir_with_entries: Path) -> None:
        backend = get_backend(trw_dir_with_entries)
        assert backend.count() == 3


# ---------------------------------------------------------------------------
# store_learning tests
# ---------------------------------------------------------------------------

class TestStoreLearning:
    def test_basic_store(self, trw_dir: Path) -> None:
        result = store_learning(
            trw_dir, "L-new001", "Test summary", "Test detail",
            tags=["test"], impact=0.7,
        )
        assert result["learning_id"] == "L-new001"
        assert result["status"] == "recorded"
        assert "path" in result
        assert "distribution_warning" in result

    def test_return_shape_keys(self, trw_dir: Path) -> None:
        """Return dict must have exact key set for API compatibility."""
        result = store_learning(
            trw_dir, "L-shape01", "s", "d",
        )
        expected_keys = {"learning_id", "path", "status", "distribution_warning"}
        assert set(result.keys()) == expected_keys

    def test_shard_id_stored_in_metadata(self, trw_dir: Path) -> None:
        store_learning(
            trw_dir, "L-shard01", "s", "d", shard_id="shard-A",
        )
        entry = find_entry_by_id(trw_dir, "L-shard01")
        assert entry is not None
        assert entry["shard_id"] == "shard-A"


# ---------------------------------------------------------------------------
# recall_learnings tests
# ---------------------------------------------------------------------------

class TestRecallLearnings:
    def test_wildcard_returns_all(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-r1", "Alpha learning", "d1")
        store_learning(trw_dir, "L-r2", "Beta learning", "d2")
        results = recall_learnings(trw_dir, "*")
        assert len(results) == 2

    def test_keyword_search(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-k1", "Python gotcha", "patching issue")
        store_learning(trw_dir, "L-k2", "Rust memory", "ownership rules")
        results = recall_learnings(trw_dir, "Python")
        assert len(results) >= 1
        assert any(r["id"] == "L-k1" for r in results)

    def test_min_impact_filter(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-i1", "Low impact", "d", impact=0.3)
        store_learning(trw_dir, "L-i2", "High impact", "d", impact=0.9)
        results = recall_learnings(trw_dir, "*", min_impact=0.7)
        assert len(results) == 1
        assert results[0]["id"] == "L-i2"

    def test_tag_filter_on_wildcard(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-t1", "s1", "d", tags=["python"])
        store_learning(trw_dir, "L-t2", "s2", "d", tags=["rust"])
        results = recall_learnings(trw_dir, "*", tags=["python"])
        assert len(results) == 1
        assert results[0]["id"] == "L-t1"

    def test_compact_mode(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-c1", "Summary", "Detail")
        results = recall_learnings(trw_dir, "*", compact=True)
        assert len(results) == 1
        result = results[0]
        # Compact should have minimal keys
        assert "id" in result
        assert "summary" in result
        assert "impact" in result
        assert "detail" not in result

    def test_return_shape_keys(self, trw_dir: Path) -> None:
        """Recalled entries have the expected learning dict keys."""
        store_learning(trw_dir, "L-rs1", "s", "d", tags=["t"], evidence=["e"])
        results = recall_learnings(trw_dir, "*", compact=False)
        assert len(results) == 1
        entry = results[0]
        expected_keys = {
            "id", "summary", "tags", "impact", "status",
            "detail", "evidence", "source_type", "source_identity",
            "created", "updated", "access_count", "last_accessed_at",
            "q_value", "q_observations", "recurrence", "shard_id",
        }
        assert expected_keys <= set(entry.keys())


# ---------------------------------------------------------------------------
# update_learning tests
# ---------------------------------------------------------------------------

class TestUpdateLearning:
    def test_status_change(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u1", "s", "d")
        result = update_learning(trw_dir, "L-u1", status="resolved")
        assert result["status"] == "updated"
        assert "status→resolved" in result["changes"]

    def test_impact_change(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u2", "s", "d", impact=0.5)
        result = update_learning(trw_dir, "L-u2", impact=0.9)
        assert result["status"] == "updated"
        entry = find_entry_by_id(trw_dir, "L-u2")
        assert entry is not None
        assert entry["impact"] == 0.9

    def test_not_found(self, trw_dir: Path) -> None:
        result = update_learning(trw_dir, "L-nonexistent")
        assert result["status"] == "not_found"

    def test_invalid_status(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u3", "s", "d")
        result = update_learning(trw_dir, "L-u3", status="bad")
        assert result["status"] == "invalid"

    def test_no_changes(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-u4", "s", "d")
        result = update_learning(trw_dir, "L-u4")
        assert result["status"] == "no_changes"

    def test_return_shape_keys(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-us1", "s", "d")
        result = update_learning(trw_dir, "L-us1", status="resolved")
        expected_keys = {"learning_id", "changes", "status"}
        assert set(result.keys()) == expected_keys


# ---------------------------------------------------------------------------
# Access tracking tests
# ---------------------------------------------------------------------------

class TestAccessTracking:
    def test_increments_count(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-at1", "s", "d")
        update_access_tracking(trw_dir, ["L-at1"])
        entry = find_entry_by_id(trw_dir, "L-at1")
        assert entry is not None
        assert entry["access_count"] == 1

    def test_nonexistent_id_no_error(self, trw_dir: Path) -> None:
        """Access tracking for missing ID should not raise."""
        update_access_tracking(trw_dir, ["L-missing"])


# ---------------------------------------------------------------------------
# Utility tests
# ---------------------------------------------------------------------------

class TestUtilities:
    def test_count_entries(self, trw_dir: Path) -> None:
        assert count_entries(trw_dir) == 0
        store_learning(trw_dir, "L-cnt1", "s", "d")
        assert count_entries(trw_dir) == 1

    def test_list_active_learnings(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-la1", "active entry", "d", impact=0.8)
        store_learning(trw_dir, "L-la2", "another", "d", impact=0.3)
        update_learning(trw_dir, "L-la2", status="obsolete")
        actives = list_active_learnings(trw_dir, min_impact=0.5)
        assert len(actives) == 1
        assert actives[0]["id"] == "L-la1"

    def test_find_entry_by_id_found(self, trw_dir: Path) -> None:
        store_learning(trw_dir, "L-fe1", "Summary here", "Detail here")
        result = find_entry_by_id(trw_dir, "L-fe1")
        assert result is not None
        assert result["summary"] == "Summary here"

    def test_find_entry_by_id_not_found(self, trw_dir: Path) -> None:
        assert find_entry_by_id(trw_dir, "L-nope") is None


# ===========================================================================
# PRD-FIX-033: Deliver Performance — SQLite Migration Tests
# ===========================================================================


class TestListEntriesByStatus:
    """PRD-FIX-033-FR01: list_entries_by_status returns entries as dicts."""

    def test_active_filter(self, trw_dir: Path) -> None:
        """Returns only active entries by default."""
        store_learning(trw_dir, "L-les1", "Summary1", "Detail1", impact=0.8)
        store_learning(trw_dir, "L-les2", "Summary2", "Detail2", impact=0.5)
        update_learning(trw_dir, "L-les2", status="obsolete")

        result = list_entries_by_status(trw_dir, status="active")
        ids = [str(e["id"]) for e in result]
        assert "L-les1" in ids
        assert "L-les2" not in ids

    def test_min_impact_filter(self, trw_dir: Path) -> None:
        """Filters by minimum impact score."""
        store_learning(trw_dir, "L-mi1", "High", "d", impact=0.9)
        store_learning(trw_dir, "L-mi2", "Low", "d", impact=0.1)

        result = list_entries_by_status(trw_dir, min_impact=0.5)
        ids = [str(e["id"]) for e in result]
        assert "L-mi1" in ids
        assert "L-mi2" not in ids

    def test_invalid_status_returns_empty(self, trw_dir: Path) -> None:
        """Invalid status string returns empty list."""
        result = list_entries_by_status(trw_dir, status="bogus_status")
        assert result == []

    def test_dict_fields_present(self, trw_dir: Path) -> None:
        """Returned dicts contain expected learning fields."""
        store_learning(trw_dir, "L-df1", "Summary", "Detail", impact=0.5)
        result = list_entries_by_status(trw_dir)
        assert len(result) >= 1
        entry = result[0]
        assert "id" in entry
        assert "summary" in entry
        assert "impact" in entry
        assert "status" in entry

    def test_resolved_status(self, trw_dir: Path) -> None:
        """Can filter by resolved status."""
        store_learning(trw_dir, "L-rs1", "Will resolve", "d", impact=0.5)
        update_learning(trw_dir, "L-rs1", status="resolved")

        result = list_entries_by_status(trw_dir, status="resolved")
        ids = [str(e["id"]) for e in result]
        assert "L-rs1" in ids


class TestFindYamlPathForEntry:
    """PRD-FIX-033-FR05: find_yaml_path_for_entry resolves YAML paths."""

    def test_finds_existing_yaml(self, trw_dir: Path) -> None:
        """Finds YAML file when it exists with sanitized name."""
        from trw_mcp.state.persistence import FileStateWriter

        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        yaml_path = entries_dir / "L-test1.yaml"
        writer.write_yaml(yaml_path, {"id": "L-test1", "summary": "test"})

        result = find_yaml_path_for_entry(trw_dir, "L-test1")
        assert result is not None
        assert result.name == "L-test1.yaml"

    def test_missing_entry_returns_none(self, trw_dir: Path) -> None:
        """Returns None when entry does not exist."""
        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        result = find_yaml_path_for_entry(trw_dir, "L-nonexistent")
        assert result is None

    def test_no_entries_dir_returns_none(self, trw_dir: Path) -> None:
        """Returns None when entries directory does not exist."""
        result = find_yaml_path_for_entry(trw_dir, "L-any")
        assert result is None

    def test_date_prefixed_filename(self, trw_dir: Path) -> None:
        """Finds YAML file with date-prefixed filename containing entry ID."""
        from trw_mcp.state.persistence import FileStateWriter

        cfg_obj = get_config()
        entries_dir = trw_dir / cfg_obj.learnings_dir / cfg_obj.entries_dir
        entries_dir.mkdir(parents=True, exist_ok=True)

        writer = FileStateWriter()
        yaml_path = entries_dir / "2026-02-01-L-dated1-summary-words.yaml"
        writer.write_yaml(yaml_path, {"id": "L-dated1", "summary": "test"})

        result = find_yaml_path_for_entry(trw_dir, "L-dated1")
        assert result is not None
        assert "L-dated1" in result.stem
