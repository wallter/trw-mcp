"""Formation config fields (PRD-CORE-265-NFR04).

Five typed, bounded knobs. Each is the kill path for one enforcement surface:
setting ``formation_ownership_enforcement`` to ``warn`` disarms the commit
boundary, ``formation_hook_ownership_mode`` to ``off`` disarms the hook, and
``formation_deliver_gate`` to ``advisory`` disarms the delivery gate — none of
which needs a code change or a redeploy. The other two are bounds, not
switches: a member cap and a lock timeout, stated here so neither is a literal
buried in the package.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field


class _FormationFields:
    """Formation domain mixin — mixed into _TRWConfigFields via MI."""

    #: Commit-boundary enforcement (FR09). ``refuse`` is the default because the
    #: commit is the ONLY ownership surface every client crosses; ``warn`` keeps
    #: the message and lets the commit through, which is the documented rollback
    #: for a legitimate cross-cutting edit. ``block`` is not spelled differently
    #: from ``refuse`` on purpose — one vocabulary, two values.
    formation_ownership_enforcement: Literal["refuse", "warn"] = "refuse"

    #: Hook advisory (FR10). ``block`` is deliberately absent from the
    #: vocabulary: PreToolUse delivery is not reliable on every supported
    #: client, and a gate that fires on some clients and not others teaches
    #: agents to distrust it.
    formation_hook_ownership_mode: Literal["warn", "off"] = "warn"

    #: Orchestrator delivery gate (FR11). ``block`` by default: an orchestrator
    #: delivering while a member is mid-implementation is a completion claim
    #: that outruns its evidence. ``advisory`` surfaces the same condition as a
    #: warning and is the rollback lever if the gate ever blocks wrongly.
    formation_deliver_gate: Literal["block", "advisory"] = "block"

    #: Rows the status roll-up will assemble (FR07/NFR01). 16 is the largest
    #: formation the 2-second SLO was stated for; the ceiling of 64 bounds the
    #: per-member run reads a hostile or mistaken manifest could demand.
    formation_status_member_limit: int = Field(
        default=16, ge=1, le=64, description="Maximum member rows assembled by the formation status roll-up."
    )

    #: Bounded wait for the manifest lock (FR04/NFR02). A timeout REFUSES rather
    #: than writing unlocked; 10s absorbs a concurrent join without making a
    #: crashed lock holder feel like a hang.
    formation_manifest_lock_timeout_seconds: float = Field(
        default=10.0, ge=0.5, le=120.0, description="Bounded wait for the formation manifest lock, in seconds."
    )
