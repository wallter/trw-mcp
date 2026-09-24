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

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, cast

import structlog

from trw_mcp.scoring._io_entries import (
    _load_entries_from_dir as _load_entries_from_dir,
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
from trw_mcp.scoring._yaml_id_index import _build_yaml_path_index as _build_yaml_path_index
from trw_mcp.scoring._yaml_id_index import _read_learning_id as _read_learning_id

logger = structlog.get_logger(__name__)

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


#: How many times its own build cost a cache must survive before expiring.
#: A TTL shorter than the build is not a cache at all -- every lookup finds an
#: expired index, rebuilds it, and the rebuild is stale before it returns.
_YAML_INDEX_MIN_LIFETIME_RATIO = 10.0

#: Seconds the last build took, used to widen the TTL on a store big enough
#: that the fixed TTL cannot hold it.
_yaml_path_index_build_seconds: float = 0.0


def _effective_yaml_index_ttl() -> float:
    """The TTL actually enforced, never shorter than the build can sustain.

    This store reached 6,805 entries, where the build took 32 s against a 30 s
    TTL: the index expired BEFORE it finished being built, so a caller looping
    over ids rebuilt the whole store per id and a single trw_deliver blocked the
    server for tens of minutes. A fixed TTL silently becomes a no-op cache at
    whatever size crosses it, with no error and no log -- it just gets slower.
    Scaling the floor with the measured build cost removes the cliff instead of
    moving it.
    """
    return max(_YAML_INDEX_TTL, _yaml_path_index_build_seconds * _YAML_INDEX_MIN_LIFETIME_RATIO)


def _get_yaml_path_index(entries_dir: Path) -> dict[str, Path]:
    """Return the cached YAML path index, rebuilding if stale."""
    global _yaml_path_index, _yaml_path_index_dir, _yaml_path_index_ts
    global _yaml_path_index_build_seconds
    now = time.monotonic()
    ttl = _effective_yaml_index_ttl()
    if _yaml_path_index_dir == entries_dir and now - _yaml_path_index_ts < ttl and _yaml_path_index:
        return _yaml_path_index
    with _yaml_path_index_lock:
        # Double-check after acquiring lock
        ttl = _effective_yaml_index_ttl()
        if _yaml_path_index_dir == entries_dir and now - _yaml_path_index_ts < ttl and _yaml_path_index:
            return _yaml_path_index
        started = time.monotonic()
        _yaml_path_index = _build_yaml_path_index(entries_dir)
        _yaml_path_index_build_seconds = time.monotonic() - started
        _yaml_path_index_dir = entries_dir
        _yaml_path_index_ts = time.monotonic()
        logger.debug(
            "yaml_path_index_built",
            entries=len(_yaml_path_index),
            build_seconds=round(_yaml_path_index_build_seconds, 3),
        )
        if _yaml_path_index_build_seconds > _YAML_INDEX_TTL:
            logger.warning(
                "yaml_path_index_build_exceeds_ttl",
                entries=len(_yaml_path_index),
                build_seconds=round(_yaml_path_index_build_seconds, 3),
                fixed_ttl=_YAML_INDEX_TTL,
                effective_ttl=round(_effective_yaml_index_ttl(), 3),
                impact="the fixed TTL alone would rebuild on every lookup; the floor was widened",
            )
        return _yaml_path_index


def _reset_yaml_path_index() -> None:
    """Clear the cached index — for testing only."""
    global _yaml_path_index, _yaml_path_index_dir, _yaml_path_index_ts
    with _yaml_path_index_lock:
        _yaml_path_index = {}
        _yaml_path_index_dir = None
        globals()["_yaml_path_index_build_seconds"] = 0.0
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
    """Resolve the scoring config (patch ``trw_mcp.scoring._utils.get_config`` in tests)."""
    from trw_mcp.scoring._utils import get_config

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
    "_default_lookup_entry",
    "_find_session_start_ts",
    "_load_entries_from_dir",
    "_read_recall_tracking_jsonl",
]
