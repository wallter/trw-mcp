"""Tier sweep lifecycle methods (extracted from tiers.py).

Implements FR04/FR06 — Hot-to-Warm, Warm-to-Cold, Cold-to-Purge transitions.

These are standalone functions that accept a ``TierManager`` instance as the
first argument (``self``).  They are assigned to ``TierManager`` as methods by
``tiers.py`` so that existing callers (``mgr.sweep()``) and test patches
(``trw_mcp.state.tiers.TierManager.sweep``) continue to work unchanged.

Parent facade: ``trw_mcp.state.tiers``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from trw_memory.lifecycle.tiers import TierSweepResult

from trw_mcp.models.config import TRWConfig, get_config
from trw_mcp.scoring import _days_since_access
from trw_mcp.state._helpers import iter_yaml_entry_files
from trw_mcp.state._tier_scoring import compute_importance_score

logger = structlog.get_logger(__name__)

# Type alias for TierManager — using Any to avoid circular import with tiers.py.
# These functions are assigned as methods on TierManager by tiers.py, so runtime
# type safety is guaranteed by the class definition itself.
_TierManagerSelf = Any


def _sweep_hot_to_warm(
    self: _TierManagerSelf,
    cfg: TRWConfig,
    today: date,
) -> tuple[int, int]:
    """Phase 1: evict stale hot-tier entries into warm tier.

    Identifies entries whose last_accessed_at exceeds
    ``memory_hot_ttl_days``, moves them to the warm tier,
    and flushes last_accessed_at to disk.

    Args:
        cfg: Resolved TRWConfig for threshold values.
        today: Reference date for staleness checks.

    Returns:
        Tuple of (demoted, errors) counts.
    """
    demoted = 0
    errors = 0

    stale_hot_ids: list[str] = []
    for entry_id, entry in list(self._hot.items()):
        days = _days_since_access(entry.model_dump(), today)
        if days > cfg.memory_hot_ttl_days:
            stale_hot_ids.append(entry_id)

    for entry_id in stale_hot_ids:
        try:
            evicted = self._hot.pop(entry_id)
            self.warm_add(entry_id, evicted.model_dump(), None)
            self._flush_last_accessed(entry_id)
            demoted += 1
            logger.debug("sweep_hot_to_warm", entry_id=entry_id)
        except Exception:  # per-item error handling: one failed eviction must not abort the sweep  # noqa: PERF203
            logger.warning("sweep_hot_to_warm_failed", entry_id=entry_id, exc_info=True)
            errors += 1

    return demoted, errors


def _sweep_warm_to_cold(
    self: _TierManagerSelf,
    cfg: TRWConfig,
    today: date,
    entries_dir: Path,
) -> tuple[int, int]:
    """Phase 2: demote idle low-importance warm entries to cold archive.

    PRD-FIX-033-FR05: Uses SQLite via ``list_active_learnings`` when
    available, falling back to YAML glob on error.
    Uses ``compute_importance_score`` (Stanford Generative Agents formula)
    instead of raw impact for more nuanced tier transition decisions.

    Args:
        cfg: Resolved TRWConfig for threshold values.
        today: Reference date for staleness checks.
        entries_dir: Path to the warm-tier YAML entries directory.

    Returns:
        Tuple of (demoted, errors) counts.
    """
    demoted = 0
    errors = 0

    _used_sqlite = False
    try:
        from trw_mcp.state.memory_adapter import (
            find_yaml_path_for_entry,
            list_active_learnings,
        )

        active_entries = list_active_learnings(self._trw_dir)
        for data in active_entries:
            entry_id = str(data.get("id", ""))
            if not entry_id:
                continue
            try:
                days = _days_since_access(data, today)
                importance = compute_importance_score(data, [], config=cfg)
                if days > cfg.memory_cold_threshold_days and importance < 0.22:
                    # Resolve YAML path for cold_archive
                    yaml_file = find_yaml_path_for_entry(self._trw_dir, entry_id)
                    if yaml_file is None:
                        logger.warning(
                            "sweep_warm_to_cold_no_yaml",
                            entry_id=entry_id,
                        )
                        continue
                    self.cold_archive(entry_id, yaml_file)
                    demoted += 1
                    logger.debug(
                        "sweep_warm_to_cold",
                        entry_id=entry_id,
                        days=days,
                        importance_score=importance,
                    )
            except Exception:  # justified: scan-resilience, one failed demotion must not abort the sweep
                logger.warning(
                    "sweep_warm_to_cold_failed",
                    entry_id=entry_id,
                    exc_info=True,
                )
                errors += 1
        _used_sqlite = True
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "sqlite_read_fallback",
            step="sweep_warm_to_cold",
            reason="list_active_learnings failed",
        )

    if not _used_sqlite and entries_dir.exists():
        # YAML fallback path (original implementation)
        for yaml_file in iter_yaml_entry_files(entries_dir):
            try:
                yaml_data = self._reader.read_yaml(yaml_file)
                entry_id = str(yaml_data.get("id", ""))
                if not entry_id:
                    continue
                # Skip non-active entries
                if str(yaml_data.get("status", "active")) != "active":
                    continue
                days = _days_since_access(yaml_data, today)
                importance = compute_importance_score(yaml_data, [], config=cfg)
                if days > cfg.memory_cold_threshold_days and importance < 0.22:
                    self.cold_archive(entry_id, yaml_file)
                    demoted += 1
                    logger.debug(
                        "sweep_warm_to_cold",
                        entry_id=entry_id,
                        days=days,
                        importance_score=importance,
                    )
            except Exception:  # justified: scan-resilience, one corrupt YAML must not abort the fallback sweep
                logger.warning(
                    "sweep_warm_to_cold_failed",
                    path=str(yaml_file),
                    exc_info=True,
                )
                errors += 1

    return demoted, errors


def _sweep_cold_to_purge(
    self: _TierManagerSelf,
    cfg: TRWConfig,
    today: date,
    purge_audit_path: Path,
) -> tuple[int, int]:
    """Phase 3: purge expired cold-tier entries past retention.

    Scans the cold archive for entries idle longer than
    ``memory_retention_days`` with importance below 0.1. Writes
    an audit record to ``purge_audit_path`` before deletion.

    Uses ``compute_importance_score`` for purge decisions.

    Args:
        cfg: Resolved TRWConfig for threshold values.
        today: Reference date for staleness checks.
        purge_audit_path: JSONL file for purge audit records.

    Returns:
        Tuple of (purged, errors) counts.
    """
    purged = 0
    errors = 0

    cold_base = self._cold_dir()
    if cold_base.exists():
        for yaml_file in sorted(cold_base.rglob("*.yaml")):
            try:
                data = self._reader.read_yaml(yaml_file)
                entry_id = str(data.get("id", ""))
                days = _days_since_access(data, today)
                importance = compute_importance_score(data, [], config=cfg)
                if days > cfg.memory_retention_days and importance < 0.1:
                    # Append to purge audit log before deleting
                    audit_record: dict[str, object] = {
                        "entry_id": entry_id,
                        "purged_at": datetime.now(timezone.utc).isoformat(),
                        "days_idle": days,
                        "importance_score": importance,
                        "impact": float(str(data.get("impact", 0.5))),
                        "summary": str(data.get("summary", "")),
                    }
                    purge_audit_path.parent.mkdir(parents=True, exist_ok=True)
                    with purge_audit_path.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(audit_record) + "\n")
                    yaml_file.unlink(missing_ok=True)
                    purged += 1
                    logger.debug(
                        "sweep_cold_purge",
                        entry_id=entry_id,
                        days=days,
                        importance_score=importance,
                    )
            except (
                Exception
            ):  # per-item error handling: skip unreadable cold files, continue purge sweep
                logger.warning(
                    "sweep_cold_purge_failed",
                    path=str(yaml_file),
                    exc_info=True,
                )
                errors += 1

    return purged, errors


def sweep(self: _TierManagerSelf) -> TierSweepResult:
    """Execute lifecycle sweep across all tiers.

    Performs four transition checks in order:
    1. Hot -> Warm: entries whose last_accessed_at exceeds memory_hot_ttl_days.
    2. Warm -> Cold: entries idle > memory_cold_threshold_days with impact < 0.5.
    3. Cold -> Purge: entries idle > memory_retention_days with impact < 0.3.
    4. Cold -> Warm is handled on-demand by cold_promote().

    All thresholds are read from get_config() at call time (FR06).
    Per-entry failures are logged and counted in ``errors``; the sweep
    continues with remaining entries.

    Returns:
        TierSweepResult with counts of promoted, demoted, purged, and errors.
    """
    cfg = get_config()
    today = datetime.now(tz=timezone.utc).date()

    entries_dir = self._trw_dir / cfg.learnings_dir / cfg.entries_dir
    purge_audit_path = self._trw_dir / "memory" / "purge_audit.jsonl"

    # Phase 1: Hot -> Warm
    hot_demoted, hot_errors = self._sweep_hot_to_warm(cfg, today)

    # Phase 2: Warm -> Cold
    warm_demoted, warm_errors = self._sweep_warm_to_cold(
        cfg,
        today,
        entries_dir,
    )

    # Phase 3: Cold -> Purge
    purged, purge_errors = self._sweep_cold_to_purge(
        cfg,
        today,
        purge_audit_path,
    )

    demoted = hot_demoted + warm_demoted
    errors = hot_errors + warm_errors + purge_errors
    promoted = 0

    logger.info(
        "tier_sweep_complete",
        promoted=promoted,
        demoted=demoted,
        purged=purged,
        errors=errors,
    )
    return TierSweepResult(
        promoted=promoted,
        demoted=demoted,
        purged=purged,
        errors=errors,
    )
