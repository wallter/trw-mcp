"""Bounded id-to-path resolution for learning sidecar files (PRD-FIX-130-FR07).

Belongs to the ``tools/_learning_helpers.py`` dedup path.

**The defect.** Resolving a duplicate's surviving entry file used to glob the
entries directory, sort it, and read YAML files one at a time until one carried
the matching id. Against the measured corpus — 6,532 files — a verifier timed
that scan at **45,945 ms worst case**, and the worst case is the COMMON case:
filenames are date-prefixed, so a recently re-learned entry sorts LAST. It ran
inside the per-record journal replay, on the hot path this PRD exists to bound.

**The fix.** ``save_learning_entry`` writes every sidecar under
``{created}-{slug}.yaml``, built by :func:`trw_mcp.state.analytics.core.entry_filename`.
The backend row for an id carries both halves of that name (``summary`` and
``created``), and looking a row up by id is a single indexed SQLite read. So the
path is COMPUTED, not searched: one row read plus at most three YAML reads
(see :func:`_candidate_dates` for why three and not one), independent of how
many files the corpus holds.

**Refuse rather than guess.** The computed path is accepted only when the file
it names actually carries the requested id. A near-miss — a same-day entry with
a colliding slug, a summary edited after the sidecar was written — resolves to
``None`` and the caller must decline to merge rather than fold a learning into
whatever entry happened to be nearby. Merging into the wrong survivor is silent
corruption of the store; declining to merge costs one duplicate row.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

__all__ = ["resolve_entry_file", "resolve_entry_path"]


def _entry_at(reader: object, path: Path) -> dict[str, object] | None:
    """Read the YAML at *path*, or ``None`` when it is unreadable/not a mapping.

    Returns the PARSED BODY, not just the id, because the caller needs the same
    content immediately afterwards. Re-reading it — as the merge branch and the
    backend sync both used to — turns FR07's "at most one entry YAML read"
    bound into three.
    """
    read_yaml = getattr(reader, "read_yaml", None)
    if read_yaml is None or not path.is_file():
        return None
    try:
        data = read_yaml(path)
    except Exception:  # justified: fail-open, an unreadable candidate is simply not a match
        logger.debug("entry_path_candidate_unreadable", path=str(path), outcome="not_a_match", exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def resolve_entry_path(
    entries_dir: Path,
    entry_id: str,
    reader: object,
    *,
    trw_dir: Path | None = None,
) -> Path | None:
    """The sidecar path for *entry_id* — :func:`resolve_entry_file` without its body."""
    resolved = resolve_entry_file(entries_dir, entry_id, reader, trw_dir=trw_dir)
    return None if resolved is None else resolved[0]


def resolve_entry_file(
    entries_dir: Path,
    entry_id: str,
    reader: object,
    *,
    trw_dir: Path | None = None,
) -> tuple[Path, dict[str, object]] | None:
    """Return ``(path, parsed body)`` for *entry_id*, or ``None`` when unproven.

    Bounded by construction: the backend row for *entry_id* supplies the summary
    and created date that :func:`~trw_mcp.state.analytics.core.entry_filename`
    turns into a filename, and at most three candidate files (the row's date and
    its two neighbours — see :func:`_candidate_dates`) are read to confirm one
    carries that id. No directory scan, no corpus read, no partial match.

    Returns ``None`` — never a "close enough" path — when the backend has no row
    for the id, when the computed file does not exist, or when it exists but
    holds a different id. The caller decides what a failed resolution means; this
    function refuses to guess.
    """
    if not entry_id or trw_dir is None:
        return None
    try:
        from trw_mcp.state.analytics.core import entry_filename
        from trw_mcp.state.memory_adapter import find_entry_by_id

        row = find_entry_by_id(trw_dir, entry_id)
    except Exception:  # justified: fail-open, an unavailable backend means "unresolved", not "wrong file"
        logger.debug("entry_path_backend_lookup_failed", entry_id=entry_id, outcome="unresolved", exc_info=True)
        return None
    if not isinstance(row, dict):
        return None
    summary = str(row.get("summary", ""))
    created = str(row.get("created", ""))
    if not summary or not created:
        return None
    for candidate_date in _candidate_dates(created):
        candidate = entries_dir / entry_filename(summary, candidate_date)
        data = _entry_at(reader, candidate)
        if data is not None and str(data.get("id", "")) == entry_id:
            return candidate, data
    logger.debug("entry_path_unresolved", entry_id=entry_id, created=created)
    return None


def _candidate_dates(created: str) -> list[str]:
    """The dates a sidecar for a row created on *created* can be named after.

    The two halves of the dual write do NOT agree on a date. The sidecar's
    ``created`` is ``datetime.date.today()`` — the LOCAL date — while the backend
    row's is ``created_at.date()``, which is UTC. Measured 2026-09-04 at 17:00
    local: the row said ``2026-09-05`` and the file said ``2026-09-04``. A
    single-date lookup would therefore miss for a whole timezone offset's worth
    of every day and silently fall back to the corpus scan FR07 exists to delete.

    Probing the neighbouring days keeps the lookup O(1) — three candidate reads,
    independent of corpus size — and each candidate is still accepted only when
    the file proves the id. Returns the row's own date first, so the common case
    costs one read.
    """
    try:
        day = date.fromisoformat(created[:10])
    except ValueError:  # justified: an unparseable date is still worth one literal probe
        return [created]
    return [day.isoformat(), (day - timedelta(days=1)).isoformat(), (day + timedelta(days=1)).isoformat()]
