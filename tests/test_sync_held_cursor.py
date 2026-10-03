"""SYNC-PUSH-HELD-STALL: rows the backend refused (held) must not stall every newer row.

Observed before the fix on a real SQLite backend (trunk 89c3ac904b): with 1000 held rows ahead of 5 pushable ones ``get_dirty_entries`` returned nothing
after 200 page reads of 1000 rows each (200,000 rows read, 8.05 s), because ``page_dirty`` has no cursor, always returns the oldest 1000 rows and the loop
asked for the same page 200 times; held=1200 gave the same (6.8 s). With 999 held it returned 1 of the 5 (the page is 999 held + 1).

The store here is the product's own SQL: ``DeltaTracker.get_dirty_entries`` over a real ``SQLiteBackend`` -- what the memory daemon's ``memory_sync_dirty_page``
runs; only the daemon transport is left out.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from trw_mcp.sync import _client_runtime as runtime
from trw_memory.models.memory import MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend
from trw_memory.sync.delta import DeltaTracker

NS = "project:held-11111111"
PUSHABLE = 5


class RealStore:
    """page_dirty over a real SQLite backend, recording every read."""

    def __init__(self, backend: SQLiteBackend, *, ignore_cursor: bool = False) -> None:
        self.backend, self.ignore_cursor = backend, ignore_cursor
        self.reads: list[tuple[int, str | None]] = []

    def page_dirty(self, namespace: str, limit: int, cursor: str | None = None) -> list[MemoryEntry]:
        self.reads.append((limit, cursor))
        after = None
        if cursor is not None and not self.ignore_cursor:
            seq, _, row_id = cursor.partition(":")
            after = (int(seq), row_id)
        return DeltaTracker.get_dirty_entries(self.backend, namespace=namespace, limit=limit, after=after)

    def mark_synced(self, namespace: str, pushed: list[MemoryEntry]) -> int:
        return DeltaTracker.mark_synced(
            [e.id for e in pushed], self.backend, namespace=namespace, expected_seq={e.id: e.sync_seq for e in pushed}
        )


def build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held_count: int, **kw: Any
) -> tuple[RealStore, dict[str, dict[str, object]]]:
    backend = SQLiteBackend(tmp_path / "memory.db")
    for i in range(held_count):
        backend.store(MemoryEntry(id=f"H-{i:05d}", namespace=NS, content=f"held row {i}"))
    for i in range(PUSHABLE):
        backend.store(MemoryEntry(id=f"P-{i:05d}", namespace=NS, content=f"pushable row {i}"))
    held = {}
    for i in range(held_count):
        row = backend.get(f"H-{i:05d}", namespace=NS)
        assert row is not None
        held[row.id] = {"sync_seq": row.sync_seq, "reason": "backend refused"}
    store = RealStore(backend, **kw)
    monkeypatch.setattr(runtime._store_selection, "selected_store", lambda _trw_dir: (store, NS))
    return store, held


def cycle(tmp_path: Path, held: dict[str, dict[str, object]]) -> list[MemoryEntry]:
    return runtime.get_dirty_entries(client_id="t", trw_dir=tmp_path, held=held)


@pytest.mark.parametrize("held_count", [1000, 1001, 1500, 2500])
def test_held_rows_filling_the_oldest_pages_do_not_hide_the_rows_behind_them(
    held_count: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, held = build(tmp_path, monkeypatch, held_count)
    with capture_logs() as logs:
        sent = cycle(tmp_path, held)
    assert [e.id for e in sent] == [f"P-{i:05d}" for i in range(PUSHABLE)]
    assert len(store.reads) <= math.ceil(held_count / 1000) + 1, (
        f"{len(store.reads)} page reads for {held_count} held rows"
    )
    assert not any(event["event"] == "sync_dirty_page_pass_cap_reached" for event in logs)
    store.backend.close()


def test_the_next_page_starts_behind_the_last_row_of_the_previous_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, held = build(tmp_path, monkeypatch, 1200)
    cycle(tmp_path, held)
    assert store.reads[0][1] is None
    last_seq = store.backend.get("H-00999", namespace=NS).sync_seq  # type: ignore[union-attr]
    assert store.reads[1][1] == f"{last_seq}:H-00999"
    store.backend.close()


def test_with_999_held_rows_each_cycle_still_makes_progress_in_one_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, held = build(tmp_path, monkeypatch, 999)
    pushed: list[str] = []
    for _ in range(PUSHABLE + 1):
        before = len(store.reads)
        sent = cycle(tmp_path, held)
        assert len(store.reads) - before == 1
        if not sent:
            break
        pushed += [e.id for e in sent]
        store.mark_synced(NS, sent)
    assert pushed == [f"P-{i:05d}" for i in range(PUSHABLE)]
    store.backend.close()


def test_a_page_identical_to_the_previous_one_stops_the_cycle_with_a_logged_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store that ignores the cursor returns the same oldest page again: that is a defect to name, never a loop to repeat 200 times."""
    store, held = build(tmp_path, monkeypatch, 1200, ignore_cursor=True)
    with capture_logs() as logs:
        sent = cycle(tmp_path, held)
    assert sent == []
    assert len(store.reads) == 2
    errors = [event for event in logs if event["event"] == "sync_dirty_page_cursor_ignored"]
    assert errors and errors[0]["log_level"] == "error" and "cursor" in str(errors[0].get("cause", "cursor"))
    store.backend.close()


class ScriptedStore:
    """page_dirty answers from a fixed list of pages, one per read."""

    def __init__(self, pages: list[list[MemoryEntry]]) -> None:
        self.pages, self.reads = pages, []

    def page_dirty(self, namespace: str, limit: int, cursor: str | None = None) -> list[MemoryEntry]:
        self.reads.append(cursor)
        return self.pages[len(self.reads) - 1]

    def mark_synced(self, namespace: str, pushed: list[MemoryEntry]) -> int:
        return len(pushed)


def test_a_held_row_edited_during_the_scan_reappears_and_is_sent_not_mistaken_for_an_ignored_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same ids on two pages is not proof the cursor was ignored: an edit re-stamps a row with a higher sync_seq, so the page keys differ."""

    def row(row_id: str, seq: int) -> MemoryEntry:
        source = "team_sync" if row_id.startswith("team-sync-") else "agent"
        return MemoryEntry(id=row_id, namespace=NS, content=row_id, sync_seq=seq, source=source)

    first = [row("H", 5), row("team-sync-1", 5)]
    second = [row("H", 6), row("team-sync-1", 6)]
    store = ScriptedStore([first, second])
    monkeypatch.setattr(runtime._store_selection, "selected_store", lambda _trw_dir: (store, NS))
    held = {"H": {"sync_seq": 5, "reason": "backend refused"}}
    with capture_logs() as logs:
        sent = runtime.get_dirty_entries(client_id="t", trw_dir=tmp_path, held=held, page_size=1)
    assert [(e.id, e.sync_seq) for e in sent] == [("H", 6)]
    assert not [event for event in logs if event["event"] == "sync_dirty_page_cursor_ignored"]


def test_a_queue_with_nothing_held_still_takes_one_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, held = build(tmp_path, monkeypatch, 0)
    assert len(cycle(tmp_path, held)) == PUSHABLE and len(store.reads) == 1
    store.backend.close()
