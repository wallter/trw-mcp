"""The shared recall rule every ``MemoryStore`` feeds (PRD-CORE-294 FR01, PRD-CORE-280 FR01).

``take_hits`` and ``settle_ids`` are the only code that decides which row an id
shows, so these cases hold for the SQLite, daemon and fake stores alike.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.state._recall_admission import RecallAdmission
from trw_mcp.state._recall_take import REPRESENTATIVE_LOOKUP_SLACK, settle_ids, take_hits
from trw_mcp.state._store_selection import RecallSpec

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _spec(**fields: object) -> RecallSpec:
    admission = RecallAdmission.build(Path("/nonexistent-trw"), status="active", as_of=None, include_superseded=False)
    return RecallSpec(admission=admission, **fields)  # type: ignore[arg-type]


def _row(entry_id: str, namespace: str = "default", *, minutes: int = 0, status: str = "active") -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        content=f"row {entry_id}",
        namespace=namespace,
        status=MemoryStatus(status),
        updated_at=_T0 + timedelta(minutes=minutes),
    )


def test_twins_in_one_store_show_and_hydrate_the_newer_row() -> None:
    older, newer = _row("L-1", "user:local", minutes=1), _row("L-1", "team:x", minutes=5)
    held = {"L-1": [older, newer]}
    spec = _spec(query="row")

    shown = take_hits(lambda k: [older, newer][:k], spec, cap=5, seen=set(), rows_for=held.__getitem__)
    found: dict[str, MemoryEntry] = {}
    settle_ids(_spec(ids=("L-1",)), found, held.__getitem__)

    assert [(row.id, row.namespace) for row in shown] == [("L-1", "team:x")]
    assert found["L-1"].namespace == "team:x"


def test_an_id_whose_newer_twin_is_not_a_hit_is_not_shown() -> None:
    """``ids=`` would hydrate the newer twin the query never matched, so search shows no stub for it."""
    older, newer = _row("L-2", "user:local", minutes=1), _row("L-2", "team:x", minutes=5)
    held = {"L-2": [older, newer]}

    shown = take_hits(lambda k: [older][:k], _spec(query="row"), cap=5, seen=set(), rows_for=held.__getitem__)

    assert shown == []


def test_a_valid_row_behind_many_refused_ones_is_found() -> None:
    refused = [_row(f"L-r{index}", status="obsolete") for index in range(10)]
    page = [*refused, _row("L-kept")]
    asked: list[int] = []

    def read(k: int) -> list[MemoryEntry]:
        asked.append(k)
        return page[:k]

    shown = take_hits(read, _spec(query="row", top_k=2), cap=1, seen=set())

    assert [row.id for row in shown] == ["L-kept"]
    assert asked == [2, 8, 32]


def test_a_store_of_refused_rows_stops_within_the_lookup_budget() -> None:
    lookups: list[str] = []

    def rows_for(entry_id: str) -> list[MemoryEntry]:
        lookups.append(entry_id)
        return [_row(entry_id, status="obsolete")]

    def read(k: int) -> list[MemoryEntry]:
        return [_row(f"L-x{index}", status="obsolete") for index in range(k)]

    shown = take_hits(read, _spec(query="row", top_k=4), cap=3, seen=set(), rows_for=rows_for)

    assert shown == []
    assert len(lookups) == 3 + REPRESENTATIVE_LOOKUP_SLACK
