"""I/O boundary layer for the scoring package.

This module bridges scoring functions to state-layer I/O (file system,
SQLite, YAML).  All ``from trw_mcp.state.*`` imports are concentrated here
so that the pure scoring modules (_correlation.py, _decay.py) remain free
of state-layer dependencies.

PRD-FIX-061 FR05/FR06: Extracted from ``_correlation.py`` and ``_decay.py``
to eliminate layer violations (scoring -> state).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import structlog

from trw_mcp.scoring._utils import (
    TRWConfig,
    _ensure_utc,
    get_config,
)

logger = structlog.get_logger(__name__)

# Type alias for the pending-update tuple used by process_outcome.
# (learning_id, yaml_path, entry_data, q_new, q_observations, outcome_history)
_PendingUpdate = tuple[str, Path | None, dict[str, object], float, int, list[object]]


# ---------------------------------------------------------------------------
# Extracted from _correlation.py
# ---------------------------------------------------------------------------


def _default_lookup_entry(
    lid: str,
    trw_dir: Path,
    entries_dir: Path,
) -> tuple[Path | None, dict[str, object] | None]:
    """Default entry lookup: SQLite primary, YAML fallback.

    PRD-FIX-061-FR05: This is the default backend selector, extracted so
    that ``process_outcome`` can accept an alternative lookup callable
    for testing or alternative storage backends.  Backend selection
    (state-layer I/O) happens here at the call boundary, not inside the
    pure scoring logic.

    Args:
        lid: Learning entry ID to look up.
        trw_dir: Path to .trw directory (for SQLite backend).
        entries_dir: Path to YAML entries directory (fallback).

    Returns:
        Tuple of (yaml_path_or_None, entry_data_or_None).
    """
    from trw_mcp.state.analytics import find_entry_by_id as yaml_find_entry_by_id
    from trw_mcp.state.memory_adapter import (
        find_entry_by_id as sqlite_find_entry_by_id,
    )
    from trw_mcp.state.memory_adapter import (
        find_yaml_path_for_entry,
    )

    entry_path: Path | None = None
    data: dict[str, object] | None = None

    sqlite_data = sqlite_find_entry_by_id(trw_dir, lid)
    yaml_found: tuple[Path, dict[str, object]] | None = None
    if entries_dir.exists():
        yaml_found = yaml_find_entry_by_id(entries_dir, lid)

    if sqlite_data is not None:
        data = sqlite_data
        entry_path = find_yaml_path_for_entry(trw_dir, lid)
        if entry_path is None and yaml_found is not None:
            entry_path, _yaml_data = yaml_found
    elif yaml_found is not None:
        entry_path, data = yaml_found

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


def _find_session_start_ts(trw_dir: Path) -> datetime | None:
    """Find the timestamp of the most recent session-start event.

    Scans all events.jsonl files under runs_root/**/ for the most recent
    session-start event. Uses glob to handle all directory layouts
    (PROPER, FLAT, OLD_NESTED).

    PRD-FIX-070-FR01: Replaced iter_run_dirs (which only found PROPER layout)
    with a direct glob across all run directory structures.

    Args:
        trw_dir: Path to .trw directory.

    Returns:
        Timestamp of the most recent session-start event, or None.
    """
    from trw_mcp.state.persistence import FileStateReader

    project_root = trw_dir.parent
    cfg: TRWConfig = get_config()
    runs_root = project_root / cfg.runs_root

    if not runs_root.exists():
        return None

    # Glob across all directory layouts (PROPER, FLAT, OLD_NESTED)
    events_files: list[tuple[float, Path]] = []
    for events_path in runs_root.glob("**/meta/events.jsonl"):
        try:
            mtime = events_path.stat().st_mtime
            events_files.append((mtime, events_path))
        except OSError:  # noqa: PERF203 — fail-open per-file, one OSError shouldn't abort the glob
            continue

    events_files.sort(reverse=True)  # Most recent first
    reader = FileStateReader()

    for _mtime, events_path in events_files[:5]:
        records = reader.read_jsonl(events_path)
        for record in reversed(records):
            if str(record.get("event", "")) in ("run_init", "session_start"):
                ts_str = str(record.get("ts", ""))
                if ts_str:
                    try:
                        result = _ensure_utc(
                            datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        )
                        logger.debug(
                            "session_scope_resolved",
                            source=str(events_path),
                            ts=str(result),
                        )
                        return result
                    except ValueError:
                        continue

    logger.debug("session_scope_fallback_to_window")
    return None


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
) -> list[dict[str, object]]:
    """Read recall-tracking JSONL file, skipping malformed lines.

    PRD-FIX-061-FR05: Extracted from ``correlate_recalls`` so that
    ``_correlation.py`` does not need to import ``FileStateReader``
    from the state layer.

    Unlike ``FileStateReader.read_jsonl()`` which raises on the first
    malformed line, this function is lenient: bad lines are silently
    skipped so that one corrupt receipt does not abort the entire
    correlation pass.

    Args:
        receipt_path: Path to recall_tracking.jsonl.

    Returns:
        List of record dicts; empty list if file missing or unreadable.
    """
    import json

    if not receipt_path.exists():
        return []
    records: list[dict[str, object]] = []
    try:
        with receipt_path.open("r", encoding="utf-8") as fh:
            for line in fh:
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


__all__ = [
    "_PendingUpdate",
    "_batch_sync_to_sqlite",
    "_default_lookup_entry",
    "_find_session_start_ts",
    "_load_entries_from_dir",
    "_read_recall_tracking_jsonl",
    "_sync_to_sqlite",
    "_write_pending_entries",
]
