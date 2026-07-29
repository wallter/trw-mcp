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

    # The six flat phase_cap_* fields were removed 2026-07-28
    # (PRD-QUAL-131-FR01). Nothing read them: the live phase-cap values are the
    # defaults on ``PhaseTimeCaps`` in ``_sub_models.py``, which carries its own
    # ``research``/``plan``/``implement``/``validate_phase``/``review``/
    # ``deliver`` fields and never projected from these. Two encodings of the
    # same six numbers, one of them settable and inert.

    # -- Wave adaptation --

    adaptation_auto_approve_threshold: int = 5
