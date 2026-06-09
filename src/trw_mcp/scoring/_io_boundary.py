"""I/O boundary layer for scoring/state interactions.

Keeps file-system, SQLite, and YAML access out of the pure scoring modules.
Extracted for PRD-FIX-061 FR05/FR06 to remove scoring -> state layer violations.

This module is the single import point (facade) for the boundary. Cohesive
helper groups live in sibling modules and are re-exported here for back-compat:

- ``_io_sqlite_sync``  — best-effort Q-value SQLite write-back.
- ``_io_entries``      — YAML entry read/write helpers.
- ``_io_recall_jsonl`` — recall-tracking JSONL tail reader.

The YAML path index, scoring-config resolution, session-event scanning, and the
default entry lookup stay here because their cross-references are monkeypatched
on this module by the test-suite (e.g. ``_build_yaml_path_index``,
``_get_yaml_path_index``, ``_resolve_scoring_config``).
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, cast

import structlog

from trw_mcp.scoring._io_entries import (
    _load_entries_from_dir as _load_entries_from_dir,
)
from trw_mcp.scoring._io_entries import (
    _write_pending_entries as _write_pending_entries,
)
from trw_mcp.scoring._io_recall_jsonl import (
    _read_recall_tracking_jsonl as _read_recall_tracking_jsonl,
)
from trw_mcp.scoring._io_recall_jsonl import (
    _tail_lines as _tail_lines,
)
from trw_mcp.scoring._io_recall_jsonl import (
    _warn_recall_tracking_skip as _warn_recall_tracking_skip,
)
from trw_mcp.scoring._io_sqlite_sync import (
    Q_LEARNING_BATCH_CHUNK_SIZE as Q_LEARNING_BATCH_CHUNK_SIZE,
)
from trw_mcp.scoring._io_sqlite_sync import (
    _batch_sync_to_sqlite as _batch_sync_to_sqlite,
)
from trw_mcp.scoring._io_sqlite_sync import (
    _sync_chunk as _sync_chunk,
)
from trw_mcp.scoring._io_sqlite_sync import (
    _sync_to_sqlite as _sync_to_sqlite,
)

logger = structlog.get_logger(__name__)

# Type alias for the pending-update tuple used by process_outcome.
# (learning_id, yaml_path, entry_data, q_new, q_observations, outcome_history)
_PendingUpdate = tuple[str, Path | None, dict[str, object], float, int, list[object]]


# In-memory YAML path index: learning_id -> yaml_path.
# Rebuilt on TTL expiry to replace repeated O(N) scans with O(1) lookups.

_yaml_path_index: dict[str, Path] = {}
_yaml_path_index_ts: float = 0.0
_yaml_path_index_dir: Path | None = None
_yaml_path_index_lock = threading.Lock()
_YAML_INDEX_TTL: float = 30.0  # Rebuild at most every 30s


class _YamlReader(Protocol):
    """Minimal protocol for YAML readers used during index construction."""

    def read_yaml(self, path: Path) -> dict[str, object]: ...


class _ScoringConfig(Protocol):
    """Minimal config surface needed by scoring I/O helpers."""

    runs_root: str


def _read_learning_id(reader: _YamlReader, yaml_file: Path) -> str | None:
    """Read a YAML entry id, returning None when the entry is unreadable."""
    try:
        data = reader.read_yaml(yaml_file)
    except Exception:  # justified: fail-open, skip unreadable entries during index build
        logger.debug("yaml_path_index_entry_skipped", path=str(yaml_file), exc_info=True)
        return None

    lid = data.get("id")
    return lid if isinstance(lid, str) and lid else None


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
        lid = _read_learning_id(reader, yaml_file)
        if lid is not None:
            index[lid] = yaml_file
    return index


def _get_yaml_path_index(entries_dir: Path) -> dict[str, Path]:
    """Return the cached YAML path index, rebuilding if stale."""
    global _yaml_path_index, _yaml_path_index_dir, _yaml_path_index_ts
    now = time.monotonic()
    if _yaml_path_index_dir == entries_dir and now - _yaml_path_index_ts < _YAML_INDEX_TTL and _yaml_path_index:
        return _yaml_path_index
    with _yaml_path_index_lock:
        # Double-check after acquiring lock
        if _yaml_path_index_dir == entries_dir and now - _yaml_path_index_ts < _YAML_INDEX_TTL and _yaml_path_index:
            return _yaml_path_index
        _yaml_path_index = _build_yaml_path_index(entries_dir)
        _yaml_path_index_dir = entries_dir
        _yaml_path_index_ts = now
        logger.debug("yaml_path_index_built", entries=len(_yaml_path_index))
        return _yaml_path_index


def _reset_yaml_path_index() -> None:
    """Clear the cached index — for testing only."""
    global _yaml_path_index, _yaml_path_index_dir, _yaml_path_index_ts
    with _yaml_path_index_lock:
        _yaml_path_index = {}
        _yaml_path_index_dir = None
        _yaml_path_index_ts = 0.0


def _backfill_yaml_path_index(lid: str, entry_path: Path | None) -> None:
    """Seed the cached YAML index with a resolved lookup path."""
    if entry_path is None:
        return
    with _yaml_path_index_lock:
        _yaml_path_index[lid] = entry_path


def _safe_mtime(path: Path) -> float | None:
    """Return file mtime, or None when the path cannot be stat'ed."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _resolve_scoring_config() -> _ScoringConfig:
    """Resolve scoring config, honoring patched correlation-module hooks in tests."""
    from trw_mcp.scoring._utils import get_config

    correlation_mod = sys.modules.get("trw_mcp.scoring._correlation")
    if correlation_mod is not None:
        patched_get_config = getattr(correlation_mod, "get_config", None)
        if callable(patched_get_config):
            return cast("_ScoringConfig", patched_get_config())
    return cast("_ScoringConfig", get_config())


