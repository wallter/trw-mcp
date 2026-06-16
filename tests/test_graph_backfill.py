"""F5 root-cause B — tests for the forced knowledge-graph backfill.

``backfill_graph`` loops over the EXISTING corpus and builds edges for entries
that were never graphed (the historical state where ``memory_graph_edges`` was
empty for the whole project). Tests prove:

- the backfill builds edges on a pre-existing un-graphed corpus, on the
  singleton connection (same DB the MCP reads);
- it is idempotent — a second run processes nothing new;
- the deadline budget short-circuits processing;
- per-entry failures are fail-open (counted, never raised).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trw_mcp.state.memory_adapter import backfill_graph, get_backend, store_learning


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".trw"
    (d / "learnings" / "entries").mkdir(parents=True)
    (d / "memory").mkdir(parents=True)
    return d


def _count_edges(trw_dir: Path) -> int:
    backend = get_backend(trw_dir)
    conn = backend._conn
    assert isinstance(conn, sqlite3.Connection)
    return int(conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0])


def _wipe_edges(trw_dir: Path) -> None:
    """Delete all edges so we simulate the historical un-graphed corpus."""
    backend = get_backend(trw_dir)
    conn = backend._conn
    assert isinstance(conn, sqlite3.Connection)
    conn.execute("DELETE FROM memory_graph_edges")
    conn.commit()


def _store_tag_sharing_corpus(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Store two tag-sharing entries WITHOUT building edges (simulate old data)."""
    monkeypatch.setattr(
        "trw_mcp.state.memory_adapter.update_entry_graph",
        lambda *a, **k: {"similarity_edges": 0, "tag_edges": 0, "consolidation_edges": 0},
    )
    store_learning(
        trw_dir,
        "L-bf-1",
        "Postgres connection pooling tuning",
        "Notes on pool size",
        tags=["postgres", "performance", "database"],
    )
    store_learning(
        trw_dir,
        "L-bf-2",
        "Postgres index strategy for performance",
        "Composite indexes",
        tags=["postgres", "performance", "indexing"],
    )
    monkeypatch.undo()


def test_backfill_graph_builds_edges_on_existing_corpus(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_tag_sharing_corpus(trw_dir, monkeypatch)
    assert _count_edges(trw_dir) == 0  # un-graphed, exactly like the historical bug

    result = backfill_graph(trw_dir)

    assert result["processed"] >= 1
    assert result["edges_built"] > 0
    assert _count_edges(trw_dir) > 0


def test_backfill_graph_is_idempotent(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_tag_sharing_corpus(trw_dir, monkeypatch)
    first = backfill_graph(trw_dir)
    assert first["processed"] >= 1

    # Second run: every entry is now an edge source → nothing reprocessed.
    second = backfill_graph(trw_dir)
    assert second["processed"] == 0
    assert second["edges_built"] == 0
    assert second["skipped"] >= 2


def test_backfill_graph_respects_deadline(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_tag_sharing_corpus(trw_dir, monkeypatch)
    _wipe_edges(trw_dir)

    # A zero deadline budget short-circuits before processing any entry.
    result = backfill_graph(trw_dir, deadline_seconds=0.0)
    assert result["processed"] == 0
    assert result["edges_built"] == 0


def test_backfill_graph_fail_open_on_entry_error(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _store_tag_sharing_corpus(trw_dir, monkeypatch)
    _wipe_edges(trw_dir)

    def boom(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("graph enrichment exploded")

    monkeypatch.setattr("trw_mcp.state._graph_backfill.update_entry_graph", boom)

    # Must not raise; failures are counted.
    result = backfill_graph(trw_dir)
    assert result["failed"] >= 1
    assert result["edges_built"] == 0


def test_backfill_graph_no_sqlite_connection_returns_zero(trw_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoConnBackend:
        _conn = None

    monkeypatch.setattr("trw_mcp.state._graph_backfill.get_backend", lambda _td: _NoConnBackend())
    result = backfill_graph(trw_dir)
    assert result == {"processed": 0, "edges_built": 0, "skipped": 0, "failed": 0}
