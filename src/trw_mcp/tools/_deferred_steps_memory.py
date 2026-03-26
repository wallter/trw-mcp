"""Memory-related deferred delivery steps.

Sub-module of ``_deferred_delivery`` — contains steps for auto-pruning,
consolidation, and tier lifecycle sweeps.

Test patches should still target the parent facade:
``patch("trw_mcp.tools._deferred_delivery._step_auto_prune")``.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from trw_mcp.models.typed_dicts import (
    ConsolidationStepResult,
    TierSweepStepResult,
)
from trw_mcp.state.persistence import FileStateReader


def _step_auto_prune(trw_dir: Path) -> dict[str, object] | None:
    """Step 2.5: Auto-prune excess learnings."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state.analytics import auto_prune_excess_entries

    config = get_config()
    if not config.learning_auto_prune_on_deliver:
        return None

    prune_result = auto_prune_excess_entries(
        trw_dir,
        max_entries=config.learning_auto_prune_cap,
    )
    pruned = int(str(prune_result.get("actions_taken", 0)))
    return prune_result if pruned > 0 else None


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
