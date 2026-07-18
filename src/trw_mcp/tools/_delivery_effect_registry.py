"""Executable delivery side-effect registry — PRD-CORE-208 FR03.

Every synchronous, deferred, nested-external, and post-batch mutation the
``trw_deliver`` call path owns has exactly one immutable :class:`EffectDescriptor`
here. The registry is the single source of truth that drives replay wrappers and
the census tests: an observed mutation without a descriptor, a descriptor without
a reachable owner, or two descriptors claiming one boundary all fail the FR03
inventory gate.

This module is deliberately *data + pure query helpers* only — no I/O, no
subprocess, no network — so it can be imported by the journal, the wrappers, and
the tests without side effects. The approved live census is PRD-CORE-208 §6.6
(inspected 2026-07-09); descriptor IDs ``S01``-``S21`` and ``D00``-``D24`` mirror
that table exactly.

Replay-class truth (§6.4) is preserved, not overclaimed: a current effect that
lacks a transactional / idempotency-key / postcondition proof stays
``NON_REPLAYABLE`` (or ``DIAGNOSTIC`` / ``COORDINATION``) rather than being
relabelled idempotent. Downgrading a descriptor to a stricter class is allowed;
upgrading ``NON_REPLAYABLE`` to automatic replay requires a reviewed sink proof
and PRD revision.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ReplayClass(str, Enum):
    """Automatic-restart safety class for one delivery effect (§6.4).

    The class dictates what the FR04 recovery machinery may do with a ``started``
    step after a crash — never more than the registered proof allows.
    """

    TRANSACTIONAL = "transactional"
    KEYED_IDEMPOTENT = "keyed_idempotent"
    POSTCONDITION_PROVABLE = "postcondition_provable"
    NON_REPLAYABLE = "non_replayable"
    DIAGNOSTIC = "diagnostic"
    COORDINATION = "coordination"


#: Classes whose ``started``-after-crash steps may NEVER be automatically replayed.
#: Recovery marks them ``indeterminate`` and requires operator reconciliation.
NON_AUTO_REPLAY_CLASSES: frozenset[ReplayClass] = frozenset({ReplayClass.NON_REPLAYABLE})


class OperationStateImpact(str, Enum):
    """Whether an effect gates operation success or is advisory only."""

    REQUIRED = "required"  # must be succeeded/explicitly-skipped for operation success
    OPTIONAL = "optional"  # advisory; absence never blocks aggregate success


class EffectDescriptor(BaseModel):
    """One immutable delivery mutation boundary (§6.6 row).

    Frozen + strict to match the CORE-205 receipt substrate. ``proof_contract``
    is the machine-checkable evidence a wrapper must capture before the effect's
    step may finalize as ``succeeded``.
    """

    model_config = ConfigDict(strict=True, frozen=True)

    effect_id: str = Field(description="Stable census ID, e.g. 'S01' or 'D16'.")
    mutation: str = Field(description="Human description of the delivery-owned mutation.")
    owner_call_point: str = Field(description="Reachable current owner entrypoint.")
    impact: OperationStateImpact
    replay_class: ReplayClass
    proof_contract: str = Field(description="Evidence a wrapper must capture to finalize.")
    redaction_policy: str = Field(default="digest_only", description="What may persist to the journal.")


# Compact raw census (§6.6). Columns:
# (effect_id, mutation, owner_call_point, impact, replay_class, proof_contract)
_R = OperationStateImpact.REQUIRED
_O = OperationStateImpact.OPTIONAL
_TX = ReplayClass.TRANSACTIONAL
_KI = ReplayClass.KEYED_IDEMPOTENT
_PP = ReplayClass.POSTCONDITION_PROVABLE
_NR = ReplayClass.NON_REPLAYABLE
_DG = ReplayClass.DIAGNOSTIC
_CO = ReplayClass.COORDINATION

_CENSUS: tuple[tuple[str, str, str, OperationStateImpact, ReplayClass, str], ...] = (
    ("S01", "run phase write", "try_update_phase", _R, _PP, "phase value + operation marker"),
    ("S02", "ceremony phase mirror", "update_run_phase", _R, _PP, "state value/revision"),
    ("S03", "phase-enter event append", "update_run_phase", _O, _KI, "stable effect event id"),
    ("S04", "phase-transition telemetry enqueue", "update_run_phase", _O, _NR, "sink key or non-replayable"),
    (
        "S05",
        "review/integration compliance copies",
        "copy_compliance_artifacts",
        _R,
        _PP,
        "source/target digest per artifact",
    ),
    (
        "S06",
        "acceptable-failure override ledger",
        "write_override_ledger",
        _R,
        _PP,
        "stable operation/effect filename + payload digest",
    ),
    ("S07", "override event append", "_log_gate_override", _O, _KI, "stable effect event id"),
    ("S08", "mechanically extracted learning writes", "_do_reflect", _O, _NR, "per-learning stable sink proof"),
    ("S09", "reflection-complete event append", "_do_reflect", _O, _KI, "stable effect event id"),
    ("S10", "analytics session/learning counters", "update_analytics", _O, _NR, "operation-keyed upsert"),
    ("S11", "checkpoint record append", "_do_checkpoint", _O, _KI, "operation/effect id in record"),
    ("S12", "checkpoint event append", "_do_checkpoint", _O, _KI, "same effect id"),
    ("S13", "integrity-check event append", "_probe_integrity", _O, _DG, "diagnostic finding only"),
    ("S14", "CLEAR score JSON replace", "step_clear_score", _R, _PP, "canonical score digest"),
    ("S15", "knowledge topic synchronization", "step_knowledge_sync", _O, _PP, "target manifest/digests"),
    ("S16", "graph backfill", "step_knowledge_sync", _O, _NR, "stable edge upsert proof or non-replayable"),
    ("S17", "session changelog write", "step_session_changelog", _O, _PP, "content digest"),
    ("S18", "ceremony deliver-called flag", "mark_deliver", _R, _PP, "deliver_called=true + revision"),
    ("S19", "nudge-analysis JSON write", "_write_nudge_analysis_artifact", _O, _PP, "content digest"),
    ("S20", "delivery-complete event append", "_log_deliver_event", _R, _KI, "operation terminal event id"),
    (
        "S21",
        "structured application log emissions",
        "delivery_logger",
        _O,
        _DG,
        "diagnostic only; excluded from success proof",
    ),
    ("D00", "deferred lock-holder record", "_try_acquire_deferred_lock", _O, _CO, "lock/lease owner + liveness"),
    ("D01", "learning auto-prune mutations/audit", "_step_auto_prune", _O, _NR, "per-action proof"),
    ("D02", "learning consolidation mutations", "_step_consolidation", _O, _NR, "keyed actions"),
    ("D03", "tier sweep, impact assignment, purge", "_step_tier_sweep", _O, _NR, "stable per-transition proof"),
    ("D04", "requirements INDEX projection", "_do_index_sync", _O, _PP, "generated digest"),
    ("D05", "requirements ROADMAP projection", "_do_index_sync", _O, _PP, "generated digest"),
    (
        "D06",
        "automatic PRD lifecycle progression",
        "_step_auto_progress",
        _O,
        _PP,
        "operation-bound transition receipt",
    ),
    ("D07", "learning POST fan-out", "_step_publish_learnings", _O, _NR, "receiver idempotency/status proof"),
    ("D08", "learning publish-hash sidecar", "publish_learnings", _O, _PP, "local content digest; never proves D07"),
    ("D09", "outcome/Q correlation", "_step_outcome_correlation", _O, _NR, "operation-keyed correlation upsert"),
    ("D10", "recall positive-outcome append", "_step_recall_outcome", _O, _KI, "effect-id dedup append"),
    ("D11", "telemetry pipeline drain/stop", "_step_telemetry", _O, _CO, "coordination; not a send proof"),
    (
        "D12",
        "session/compliance telemetry record + flush",
        "_step_telemetry",
        _O,
        _NR,
        "receiver event-id status; local row keyable",
    ),
    ("D13", "session-summary event append", "_step_telemetry", _O, _KI, "operation/effect id"),
    ("D14", "telemetry batch POST fan-out", "_step_batch_send", _O, _NR, "receiver idempotency/status proof"),
    ("D15", "telemetry queue rewrite/drop", "BatchSender.send", _O, _PP, "local queue digest; cannot prove D14"),
    (
        "D16",
        "trust session/success counters",
        "_step_trust_increment",
        _R,
        _NR,
        "operation-keyed increment ledger/upsert",
    ),
    ("D17", "trust-tier transition audit", "_step_trust_increment", _O, _NR, "same atomic/keyed unit as D16"),
    ("D18", "ceremony feedback session append", "_step_ceremony_feedback", _O, _KI, "upsert by delivery operation id"),
    (
        "D19",
        "reduction proposal registration/persistence",
        "_process_ceremony_proposal",
        _O,
        _KI,
        "deterministic proposal/effect id",
    ),
    (
        "D20",
        "automatic ceremony escalation override",
        "apply_auto_escalation",
        _O,
        _PP,
        "target tier + source evidence digest",
    ),
    ("D21", "ceremony-change history append", "apply_auto_escalation", _O, _KI, "stable change/effect id"),
    ("D22", "session metrics persistence", "_persist_session_metrics", _O, _PP, "metrics digest"),
    ("D23", "deferred-results persistence", "_persist_deferred_results", _O, _PP, "operation/effect digest"),
    (
        "D24",
        "deferred-delivery audit append",
        "_log_deferred_result",
        _O,
        _KI,
        "batch change/effect id; diagnostic fallback",
    ),
)


def _build_registry() -> dict[str, EffectDescriptor]:
    registry: dict[str, EffectDescriptor] = {}
    for effect_id, mutation, owner, impact, replay_class, proof in _CENSUS:
        if effect_id in registry:  # pragma: no cover - guarded by frozen data + test
            raise ValueError(f"duplicate effect descriptor id: {effect_id}")
        registry[effect_id] = EffectDescriptor(
            effect_id=effect_id,
            mutation=mutation,
            owner_call_point=owner,
            impact=impact,
            replay_class=replay_class,
            proof_contract=proof,
        )
    return registry


#: The approved, immutable current delivery-effect inventory (§6.6).
DELIVERY_EFFECT_REGISTRY: dict[str, EffectDescriptor] = _build_registry()

#: Deferred roster IDs that FR03 requires to be represented (13 roster entries
#: D01-D13 plus post-batch D14-D24 and the D00 coordination lock).
DEFERRED_ROSTER_IDS: frozenset[str] = frozenset(
    d.effect_id for d in DELIVERY_EFFECT_REGISTRY.values() if d.effect_id.startswith("D")
)


def get_descriptor(effect_id: str) -> EffectDescriptor:
    """Return the descriptor for ``effect_id`` or raise ``KeyError`` (FR03)."""
    return DELIVERY_EFFECT_REGISTRY[effect_id]


def all_effect_ids() -> frozenset[str]:
    """Every registered census ID (S* and D*)."""
    return frozenset(DELIVERY_EFFECT_REGISTRY)


def required_effect_ids() -> frozenset[str]:
    """Effects that must be succeeded or explicitly skipped for operation success."""
    return frozenset(
        d.effect_id for d in DELIVERY_EFFECT_REGISTRY.values() if d.impact is OperationStateImpact.REQUIRED
    )


def effects_by_replay_class(replay_class: ReplayClass) -> tuple[EffectDescriptor, ...]:
    """All descriptors in one replay class, ID-sorted (deterministic)."""
    return tuple(
        sorted(
            (d for d in DELIVERY_EFFECT_REGISTRY.values() if d.replay_class is replay_class),
            key=lambda d: d.effect_id,
        )
    )


def is_auto_replayable_after_started(effect_id: str) -> bool:
    """False for classes that must become ``indeterminate`` after a crash (FR04).

    A ``NON_REPLAYABLE`` started effect is never automatically re-invoked; the
    registry — not a code comment — is the authority for that decision.
    """
    return DELIVERY_EFFECT_REGISTRY[effect_id].replay_class not in NON_AUTO_REPLAY_CLASSES


def reconcile_static_roster(observed_effect_ids: frozenset[str]) -> dict[str, tuple[str, ...]]:
    """Compare an observed mutation ID set against the registry (FR03 census).

    Returns a report with three ID-sorted tuples. A clean census has all three
    empty; any non-empty field fails the FR03 inventory gate:

    - ``missing``: registered descriptors with no observed owner mutation.
    - ``orphan``: observed mutations with no registered descriptor.
    - ``unclassified``: alias of ``orphan`` retained for the FR03 acceptance
      wording ("unclassified_mutations == 0").
    """
    registered = all_effect_ids()
    missing = tuple(sorted(registered - observed_effect_ids))
    orphan = tuple(sorted(observed_effect_ids - registered))
    return {"missing": missing, "orphan": orphan, "unclassified": orphan}
