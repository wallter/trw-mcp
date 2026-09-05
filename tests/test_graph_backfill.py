"""F5 root-cause B — tests for the forced knowledge-graph backfill.

``backfill_graph`` loops over the EXISTING corpus and builds edges for entries
that were never graphed (the historical state where ``memory_graph_edges`` was
empty for the whole project). Tests prove:

- the backfill builds a MATERIALISED edge on a pre-existing un-graphed corpus,
  on the singleton connection (same DB the MCP reads);
- it is idempotent — once the sweep has read the corpus through, later runs
  process nothing;
- a bounded run resumes where it stopped instead of re-reading from the top;
- the deadline budget short-circuits processing;
- per-entry failures are fail-open (counted, never raised).

The corpus these tests sweep is joined by consolidation lineage, not shared
tags. PRD-CORE-245 FR07 stopped materialising ``tag_cooccurrence`` edges — the
relation is derived from ``memory_tags`` at query time — so tag overlap is no
longer something a backfill can build, and a test that asked it to would be
asserting the deleted design.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry

from trw_mcp.state.memory_adapter import backfill_graph, get_backend


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir(parents=True)
    return d


def _conn(trw_dir: Path) -> sqlite3.Connection:
    conn = get_backend(trw_dir)._conn
    assert isinstance(conn, sqlite3.Connection)
    return conn


def _count_edges(trw_dir: Path, edge_type: str | None = None) -> int:
    if edge_type is None:
        return int(_conn(trw_dir).execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0])
    return int(
        _conn(trw_dir)
        .execute("SELECT COUNT(*) FROM memory_graph_edges WHERE edge_type = ?", (edge_type,))
        .fetchone()[0]
    )


def _sweep_state(trw_dir: Path) -> dict[str, object]:
    raw = json.loads((trw_dir / "memory" / "graph-backfill.json").read_text(encoding="utf-8"))
    namespaces = raw["namespaces"]
    assert isinstance(namespaces, dict)
    state = namespaces["default"]
    assert isinstance(state, dict)
    return state


def _store_ungraphed_corpus(trw_dir: Path) -> None:
    """Write a consolidation-linked corpus straight to the store, ungraphed.

    ``backend.store`` is the durable write WITHOUT the MCP store path's graph
    enrichment, which is exactly the historical state the backfill exists for:
    rows in the store that no ``update_entry_graph`` call ever saw. Entry 2 and
    entry 3 name entry 1 as a consolidation source, so a swept entry has one
    materialised edge to build and it is deterministic — no embedder, no
    similarity threshold.
    """
    backend = get_backend(trw_dir)
    base = datetime(2026, 9, 3, tzinfo=timezone.utc)
    backend.store(
        MemoryEntry(id="L-bf-1", content="Postgres connection pooling tuning", created_at=base, updated_at=base)
    )
    for index in (2, 3):
        moment = base + timedelta(minutes=index)
        backend.store(
            MemoryEntry(
                id=f"L-bf-{index}",
                content=f"Postgres note {index}",
                created_at=moment,
                updated_at=moment,
                consolidated_from=["L-bf-1"],
            )
        )


def test_backfill_graph_builds_edges_on_existing_corpus(trw_dir: Path) -> None:
    _store_ungraphed_corpus(trw_dir)
    assert _count_edges(trw_dir) == 0  # un-graphed, exactly like the historical bug

    result = backfill_graph(trw_dir)

    assert result["processed"] == 3
    assert result["edges_built"] == 2
    assert _count_edges(trw_dir, "consolidation") == 2


def test_backfill_graph_is_idempotent(trw_dir: Path) -> None:
    _store_ungraphed_corpus(trw_dir)
    first = backfill_graph(trw_dir)
    assert first["processed"] == 3
    assert _sweep_state(trw_dir)["complete"] is True

    # The corpus has been read through, so a second run does not re-enrich it —
    # before the durable cursor this reprocessed all three entries on every
    # deliver, forever, because a tag-only entry never becomes an edge source.
    second = backfill_graph(trw_dir)
    assert second["processed"] == 0
    assert second["edges_built"] == 0
    assert _count_edges(trw_dir, "consolidation") == 2


def test_backfill_graph_resumes_after_a_bounded_run(trw_dir: Path) -> None:
    """A bounded pass advances the cursor; the next call takes the NEXT entry."""
    _store_ungraphed_corpus(trw_dir)

    first = backfill_graph(trw_dir, limit=1)
    assert first["processed"] == 1
    assert _sweep_state(trw_dir)["complete"] is False
    cursor_after_first = _sweep_state(trw_dir)["entry_id"]

    second = backfill_graph(trw_dir, limit=1)
    assert second["processed"] == 1
    assert _sweep_state(trw_dir)["entry_id"] != cursor_after_first

    third = backfill_graph(trw_dir, limit=1)
    assert third["processed"] == 1
    # Three entries, three bounded passes, each entry enriched exactly once.
    assert _count_edges(trw_dir, "consolidation") == 2


def test_backfill_graph_respects_deadline(trw_dir: Path) -> None:
    _store_ungraphed_corpus(trw_dir)

    # A zero deadline budget short-circuits before processing any entry.
    result = backfill_graph(trw_dir, deadline_seconds=0.0)
    assert result["processed"] == 0
    assert result["edges_built"] == 0
    # ... and must NOT record a swept-through corpus it never read.
    assert _sweep_state(trw_dir)["complete"] is False
    assert _sweep_state(trw_dir)["entry_id"] is None


def test_backfill_graph_fail_open_on_entry_error(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_ungraphed_corpus(trw_dir)

    def boom(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("graph enrichment exploded")

    monkeypatch.setattr("trw_mcp.state._graph_backfill.update_entry_graph", boom)

    # Must not raise; failures are counted.
    result = backfill_graph(trw_dir)
    assert result["failed"] == 3
    assert result["edges_built"] == 0


def test_backfill_graph_no_sqlite_connection_returns_zero(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoConnBackend:
        _conn = None

    monkeypatch.setattr("trw_mcp.state._graph_backfill.get_backend", lambda _td: _NoConnBackend())
    result = backfill_graph(trw_dir)
    assert result == {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}
