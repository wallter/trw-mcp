"""One-off migration: clear implausibly shared anchor sets (PRD-CORE-267 FR03).

Before FR01/FR02, anchor derivation drew candidate files from whichever peer
run had written an event most recently plus a name-only ``git diff`` over the
shared working tree, and fell back to the FIRST symbol of each file when no
line range matched. The result, measured on the development store on
2026-09-05: 2,006 anchored rows across only 370 distinct anchor sets, with the
largest 26 sets holding 1,472 of those rows and the single largest — three
unrelated backend symbols — carried by 381 entries about entirely different
subjects.

Those anchors cannot be repaired: the information needed to reconstruct a
correct one was never captured. Clearing them is the only truthful action, so
this sweep is dry-run by default and requires an explicit apply.

Invoked by ``trw-mcp maintain-verify --clear-shared-anchors``. Nothing in the
server calls it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Page size for the keyset walk over stored entries. Independent of the
#: migration threshold — it bounds memory, not selection.
_PAGE_SIZE = 500

#: Hard ceiling on rows scanned in one invocation, so a pathologically large
#: store cannot turn an operator command into an unbounded scan.
_MAX_SCANNED = 200_000

#: The identity of an anchor set: the sorted (file, symbol_name) pairs. Line
#: numbers and signatures are deliberately excluded — two entries fabricated
#: from the same file list name the same symbols even when the file has since
#: been edited and the recorded line numbers drifted apart.
_AnchorSetKey = tuple[tuple[str, str], ...]


@dataclass(slots=True)
class AnchorMigrationSummary:
    """Result of one migration invocation — the operator-facing audit record."""

    dry_run: bool = True
    threshold: int = 0
    entries_scanned: int = 0
    sets_over_threshold: int = 0
    entries_affected: int = 0
    entries_cleared: int = 0
    clear_failures: int = 0
    duration_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        """Plain mapping for CLI/JSON output."""
        return {
            "dry_run": self.dry_run,
            "threshold": self.threshold,
            "entries_scanned": self.entries_scanned,
            "sets_over_threshold": self.sets_over_threshold,
            "entries_affected": self.entries_affected,
            "entries_cleared": self.entries_cleared,
            "clear_failures": self.clear_failures,
            "duration_ms": self.duration_ms,
        }


def _anchor_set_key(entry: Any) -> _AnchorSetKey | None:
    """Return the identity of *entry*'s anchor set, or ``None`` when unanchored."""
    anchors = list(getattr(entry, "anchors", []) or [])
    pairs: list[tuple[str, str]] = []
    for anchor in anchors:
        if isinstance(anchor, dict):
            file_ref, symbol = anchor.get("file"), anchor.get("symbol_name")
        else:
            file_ref, symbol = getattr(anchor, "file", None), getattr(anchor, "symbol_name", None)
        if file_ref is None and symbol is None:
            continue
        pairs.append((str(file_ref or ""), str(symbol or "")))
    if not pairs:
        return None
    return tuple(sorted(pairs))


def _collect_anchor_groups(
    backend: Any,
    *,
    namespace: str | None,
    max_scanned: int,
) -> tuple[dict[_AnchorSetKey, list[tuple[str, str]]], int]:
    """Group every anchored entry by its exact anchor set.

    Returns ``(groups, entries_scanned)`` where each group maps an anchor-set
    identity to the ``(entry_id, namespace)`` pairs carrying it. Pages with a
    keyset cursor so the walk is stable while rows are being rewritten.
    """
    from trw_memory.models.memory import MemoryStatus
    from trw_memory.storage.interface import EntryCursor

    groups: dict[_AnchorSetKey, list[tuple[str, str]]] = {}
    scanned = 0
    cursor: Any = None
    while scanned < max_scanned:
        page = backend.list_entries(
            status=MemoryStatus.ACTIVE,
            namespace=namespace,
            limit=_PAGE_SIZE,
            after=cursor,
        )
        if not page:
            break
        for entry in page:
            scanned += 1
            key = _anchor_set_key(entry)
            if key is None:
                continue
            entry_id = str(getattr(entry, "id", ""))
            if not entry_id:
                continue
            entry_ns = str(getattr(entry, "namespace", namespace) or "default")
            groups.setdefault(key, []).append((entry_id, entry_ns))
        if len(page) < _PAGE_SIZE:
            break
        cursor = EntryCursor.from_entry(page[-1])
    return groups, scanned


def clear_shared_anchor_sets(
    backend: Any,
    *,
    threshold: int,
    apply: bool = False,
    namespace: str | None = None,
    max_scanned: int = _MAX_SCANNED,
) -> AnchorMigrationSummary:
    """Report — and optionally clear — anchor sets shared by too many entries.

    An entry is selected when its EXACT anchor set is carried by at least
    *threshold* entries. Selected entries have their anchor list emptied and
    their anchor validity cleared to ``None``, which is the value PRD-CORE-244
    established for "never assessed".

    Idempotent by construction: a cleared entry has no anchor set, so it is not
    grouped on a later run and a second apply reports zero affected.

    Args:
        backend: Memory backend exposing ``list_entries`` and ``update``.
        threshold: ``TRWConfig.anchor_shared_set_migration_threshold``.
        apply: ``False`` (default) reports without writing anything.
        namespace: Optional namespace scope; ``None`` covers every namespace.
        max_scanned: Ceiling on rows examined in one invocation.

    Returns:
        An :class:`AnchorMigrationSummary` describing what was found and done.
    """
    started = time.monotonic()
    summary = AnchorMigrationSummary(dry_run=not apply, threshold=threshold)

    groups, summary.entries_scanned = _collect_anchor_groups(backend, namespace=namespace, max_scanned=max_scanned)
    selected = [members for members in groups.values() if len(members) >= threshold]
    summary.sets_over_threshold = len(selected)
    summary.entries_affected = sum(len(members) for members in selected)

    if apply:
        for members in selected:
            for entry_id, entry_ns in members:
                if _clear_entry(backend, entry_id, entry_ns):
                    summary.entries_cleared += 1
                else:
                    summary.clear_failures += 1

    summary.duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "anchor_shared_set_migration",
        dry_run=summary.dry_run,
        threshold=summary.threshold,
        entries_scanned=summary.entries_scanned,
        sets_over_threshold=summary.sets_over_threshold,
        entries_affected=summary.entries_affected,
        entries_cleared=summary.entries_cleared,
        clear_failures=summary.clear_failures,
        duration_ms=summary.duration_ms,
    )
    return summary


def _clear_entry(backend: Any, entry_id: str, namespace: str) -> bool:
    """Empty one entry's anchors + validity. ``False`` when the write did not land."""
    try:
        backend.update(entry_id, namespace=namespace, anchors=[], anchor_validity=None)
    except Exception:  # trw-fail-silent-allow: one unwritable row is counted as a clear_failure in the returned summary and reported to the operator, never discarded
        logger.warning("anchor_migration_clear_failed", entry_id=entry_id, namespace=namespace, exc_info=True)
        return False
    return True


def run_anchor_migration_for_project(*, apply: bool = False, namespace: str | None = None) -> AnchorMigrationSummary:
    """Resolve the live config + backend, then run the migration. CLI entry seam."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_trw_dir
    from trw_mcp.state.memory_adapter import get_backend

    config = get_config()
    return clear_shared_anchor_sets(
        get_backend(resolve_trw_dir()),
        threshold=config.anchor_shared_set_migration_threshold,
        apply=apply,
        namespace=namespace,
    )


__all__ = [
    "AnchorMigrationSummary",
    "clear_shared_anchor_sets",
    "run_anchor_migration_for_project",
]
