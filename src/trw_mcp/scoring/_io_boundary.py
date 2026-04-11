"""I/O boundary layer for the scoring package.

This module bridges scoring functions to state-layer I/O (file system,
SQLite, YAML).  All ``from trw_mcp.state.*`` imports are concentrated here
so that the pure scoring modules (_correlation.py, _decay.py) remain free
of state-layer dependencies.

PRD-FIX-061 FR05/FR06: Extracted from ``_correlation.py`` and ``_decay.py``
to eliminate layer violations (scoring -> state).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Type alias for the pending-update tuple used by process_outcome.
# (learning_id, yaml_path, entry_data, q_new, q_observations, outcome_history)
_PendingUpdate = tuple[str, Path | None, dict[str, object], float, int, list[object]]


# ---------------------------------------------------------------------------
# In-memory YAML path index — maps learning_id -> yaml_path.
# Built once on first access, refreshed after TTL expiry.
# Replaces O(N) full-directory scans with O(1) dict lookups while
# preserving the dual-read/dual-write contract (YAML is authoritative).
# ---------------------------------------------------------------------------

_yaml_path_index: dict[str, Path] = {}
_yaml_path_index_ts: float = 0.0
_yaml_path_index_lock = threading.Lock()
_YAML_INDEX_TTL: float = 30.0  # Rebuild at most every 30s


def _build_yaml_path_index(entries_dir: Path) -> dict[str, Path]:
    """Scan entries_dir once and build {learning_id -> yaml_path} map.

    Reads the ``id`` field from each YAML file. Skips files that fail
    to parse. This is O(N) but runs once per TTL window instead of
    once per lookup (which was O(M*N) total).
    """
    from trw_mcp.state._helpers import iter_yaml_entry_files
    from trw_mcp.state.persistence import FileStateReader

    index: dict[str, Path] = {}
    reader = FileStateReader()
    for yaml_file in iter_yaml_entry_files(entries_dir):
        try:
            data = reader.read_yaml(yaml_file)
            lid = data.get("id")
            if isinstance(lid, str) and lid:
                index[lid] = yaml_file
        except Exception:  # justified: fail-open, skip unreadable entries during index build
            continue
    return index


def _get_yaml_path_index(entries_dir: Path) -> dict[str, Path]:
    """Return the cached YAML path index, rebuilding if stale."""
    global _yaml_path_index, _yaml_path_index_ts
    now = time.monotonic()
    if now - _yaml_path_index_ts < _YAML_INDEX_TTL and _yaml_path_index:
        return _yaml_path_index
    with _yaml_path_index_lock:
        # Double-check after acquiring lock
        if now - _yaml_path_index_ts < _YAML_INDEX_TTL and _yaml_path_index:
            return _yaml_path_index
        _yaml_path_index = _build_yaml_path_index(entries_dir)
        _yaml_path_index_ts = now
        logger.debug("yaml_path_index_built", entries=len(_yaml_path_index))
        return _yaml_path_index


def _reset_yaml_path_index() -> None:
    """Clear the cached index — for testing only."""
    global _yaml_path_index, _yaml_path_index_ts
    with _yaml_path_index_lock:
        _yaml_path_index = {}
        _yaml_path_index_ts = 0.0


def _backfill_yaml_path_index(lid: str, entry_path: Path | None) -> None:
    """Seed the cached YAML index with a resolved lookup path."""
    if entry_path is None:
        return
    with _yaml_path_index_lock:
        _yaml_path_index[lid] = entry_path


# ---------------------------------------------------------------------------
# Extracted from _correlation.py
# ---------------------------------------------------------------------------


def _default_lookup_entry(
    lid: str,
    trw_dir: Path,
    entries_dir: Path,
) -> tuple[Path | None, dict[str, object] | None]:
    """Default entry lookup: SQLite data + indexed YAML path.

    Preserves the dual-read/dual-write contract: SQLite provides data
    (O(1)), the YAML path index provides the write-back path (O(1)
    amortized).  When SQLite misses, falls back to the YAML index
    for both data and path.

    PRD-FIX-061-FR05: This is the default backend selector, extracted so
    that ``process_outcome`` can accept an alternative lookup callable
    for testing or alternative storage backends.

    Args:
        lid: Learning entry ID to look up.
        trw_dir: Path to .trw directory (for SQLite backend).
        entries_dir: Path to YAML entries directory (fallback).

    Returns:
        Tuple of (yaml_path_or_None, entry_data_or_None).
    """
    from trw_mcp.state.memory_adapter import (
        find_entry_by_id as sqlite_find_entry_by_id,
    )

    # O(1) path lookup via cached index (replaces O(N) glob per call)
    yaml_index = _get_yaml_path_index(entries_dir) if entries_dir.exists() else {}
    entry_path = yaml_index.get(lid)

    # SQLite-primary for data, YAML-fallback
    data: dict[str, object] | None = sqlite_find_entry_by_id(trw_dir, lid)

    if data is not None:
        return entry_path, data

    # SQLite miss — read data from YAML directly
    if entry_path is not None:
        try:
            from trw_mcp.state.persistence import FileStateReader

            data = FileStateReader().read_yaml(entry_path)
        except Exception:  # justified: fail-open, YAML read failure returns None
            logger.debug("yaml_entry_read_failed", learning_id=lid)
        if data is not None:
            return entry_path, data

    # Compatibility fallback: if the O(1) YAML-path index does not know about
    # the entry yet (or the direct read failed), fall back to the canonical
    # state-layer YAML scan helper. This preserves pre-FIX-061 call patterns
    # used by tests and pre-migration entries without re-introducing scoring ->
    # state imports in _correlation.py.
    try:
        from trw_mcp.state.analytics import find_entry_by_id as yaml_find_entry_by_id

        result = yaml_find_entry_by_id(entries_dir, lid)
        if result is not None:
            _backfill_yaml_path_index(lid, result[0])
            return result
    except Exception:  # justified: fail-open, fallback scan is best-effort
        logger.debug("yaml_entry_lookup_failed", learning_id=lid, exc_info=True)

    return entry_path, data


def _sync_to_sqlite(
    lid: str,
    q_new: float,
    q_obs: int,
    history: list[str],
    trw_dir: Path,
) -> None:
    """Sync Q-value and outcome_history back to SQLite (best-effort)."""
    try:
        from trw_mcp.state.memory_adapter import get_backend

        backend = get_backend(trw_dir)
        backend.update(
            lid,
            q_value=round(q_new, 4),
            q_observations=q_obs,  # already incremented by _update_entry_q_values
            outcome_history=history,
        )
    except Exception:  # justified: fail-open, SQLite sync is best-effort (YAML is authoritative)
        logger.debug("q_value_sqlite_sync_skipped", exc_info=True)  # justified: fail-open, YAML is authoritative


def _batch_sync_to_sqlite(
    updates: list[_PendingUpdate],
    trw_dir: Path,
) -> None:
    """Batch sync Q-values to SQLite (PRD-FIX-070-FR03).

    Groups all updates into a single backend session instead of N individual
    calls. Falls back gracefully -- individual entry failures don't abort the batch.
    """
    if not updates:
        return
    try:
        from trw_mcp.state.memory_adapter import get_backend

        backend = get_backend(trw_dir)
        synced = 0
        for lid, _path, _data, q_new, q_obs, history in updates:
            try:
                backend.update(
                    lid,
                    q_value=round(q_new, 4),
                    q_observations=q_obs,
                    outcome_history=history,
                )
                synced += 1
            except Exception:  # noqa: PERF203 — justified: fail-open, individual entry failures don't abort batch
                logger.debug("q_value_sqlite_sync_skipped", learning_id=lid, exc_info=True)
        logger.debug("batch_sqlite_sync_complete", synced=synced, total=len(updates))
    except Exception:  # justified: fail-open, SQLite batch sync is best-effort
        logger.debug("q_value_sqlite_batch_sync_failed", exc_info=True)


def _write_pending_entries(
    pending_updates: list[_PendingUpdate],
) -> list[str]:
    """Write pending Q-value updates to YAML files.

    PRD-FIX-061-FR05: Extracted from ``process_outcome`` so that
    ``_correlation.py`` does not need to import ``FileStateWriter``
    from the state layer.

    Args:
        pending_updates: List of pending update tuples from process_outcome.

    Returns:
        List of learning IDs that were successfully written.
    """
    from trw_mcp.state.persistence import FileStateWriter

    updated_ids: list[str] = []
    writer = FileStateWriter()
    for lid, entry_path, data, _q_new, _q_obs, _history in pending_updates:
        if entry_path is not None:
            try:
                writer.write_yaml(entry_path, data)
            except Exception:  # justified: fail-open, YAML write failures exclude entry from updated_ids
                logger.warning(
                    "q_value_yaml_write_failed",
                    learning_id=lid,
                    exc_info=True,
                )
                continue  # Do not claim this ID was updated
        updated_ids.append(lid)
    return updated_ids


# ---------------------------------------------------------------------------
# Extracted from _decay.py
# ---------------------------------------------------------------------------


def _load_entries_from_dir(entries_dir: Path) -> Iterator[dict[str, object]]:
    """Load entry dicts from a YAML entries directory.

    Yields parsed dicts for each readable YAML entry file.
    Silently skips files that fail to parse.

    Args:
        entries_dir: Directory containing YAML entry files.

    Yields:
        Parsed entry dicts.
    """
    from trw_mcp.state._helpers import iter_yaml_entry_files
    from trw_mcp.state.persistence import FileStateReader

    reader = FileStateReader()
    for yaml_file in iter_yaml_entry_files(entries_dir):
        try:
            yield reader.read_yaml(yaml_file)
        except Exception:  # justified: fail-open, skip unreadable YAML entries  # noqa: S112, PERF203
            continue


def _read_recall_tracking_jsonl(
    receipt_path: Path,
    *,
    max_lines: int = 5000,
) -> list[dict[str, object]]:
    """Read the TAIL of the recall-tracking JSONL file, skipping malformed lines.

    Reads from the end of the file to avoid scanning 100K+ old records
    that will be filtered out by the correlation window anyway.  The
    ``max_lines`` cap keeps memory and CPU bounded even for very large
    files (the old implementation read all 172K+ lines).

    PRD-FIX-061-FR05: Extracted from ``correlate_recalls`` so that
    ``_correlation.py`` does not need to import ``FileStateReader``
    from the state layer.

    Args:
        receipt_path: Path to recall_tracking.jsonl.
        max_lines: Maximum number of recent lines to read (default 5000).

    Returns:
        List of record dicts; empty list if file missing or unreadable.
    """
    import json

    if not receipt_path.exists():
        return []

    records: list[dict[str, object]] = []
    try:
        # Read the tail of the file efficiently: seek backwards from EOF
        # to find the last ``max_lines`` newline-delimited records.
        raw_lines = _tail_lines(receipt_path, max_lines)
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                logger.debug("recall_tracking_bad_json_skipped", path=str(receipt_path))
                continue
            if isinstance(record, dict):
                records.append(record)
    except OSError:  # justified: fail-open, can't read the tracking file
        logger.debug("recall_tracking_read_failed", path=str(receipt_path))
    return records


def _tail_lines(path: Path, max_lines: int) -> list[str]:
    """Read the last ``max_lines`` lines from a file efficiently.

    Uses backward seeking from EOF to avoid reading the entire file
    when only the tail is needed.  Falls back to reading the whole
    file when it is small enough (< 64 KB).
    """
    import os

    file_size = path.stat().st_size
    if file_size == 0:
        return []

    # For small files, just read the whole thing
    if file_size < 65_536:
        with path.open("r", encoding="utf-8") as fh:
            return fh.readlines()[-max_lines:]

    # For large files, seek backwards in chunks to find enough newlines
    chunk_size = min(file_size, max(4096, max_lines * 256))  # ~256 bytes/line estimate
    raw_lines: list[bytes] = []
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        remaining = file_size
        buf = b""
        while remaining > 0 and len(raw_lines) < max_lines + 1:
            read_size = min(chunk_size, remaining)
            remaining -= read_size
            fh.seek(remaining)
            chunk = fh.read(read_size)
            buf = chunk + buf
            raw_lines = buf.split(b"\n")
        # Decode only the tail we need
        tail = raw_lines[-max_lines:] if len(raw_lines) > max_lines else raw_lines
        return [ln.decode("utf-8", errors="replace") for ln in tail if ln]


__all__ = [
    "_PendingUpdate",
    "_batch_sync_to_sqlite",
    "_default_lookup_entry",
    "_load_entries_from_dir",
    "_read_recall_tracking_jsonl",
    "_sync_to_sqlite",
    "_write_pending_entries",
]
