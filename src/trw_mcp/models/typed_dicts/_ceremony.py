"""Ceremony scoring, feedback, escalation, and delivery-gate TypedDicts."""

from __future__ import annotations

from typing import TypedDict, Optional


class AutoMaintenanceDict(TypedDict, total=False):
    """Return shape of ``run_auto_maintenance()``.

    All keys are optional — only populated when the corresponding maintenance
    operation produced a non-empty result.
    """

    update_advisory: str
    auto_upgrade: dict[str, object]
    stale_runs_closed: dict[str, object]
    embeddings_advisory: str
    embeddings_backfill: dict[str, int]


class DeliveryGatesDict(TypedDict, total=False):
    """Return shape of ``check_delivery_gates()``.

    All keys are optional — only populated when a gate or advisory fires.
    """

    review_warning: str
    review_advisory: str
    integration_review_block: str
    integration_review_warning: str
    untracked_warning: str
    build_gate_warning: str
    warning: str


class ComplianceArtifactsDict(TypedDict, total=False):
    """Return shape of ``copy_compliance_artifacts()``.

    Keys are present only when at least one artifact was copied.
    """

    compliance_artifacts_copied: list[str]
    compliance_dir: str


class ReflectResultDict(TypedDict):
    """Return shape of ``_do_reflect()`` in ceremony.py.

    Always-present keys — reflect is a synchronous critical-path step.
    """

    status: str
    events_analyzed: int
    learnings_produced: int
    success_patterns: int


class ClaudeMdSyncResultDict(TypedDict, total=False):
    """Return shape of ``_do_claude_md_sync()`` / ``execute_claude_md_sync()``.

    All keys are present on both the ``"synced"`` and ``"unchanged"`` paths.
    ``hash`` is only present on the ``"unchanged"`` (cache-hit) path.
    """

    path: str
    scope: str
    status: str
    learnings_promoted: int
    patterns_included: int
    total_lines: int
    llm_used: bool
    agents_md_synced: bool
    agents_md_path: str | None
    bounded_contexts_synced: int
    # cache-hit path only
    hash: str


class CeremonyScoreResult(TypedDict):
    """Return shape of ``compute_ceremony_score()``."""

    score: int
    session_start: bool
    deliver: bool
    checkpoint_count: int
    learn_count: int
    build_check: bool
    build_passed: bool | None


class CeremonyFeedbackEntry(TypedDict):
    """Single session outcome recorded in ceremony-feedback.yaml."""

    session_id: str
    run_path: str
    ceremony_score: float
    outcome_quality: float
    current_tier: str
    task_name: str
    task_class: str
    completed_at: str


class EscalationResult(TypedDict):
    """Return shape of ``check_auto_escalation()`` when escalation fires."""

    triggered: bool
    new_tier: str
    from_tier: str
    reason: str
    window_scores: list[float]
    threshold: float


class TierCeremonyScoreResult(TypedDict):
    """Return shape of ``compute_tier_ceremony_score()``."""

    score: int
    tier: str
    matched_events: int
    expected_events: int
    has_recall: bool
    has_init: bool
    checkpoint_count: int
    has_learn: bool
    has_build_check: bool
    has_deliver: bool
    has_review: bool


class ReductionProposalDict(TypedDict):
    """Shape of a ceremony reduction proposal from ``generate_reduction_proposal()``."""

    proposal_id: str
    task_class: str
    from_tier: str
    to_tier: str
    sample_count: int
    avg_ceremony_score: float
    avg_outcome_quality: float
    generated_at: str
    status: str


class CeremonyClassStatusDict(TypedDict):
    """Per-task-class status returned by ``_get_class_status()``."""

    task_class: str
    current_tier: str
    session_count: int
    avg_ceremony_score: Optional[float]
    avg_outcome_quality: Optional[float]
    proposals: list[ReductionProposalDict]
    auto_escalation: Optional[EscalationResult]
    warnings: list[str]


class CeremonyStatusResult(TypedDict):
    """Return shape of ``get_ceremony_status()`` and ``trw_ceremony_status``."""

    task_classes: list[CeremonyClassStatusDict]


class CeremonyApproveResult(TypedDict):
    """Return shape of ``approve_proposal()`` and ``trw_ceremony_approve``."""

    status: str
    change_id: str
    task_class: str
    new_tier: str


class CeremonyRevertResult(TypedDict):
    """Return shape of ``revert_change()`` and ``trw_ceremony_revert``."""

    status: str
    task_class: str
    restored_tier: str


class AutoRecalledItemDict(TypedDict, total=False):
    """Single entry in the phase-contextual auto-recall result list.

    Returned by ``_phase_contextual_recall()`` — a ranked subset of
    ``LearningEntryDict`` projected down to summary fields only.
    """

    id: str | None
    summary: str | None
    impact: float | None


class SessionRecallExtrasDict(TypedDict, total=False):
    """Extra metadata fields returned alongside learnings by ``perform_session_recalls()``.

    Keys present on the focused-query path: ``query``, ``query_matched``,
    ``total_available``.  Only ``total_available`` is always populated.
    """

    query: str
    query_matched: int
    total_available: int


class FinalizeRunResult(TypedDict, total=False):
    """Return shape of ``finalize_run()``.

    Currently always returns ``{}`` — placeholder for future run-close fields
    such as ``run_id``, ``closed_at``, ``archived_path``.
    """
