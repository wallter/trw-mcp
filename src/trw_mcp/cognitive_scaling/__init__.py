"""Planning-Phase Cognitive Scaling package (PRD-SCALE-001).

Sprint-97 scope (Phase 0 + Phase 1): the Scout — a deterministic, fail-open,
language-agnostic classifier that emits three grounded signals
(``blast_radius``, ``churn``, ``precedent_gap``), routes the session into a
``PlanningMode``, and writes the session-layer overlay
(``meta/session_profile.yaml``) that the H2 profile resolver consumes to make
ceremony dynamic per task.

Public facade — import Scout entry points from here:

    from trw_mcp.cognitive_scaling import classify, write_session_profile

Sprint-98 deferrals (drafts / rubric / synthesizer / dissent ledger / probe
execution) are intentionally NOT present in this package yet.
"""

from __future__ import annotations

from trw_mcp.cognitive_scaling.scout import (
    classify,
    propose_probe_budget,
    write_session_profile,
)

__all__ = [
    "classify",
    "propose_probe_budget",
    "write_session_profile",
]
