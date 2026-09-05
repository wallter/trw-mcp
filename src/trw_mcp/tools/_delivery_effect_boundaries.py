"""Declared crash boundary per delivery effect — PRD-FIX-127 FR03.

Belongs to the ``tools/_delivery_effect_registry.py`` module, which imports this
mapping and makes :class:`EffectBoundary` a REQUIRED field on every descriptor.
Split out only so the registry stays under the 350 effective-LOC gate.

Exactly three legal forms, and the registry rejects anything else at import:

- ``own`` — a ``begin_step`` / ``finalize_step`` pair exists at the effect's real
  call site, so a crash inside the effect leaves a durable ``started`` row.
- ``shared_with: <effect_id>`` — the effect is reached only from inside a named
  host's open boundary, so a crash cannot separate the two: the host's step row
  IS this effect's crash evidence. The host must itself declare ``own``.
- ``unjournaled`` — the write is not evidence of anything, permitted only when the
  descriptor's replay class is ``diagnostic`` or ``coordination``.

``own`` is required whenever an effect can be reached independently of any other
journaled write, or is marked ``required``; the two ``required`` effects that had
no boundary before this PRD (``S02`` ceremony phase mirror, ``S06`` acceptable-
failure override ledger) therefore take ``own`` and gained a real call-site
boundary rather than a label.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class BoundaryKind(str, Enum):
    """The closed set of crash-boundary declarations (FR03)."""

    OWN = "own"
    SHARED_WITH = "shared_with"
    UNJOURNALED = "unjournaled"


class EffectBoundary(BaseModel):
    """One effect's declared crash boundary, with the rationale that settled it."""

    model_config = ConfigDict(strict=True, frozen=True)

    kind: BoundaryKind
    host_effect_id: str = Field(default="", description="Only set (and required) for shared_with.")
    rationale: str = Field(min_length=1, description="Why this form is the honest one for this effect.")


def _own(rationale: str) -> EffectBoundary:
    return EffectBoundary(kind=BoundaryKind.OWN, rationale=rationale)


def _shared(host: str, rationale: str) -> EffectBoundary:
    return EffectBoundary(kind=BoundaryKind.SHARED_WITH, host_effect_id=host, rationale=rationale)


def _unjournaled(rationale: str) -> EffectBoundary:
    return EffectBoundary(kind=BoundaryKind.UNJOURNALED, rationale=rationale)


_NESTED = "reached only from inside the host boundary; a crash cannot separate them"

#: Every §6.6 census ID -> its declared crash boundary. The registry raises at
#: import when an ID is missing here, so a NEW descriptor cannot be added without
#: declaring one (that omission is the defect FR03 exists to close).
EFFECT_BOUNDARIES: dict[str, EffectBoundary] = {
    "S01": _own("journal.step('S01') wraps try_update_phase in run_trw_deliver"),
    "S02": _own("required effect; journal.step('S02') wraps the ceremony phase mirror in update_run_phase"),
    "S03": _shared("S01", f"phase_enter append inside update_run_phase, {_NESTED}"),
    "S04": _shared("S01", f"phase-transition enqueue inside update_run_phase, {_NESTED}"),
    "S05": _own("journal.step('S05') wraps copy_compliance_artifacts in run_trw_deliver"),
    "S06": _own("required effect; journal.step('S06') wraps apply_structured_override's ledger write"),
    "S07": _own("_log_gate_override runs after the S06 boundary closes, so it needs its own"),
    "S08": _own("journal.step('S08') wraps _do_reflect in run_trw_deliver"),
    "S09": _shared("S08", f"reflection-complete append inside _do_reflect, {_NESTED}"),
    "S10": _shared("S08", f"analytics counters inside _do_reflect, {_NESTED}"),
    "S11": _own("journal.step('S11') wraps _step_checkpoint in run_trw_deliver"),
    "S12": _shared("S11", f"checkpoint event append inside _do_checkpoint, {_NESTED}"),
    "S13": _unjournaled("integrity probe is diagnostic; its finding is never success evidence"),
    "S14": _own("journal.step('S14') wraps step_clear_score in run_trw_deliver"),
    "S15": _own("journal.step('S15') wraps step_knowledge_sync in run_trw_deliver"),
    "S16": _shared("S15", f"graph backfill inside step_knowledge_sync, {_NESTED}"),
    "S17": _own("journal.step('S17') wraps step_session_changelog in run_trw_deliver"),
    "S18": _own("journal.step('S18') wraps mark_deliver in run_trw_deliver"),
    "S19": _own("journal.step('S19') wraps _write_nudge_analysis_artifact in run_trw_deliver"),
    "S20": _own("journal.step('S20') wraps _log_deliver_event in run_trw_deliver"),
    "S21": _unjournaled("structured application logs are diagnostic and excluded from success proof"),
    "S22": _own("journal.step('S22') wraps step_project_handoff in run_trw_deliver"),
    "S23": _own("journal_step('S23') wraps _persist_decision_set inside the gate dispatcher"),
    "D00": _unjournaled("deferred lock record is coordination; liveness is its own proof"),
    "D01": _own("deferred chokepoint journals the auto_prune roster step"),
    "D02": _own("deferred chokepoint journals the consolidation roster step"),
    "D03": _own("deferred chokepoint journals the tier_sweep roster step"),
    "D04": _own("deferred chokepoint journals the index_sync roster step"),
    "D05": _shared("D04", f"ROADMAP projection inside _do_index_sync, {_NESTED}"),
    "D06": _own("deferred chokepoint journals the auto_progress roster step"),
    "D07": _own("deferred chokepoint journals the publish_learnings roster step"),
    "D08": _shared("D07", f"publish-hash sidecar inside publish_learnings, {_NESTED}"),
    "D09": _own("deferred chokepoint journals the outcome_correlation roster step"),
    "D10": _own("deferred chokepoint journals the recall_outcome roster step"),
    "D11": _unjournaled("pipeline drain/stop is coordination, never a send proof"),
    "D12": _own("deferred chokepoint journals the telemetry roster step"),
    "D13": _shared("D12", f"session-summary append inside _step_telemetry, {_NESTED}"),
    "D14": _own("deferred chokepoint journals the batch_send roster step"),
    "D15": _shared("D14", f"queue rewrite inside BatchSender.send, {_NESTED}"),
    "D16": _own("deferred chokepoint journals the trust_increment roster step"),
    "D17": _shared("D16", f"tier-transition audit inside _step_trust_increment, {_NESTED}"),
    "D18": _own("deferred chokepoint journals the ceremony_feedback roster step"),
    "D19": _shared("D18", f"proposal persistence inside _step_ceremony_feedback, {_NESTED}"),
    "D20": _shared("D18", f"auto-escalation override inside _step_ceremony_feedback, {_NESTED}"),
    "D21": _shared("D18", f"ceremony-change append inside apply_auto_escalation, {_NESTED}"),
    "D22": _own("journal.step('D22') wraps _persist_session_metrics, the run-yaml write it names"),
    "D23": _own("journal.step('D23') wraps _persist_deferred_results BEFORE the terminal transition"),
    "D24": _own("journal.step('D24') wraps _log_deferred_result BEFORE the terminal transition"),
    "D26": _own("deferred chokepoint journals the delivery_metrics roster step"),
    "D25": _own("deferred chokepoint journals the memory_decay roster step"),
}
