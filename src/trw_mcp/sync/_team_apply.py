"""Merging a pulled page into the store: plan every row, apply the page in batches (SYNC-APPLY-BATCH). Belongs to :mod:`trw_mcp.sync.pull`.

``merge_team_learnings`` wrote each pulled row with its own daemon call (about 32 ms a round trip: 200 rows were 6.5 s, holding the
daemon's write lane against recall). This module reads and plans the whole page first, then writes it in chunks of
``APPLY_CHUNK`` rows with one ``apply_synced_many`` call each. Every row still carries its own ``if_revision`` and gets its own write-gate
verdict, so a row that came back ``conflict`` is re-read and re-merged one at a time exactly as before (``CONFLICT_ATTEMPTS``). A store without
the batched call (a daemon from before it) or one whose batch fails sends the remaining rows through the per-row ``apply_synced`` path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog
from trw_memory.models.memory import MemoryEntry

from trw_mcp.state._store_selection import MemoryStore

logger = structlog.get_logger(__name__)

__all__ = ["APPLY_CHUNK", "COUNT_KEYS", "merge_page"]

#: Rows per ``apply_synced_many`` call: the daemon's own cap (``MAX_SYNC_APPLY_MANY``), restated so an older trw-memory still imports.
APPLY_CHUNK = 200
COUNT_KEYS = ("inserted", "merged", "unchanged", "skipped_no_id", "invalid", "quarantined", "blocked", "failed")

#: ``(pulled row, local id, row this host held when read, merged row to write, remote_won)``; ``resolved`` is None when nothing is to be written.
_MergePulled = Callable[..., tuple[MemoryEntry, bool] | str]


@dataclass
class _Row:
    source_id: str
    local_id: str
    raw: dict[str, Any]
    existing: MemoryEntry | None
    resolved: MemoryEntry
    remote_won: bool


def merge_page(
    store: MemoryStore,
    target: str,
    team_learnings: list[dict[str, Any]],
    *,
    local_id_of: Callable[[str], str],
    merge_pulled: _MergePulled,
    prefetched: Callable[[str, str], MemoryEntry | None] | None,
) -> dict[str, int]:
    """Merge *team_learnings* into *target*; the outcome counts by :data:`COUNT_KEYS`."""
    counts = dict.fromkeys(COUNT_KEYS, 0)
    rows: list[_Row] = []
    for raw in team_learnings:
        source_id = str(raw.get("source_learning_id", "")).strip()
        if not source_id:
            counts["skipped_no_id"] += 1
            continue
        local_id = local_id_of(source_id)
        try:
            existing = (
                prefetched(source_id, local_id) if prefetched is not None else _find(store, target, source_id, local_id)
            )
            plan = merge_pulled(existing, raw, local_id, target, source_id)
        except Exception:  # justified: per-item, one invalid team learning must not abort the full merge
            _failed(counts, source_id)
            continue
        if isinstance(plan, str):  # nothing to write: counted here
            counts[plan] += 1
            continue
        rows.append(_Row(source_id, local_id, raw, existing, plan[0], plan[1]))
    verdicts = _apply_batched(store, target, rows)
    for row, verdict in zip(rows, verdicts, strict=True):
        try:
            if verdict is not None and verdict[0] != "conflict":
                outcome: tuple[str, str, MemoryEntry | None] | str = verdict
            else:  # no batched verdict: the whole per-row path; a batched ``conflict``: it starts at the re-read (the revision it was sent over is stale)
                outcome = _apply_alone(store, target, row, merge_pulled, start=0 if verdict is None else 1)
        except Exception:  # justified: per-item, one invalid team learning must not abort the full merge
            _failed(counts, row.source_id)
            continue
        if isinstance(outcome, str):  # a re-read after a conflict found nothing left to write: counted as that plan
            counts[outcome] += 1
        else:
            _book(counts, row.source_id, *outcome)
    return counts


def _find(store: MemoryStore, target: str, source_id: str, local_id: str) -> MemoryEntry | None:
    return store.find_synced(target, source_id, [local_id, source_id])


def _failed(counts: dict[str, int], source_id: str) -> None:
    counts["failed"] += 1
    logger.warning(
        "sync_team_merge_entry_error",
        event_type="sync_team_merge",
        outcome="error",
        source_learning_id=source_id,
        exc_info=True,
    )


def _apply_batched(
    store: MemoryStore, target: str, rows: list[_Row]
) -> list[tuple[str, str, MemoryEntry | None] | None]:
    """One verdict per row from the batched call; ``None`` sends that row to the per-row path (no batched call, a failed batch, a bad answer)."""
    from trw_memory.lifecycle.correction import revision_of

    verdicts: list[tuple[str, str, MemoryEntry | None] | None] = [None] * len(rows)
    for start in range(0, len(rows), APPLY_CHUNK):
        chunk = rows[start : start + APPLY_CHUNK]
        try:
            answers = store.apply_synced_many(
                target, [(r.resolved, revision_of(r.existing), r.remote_won) for r in chunk]
            )
            if len(answers) != len(chunk):
                raise ValueError(f"apply_synced_many answered {len(answers)} verdicts for {len(chunk)} rows")
        except Exception:  # trw-fail-silent-allow: logged; the per-row apply is the fallback and a store that is down fails there, loudly
            logger.warning("sync_team_merge_batched_apply_unavailable", event_type="sync_team_merge", exc_info=True)
            return verdicts  # this and every later chunk: per-row
        for offset, (status, reason) in enumerate(answers):
            verdicts[start + offset] = (status, reason, chunk[offset].existing)
    return verdicts


def _apply_alone(
    store: MemoryStore, target: str, row: _Row, merge_pulled: _MergePulled, *, start: int
) -> tuple[str, str, MemoryEntry | None] | str:
    """The per-row path: apply over the revision read, and on ``conflict`` re-read and re-merge (B71-90, PRD-CORE-308)."""
    from trw_memory.lifecycle.correction import CONFLICT_ATTEMPTS, revision_of

    existing, resolved, remote_won = row.existing, row.resolved, row.remote_won
    status, reason = "", ""
    for attempt in range(start, CONFLICT_ATTEMPTS):
        if attempt:  # a retry after a conflict re-reads that one row
            existing = _find(store, target, row.source_id, row.local_id)
            plan = merge_pulled(existing, row.raw, row.local_id, target, row.source_id)
            if isinstance(plan, str):  # nothing to write (after a conflict too)
                return plan
            resolved, remote_won = plan
        status, reason = store.apply_synced(target, resolved, if_revision=revision_of(existing), synced=remote_won)
        if status != "conflict":
            break
    return status, reason, existing


def _book(counts: dict[str, int], source_id: str, status: str, reason: str, existing: MemoryEntry | None) -> None:
    """Count one row's final write-gate verdict."""
    if status == "stored":
        counts["inserted" if existing is None else "merged"] += 1
    elif status in ("quarantined", "blocked"):
        # PRD-FIX-138-FR01: a write-time security REFUSAL is a judged decision, not a store failure. Booking it as ``failed`` held the pull
        # cursor on this item forever (see _client_cycle), and one poisoned team learning then stalled sync for the install.
        counts[status] += 1
        if status == "blocked":
            logger.warning(
                "sync_team_merge_entry_blocked",
                event_type="sync_team_merge",
                outcome="blocked",
                source_learning_id=source_id,
                reason=reason,
            )
    else:
        counts["failed"] += 1
        logger.warning(
            "sync_team_merge_entry_error",
            event_type="sync_team_merge",
            outcome=status,
            source_learning_id=source_id,
            reason=reason,
        )
