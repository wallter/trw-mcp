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
    # min_shards_target, min_shards_floor, consensus_quorum, max_child_depth and checkpoint_secs
    # were removed 2026-09-16 (PRD-QUAL-139-FR05). The corrected consumer scan -- which now also
    # sees bundled-hook readers and flat fields aliased into a nested sub-model -- still found
    # no consumer; no PRD in docs/requirements-aare-f/prds names any of the five; and the only
    # tests were default pins. The keys are listed in trw_mcp/data/config-retired-keys.json so
    # an operator who set one is warned rather than silently ignored. max_research_waves followed
    # under PRD-CORE-291 (slice 2), removed together with its OrchestrationConfig mirror in
    # _sub_models.py so no live nested default is left behind.

    # The six flat phase_cap_* fields were removed 2026-07-28
    # (PRD-QUAL-131-FR01). Nothing read them: the live phase-cap values are the
    # defaults on ``PhaseTimeCaps`` in ``_sub_models.py``, which carries its own
    # ``research``/``plan``/``implement``/``validate_phase``/``review``/
    # ``deliver`` fields and never projected from these. Two encodings of the
    # same six numbers, one of them settable and inert.

    # adaptation_auto_approve_threshold (the former "Wave adaptation" section)
    # was removed under PRD-CORE-291 (slice 2): no production reader.
