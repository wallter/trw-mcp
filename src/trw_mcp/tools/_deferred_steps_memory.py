"""Memory-related deferred delivery steps.

Sub-module of ``_deferred_delivery`` — contains steps for auto-pruning,
consolidation, and tier lifecycle sweeps.

Test patches should still target the parent facade:
``patch("trw_mcp.tools._deferred_delivery._step_auto_prune")``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.typed_dicts import (
    ConsolidationStepResult,
    TierSweepStepResult,
)
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools import _deferred_state as _ds

logger = structlog.get_logger(__name__)


def _step_auto_prune(trw_dir: Path) -> dict[str, object] | None:
    """Step 2.5: Auto-prune excess learnings.

    Throttle: when ``learning_auto_prune_min_interval_hours > 0`` and the
    last successful run is younger than the window, skip without scanning.
    A scan over 1k+ entries holds the SQLite writer lock for many
    seconds, so we do not pay that cost every trw_deliver.

    Deadline + cancellation: pass the per-step wall-clock budget and the
    watchdog's cancellation event to ``auto_prune_excess_entries`` so a
    runaway pass returns its partial result instead of pegging the worker.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state.analytics import auto_prune_excess_entries

    config = get_config()
    if not config.learning_auto_prune_on_deliver:
        return None

    now = time.monotonic()
    min_interval_s = max(int(config.learning_auto_prune_min_interval_hours), 0) * 3600
    last_run = _ds._last_auto_prune_at
    if min_interval_s > 0 and last_run is not None:
        elapsed = now - last_run
        if elapsed < min_interval_s:
            remaining_s = int(min_interval_s - elapsed)
            return {
                "status": "throttled",
                "reason": "min_interval",
                "elapsed_seconds": int(elapsed),
                "next_run_in_seconds": remaining_s,
            }

    deadline_s = max(int(config.learning_auto_prune_max_seconds), 1)
    prune_result = auto_prune_excess_entries(
        trw_dir,
        max_entries=config.learning_auto_prune_cap,
        deadline_seconds=deadline_s,
        cancel_event=_ds._cancel_event,
    )
    # Record success even on deadline_exceeded — the partial pass did
    # whatever work it could and the next pass should still respect the
    # throttle so the worker isn't pegged by repeated short partials.
    _ds._last_auto_prune_at = now
    pruned = int(str(prune_result.get("actions_taken", 0)))
    if pruned == 0 and prune_result.get("status") not in ("deadline_exceeded", "cancelled"):
        return None
    return prune_result


def _step_consolidation(trw_dir: Path) -> ConsolidationStepResult:
    """Step 2.6: Memory consolidation (PRD-CORE-044)."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.consolidation import consolidate_cycle

    config = get_config()
    if not config.memory_consolidation_enabled:
        return cast("ConsolidationStepResult", {"status": "skipped", "reason": "disabled"})

    return cast(
        "ConsolidationStepResult",
        dict(
            consolidate_cycle(
                trw_dir,
                max_entries=config.memory_consolidation_max_per_cycle,
                allow_cold_embedder_load=False,
            )
        ),
    )


def _step_tier_sweep(trw_dir: Path) -> TierSweepStepResult:
    """Step 2.7: Tier lifecycle sweep (PRD-CORE-043) + impact tier assignment (PRD-FIX-052-FR07)."""
    from trw_mcp.state.persistence import FileStateWriter
    from trw_mcp.state.tiers import TierManager

    reader = FileStateReader()
    writer = FileStateWriter()
    tier_mgr = TierManager(trw_dir, reader, writer)
    sweep_result = tier_mgr.sweep()

    # PRD-FIX-052-FR07: assign impact_tier labels to all active entries post-sweep
    tier_distribution = tier_mgr.assign_impact_tiers(trw_dir)

    return {
        "status": "success",
        "promoted": sweep_result.promoted,
        "demoted": sweep_result.demoted,
        "purged": sweep_result.purged,
        "errors": sweep_result.errors,
        "impact_tier_distribution": tier_distribution,
    }
