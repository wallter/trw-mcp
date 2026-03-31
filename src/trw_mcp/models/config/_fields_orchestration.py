"""Orchestration, phase time caps, and wave adaptation fields.

Covers sections 1, 2, 25 of the original _main_fields.py:
  - Orchestration
  - Phase time caps
  - Wave adaptation
"""

from __future__ import annotations

from trw_mcp.models.config._defaults import DEFAULT_PARALLELISM_MAX


class _OrchestrationFields:
    """Orchestration domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Orchestration --

    parallelism_max: int = DEFAULT_PARALLELISM_MAX
    timebox_hours: int = 8
    max_research_waves: int = 3
    min_shards_target: int = 3
    min_shards_floor: int = 2
    consensus_quorum: float = 0.67
    max_child_depth: int = 2
    checkpoint_secs: int = 600

    # -- Phase time caps --

    phase_cap_research: float = 0.25
    phase_cap_plan: float = 0.15
    phase_cap_implement: float = 0.35
    phase_cap_validate: float = 0.10
    phase_cap_review: float = 0.10
    phase_cap_deliver: float = 0.05

    # -- Wave adaptation --

    adaptation_enabled: bool = True
    max_total_waves: int = 8
    max_adaptations_per_run: int = 5
    max_shards_added_per_adaptation: int = 3
    adaptation_auto_approve_threshold: int = 5