def _decode_jsonl_line(raw: bytes) -> str | None:
    """Decode one JSONL byte line as UTF-8, or None when the bytes are invalid.

    Reading JSONL in byte-line mode and decoding per row isolates a single
    non-UTF-8 row so adjacent valid rows survive instead of the whole-file
    read aborting on a ``UnicodeDecodeError``. The undecodable bytes are never
    surfaced (callers log structural-only signals).
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _read_recent_session_records(events_path: Path) -> list[dict[str, object]]:
    """Read parseable event records from an events.jsonl file.

    Reads in byte-line mode so a single non-UTF-8 row is skipped without losing
    adjacent valid run_init/session_start rows. Malformed JSON and non-object
    rows are skipped the same way.
    """
    import json

    records: list[dict[str, object]] = []
    try:
        with events_path.open("rb") as fh:
            for raw in fh:
                line = _decode_jsonl_line(raw)
                if line is None:
                    continue
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
    except OSError:
        return []
    return records


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

    # Read from YAML directly when SQLite misses.
    if entry_path is not None:
        try:
            from trw_mcp.state.persistence import FileStateReader

            yaml_data = FileStateReader().read_yaml(entry_path)
        except Exception:  # justified: fail-open, YAML read failure returns None
            logger.debug("yaml_entry_read_failed", learning_id=lid)
        else:
            if data is None:
                data = yaml_data
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


def _find_session_start_ts(trw_dir: Path) -> datetime | None:
    """Return the newest ``run_init`` or ``session_start`` timestamp under ``runs_root``.

    Uses glob-based discovery for mixed run directory layouts and preserves the
    PRD-FIX-061/070 boundary contract used by ``_correlation.py``.
    """
    cfg = _resolve_scoring_config()

    project_root = trw_dir.parent
    runs_root = project_root / cfg.runs_root

    if not runs_root.exists():
        return None

    events_files: list[tuple[float, Path]] = []
    for events_path in runs_root.glob("**/meta/events.jsonl"):
        mtime = _safe_mtime(events_path)
        if mtime is not None:
            events_files.append((mtime, events_path))

    events_files.sort(reverse=True)

    for _mtime, events_path in events_files[:5]:
        for record in reversed(_read_recent_session_records(events_path)):
            if str(record.get("event", "")) in ("run_init", "session_start"):
                ts_str = str(record.get("ts", ""))
                if ts_str:
                    try:
                        dt = datetime.fromisoformat(ts_str)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt
                    except ValueError:
                        continue

    logger.debug("session_scope_fallback_to_window")
    return None


__all__ = [
    "Q_LEARNING_BATCH_CHUNK_SIZE",
    "_PendingUpdate",
    "_batch_sync_to_sqlite",
    "_default_lookup_entry",
    "_find_session_start_ts",
    "_load_entries_from_dir",
    "_read_recall_tracking_jsonl",
    "_sync_chunk",
    "_sync_to_sqlite",
    "_write_pending_entries",
]
