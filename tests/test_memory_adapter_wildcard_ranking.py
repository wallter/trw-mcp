"""R-RANK-002/004: wildcard recall must rank by impact/utility, not recency.

The wildcard branch of ``recall_learnings`` fetches entries via
``backend.list_entries`` which orders ``updated_at DESC`` only. Pre-fix, that
recency order was returned verbatim, so the NEWEST learning surfaced first even
when an older entry was far higher impact. These tests drive the real
``recall_learnings`` wildcard path with a backend that returns recency-ordered
entries and assert that the RETURNED ORDER is impact-driven.

Each test FAILS against the pre-fix code (which returned list_entries order
unchanged): the high-impact entry is deliberately the OLDEST, so a recency-only
path puts it last.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from trw_memory.models.memory import MemoryEntry, MemoryStatus

from trw_mcp.state.memory_adapter import recall_learnings


@pytest.fixture
def trw_dir(tmp_project: Path) -> Path:
    trw = tmp_project / ".trw"
    (trw / "memory").mkdir(exist_ok=True)
    return trw


def _entry(entry_id: str, impact: float, updated: datetime) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        content=f"content for {entry_id}",
        detail="",
        tags=[],
        importance=impact,
        status=MemoryStatus.ACTIVE,
        created_at=updated,
        updated_at=updated,
    )


def _recency_ordered_entries() -> list[MemoryEntry]:
    """Return entries in updated_at DESC order (what list_entries yields).

    'trivial' is the NEWEST (would be first under recency) but lowest impact;
    'tribal' is the OLDEST but highest impact. A correct ranking returns
    'tribal' first.
    """
    now = datetime.now(timezone.utc)
    return [
        _entry("trivial", impact=0.70, updated=now),  # newest, low impact
        _entry("mid", impact=0.75, updated=now - timedelta(minutes=5)),
        _entry("tribal", impact=0.95, updated=now - timedelta(hours=2)),  # oldest, high impact
    ]


def _patched_recall(backend: MagicMock, trw_dir: Path, **kwargs: object) -> list[dict[str, object]]:
    with (
        patch("trw_mcp.state.memory_adapter.get_backend", return_value=backend),
        patch("trw_mcp.state.memory_adapter.initialize_canaries"),
        patch("trw_mcp.state.memory_adapter.should_halt_recalls", return_value=False),
        patch("trw_mcp.state.memory_adapter.probe_canaries"),
        patch("trw_mcp.state.memory_adapter._memory_recovery_in_progress", return_value=False),
    ):
        return recall_learnings(trw_dir, "*", status="active", **kwargs)  # type: ignore[arg-type]


class TestWildcardRanksByImpact:
    def test_wildcard_returns_highest_impact_first_not_newest(self, trw_dir: Path) -> None:
        backend = MagicMock()
        backend.list_entries.return_value = _recency_ordered_entries()

        results = _patched_recall(backend, trw_dir, min_impact=0.0, max_results=10, compact=True)

        ids = [str(r.get("id")) for r in results]
        assert ids == ["tribal", "mid", "trivial"], (
            "wildcard recall must order by impact/utility, not updated_at DESC "
            f"(got {ids})"
        )

    def test_wildcard_top_result_is_high_impact_entry(self, trw_dir: Path) -> None:
        backend = MagicMock()
        backend.list_entries.return_value = _recency_ordered_entries()

        results = _patched_recall(backend, trw_dir, min_impact=0.0, max_results=10, compact=True)

        assert results, "expected non-empty recall"
        assert str(results[0].get("id")) == "tribal"
        assert float(str(results[0].get("impact"))) == pytest.approx(0.95)

    def test_precondition_backend_returns_recency_order(self, trw_dir: Path) -> None:
        """Sanity: list_entries yields the trivial (newest) entry first.

        Documents the input order the ranking has to overturn — without the
        fix, this same order would be the OUTPUT order.
        """
        entries = _recency_ordered_entries()
        assert [e.id for e in entries] == ["trivial", "mid", "tribal"]
        assert entries[0].importance < entries[-1].importance

    def test_wildcard_ranking_failure_falls_open_to_recency(self, trw_dir: Path) -> None:
        """If ranking raises, recall returns the (recency) order rather than erroring."""
        backend = MagicMock()
        backend.list_entries.return_value = _recency_ordered_entries()

        # Patch rank_by_utility to raise; _rank_wildcard_by_utility must swallow
        # it and return the unranked (recency) order instead of propagating.
        with patch("trw_mcp.scoring.rank_by_utility", side_effect=RuntimeError("rank boom")):
            results = _patched_recall(backend, trw_dir, min_impact=0.0, max_results=10, compact=True)

        ids = [str(r.get("id")) for r in results]
        assert ids == ["trivial", "mid", "tribal"], "fail-open must preserve recency order"
