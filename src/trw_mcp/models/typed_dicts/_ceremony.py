"""Ceremony scoring, feedback, escalation, and delivery-gate TypedDicts."""

from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict


class AutoMaintenanceDict(TypedDict, total=False):
    """Return shape of ``run_auto_maintenance()``.

    All keys are optional — only populated when the corresponding maintenance
    operation produced a non-empty result.
    """

    update_advisory: str
    auto_upgrade: dict[str, object]
    stale_runs_closed: dict[str, object]
    # Learn write-ahead-journal recovery: only present when a prior interrupted
    # session left accepted-but-unstored learnings to replay (omit-when-empty).
    pending_learns_replayed: dict[str, object]


class ReconciledLocalWritesDict(TypedDict):
    """The ``reconciled_local_writes`` field on the session-start result (PRD-CORE-247-FR05).

    ``pending`` is what this session was told about; ``cleared`` is how many of
    those the tag was actually removed from. They differ only when a clear
    failed, which is precisely the signal an operator needs — a count that keeps
    rising across sessions means the clear step is broken, not that offline
    writing is busy.
    """

    pending: int
    learning_ids: list[str]
    cleared: int


class OpenHandoffItemDict(TypedDict):
    """One open project-handoff row as the calling agent sees it (PRD-CORE-249-FR03)."""

    gate_id: str
    blocking_class: str
    owner: str
    run_id: str
    reason: str
    first_seen: str
    age_days: int


class OpenHandoffDict(TypedDict, total=False):
    """The ``open_handoff`` field on the session-start result (PRD-CORE-249-FR03).

    ``status`` is ``measured``, ``not_measured``, or ``absent``. ``total`` is
    present only when ``status`` is ``measured`` — a ``not_measured`` block must
    never be indistinguishable from zero open items.
    """

    status: str
    reason: str
    total: int
    items: list[OpenHandoffItemDict]
    path: str


class MovedCheckoutCandidateDict(TypedDict):
    """A populated same-slug namespace that this checkout may have come from."""

    namespace: str
    rows: int


class MovedCheckoutDict(TypedDict, total=False):
    """The ``moved_checkout`` observation as the calling agent sees it (PRD-CORE-253-FR01).

    ``status`` is the tri-state and is ALWAYS present:

    * ``"measured"`` — the census ran and found a same-slug populated sibling.
      The observation fields below are populated.
    * ``"absent"`` — the census ran and found nothing to report.
    * ``"not_measured"`` — the census could not run (no user store, or a census
      or namespace-resolution error); ``reason`` names the cause.

    Before this, a failed census returned the same "nothing to report" as a
    successful one, so the session-start key was omitted identically in both
    cases — reintroducing exactly the ambiguity between "no memory" and
    "the memory is one rename away" that this feature exists to remove.
    """

    status: str
    reason: str
    current_namespace: str
    current_rows: int
    candidates: list[MovedCheckoutCandidateDict]
    repair_command: str


class DeliveryGatesDict(TypedDict, total=False):
    """Return shape of ``check_delivery_gates()``.

    All keys are optional — only populated when a gate or advisory fires.
    """

    review_block: str
    review_warning: str
    review_advisory: str
    # PRD-CORE-255-FR05: the typed ReviewReceipt that SATISFIED the review gate —
    # {receipt_id, scope_digest, age_seconds}. Present only when a typed receipt
    # was actually trusted, so absence means "no receipt satisfied the gate",
    # never "one did but is unnamed". Caller-actionable (which review, how
    # fresh), so it stays in the default response, not behind verbose=True.
    review_evidence: dict[str, object]
    # PRD-CORE-192-FR04: pre-deliver REVIEW nudge for a STANDARD+ run with no review.
    review_nudge: str
    review_scope_block: str
    integration_review_block: str
    integration_review_warning: str
    untracked_warning: str
    build_gate_warning: str
    build_gate_block: str
    build_gate_override: str
    checkpoint_blocker_warning: str
    warning: str
    complexity_drift_warning: str
    instruction_parity_warning: str
    # PRD-CORE-184-FR03: task-type-aware deliver gate mode.
    delivery_blocked: str
    missing_gate: str
    # PRD-SEC-013-FR07: open intent-contract violation blocks delivery until dispositioned.
    intent_violation_block: str
    # PRD-CORE-255-FR04: safety-critical PRD scope with no settled adversarial-audit
    # receipt. ``_block`` is the STRUCTURED hard block; ``_advisory`` is the same
    # shortfall under an advisory deliver-gate mode, or the inert "this run
    # declared no PRD scope" notice. ``safety_critical`` carries the FR03
    # resolution word — currently only ``not_declared``, since ``true``/``unknown``
    # already speak through ``_block``/``_advisory`` and ``false`` is silence.
    safety_critical_adversarial_block: str
    safety_critical_adversarial_advisory: str
    safety_critical: str
    blocked_task_type: str
    # PRD-CORE-244-FR06: a learning this session disproved and never retracted.
    # ADVISORY — it names entry ids and never sets a blocking condition.
    retraction_nudge: str


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


class _ReviewMdResultRequired(TypedDict):
    """Return shape of ``generate_review_md()``."""

    status: Literal["generated", "failed", "skipped"]  # skipped: a dry run (B71-110)
    path: str | None
    rules_count: int


class ReviewMdResultDict(_ReviewMdResultRequired, total=False):
    """Optional error details returned by ``generate_review_md()``."""

    error: str


class InstructionPointerSkipDict(TypedDict):
    """One single-source pointer file left un-clobbered by sync (PRD-CORE-203 FR07)."""

    path: str
    import_targets: list[str]
    healed: bool


#: Where an instruction-file write came from (PRD-FIX-123-FR05). Supplied by the
#: entry point (tool, bootstrap init, bootstrap update); never inferred from a
#: stack walk. ``unknown`` is a declared member, not a failure mode: provenance
#: is emitted even when the trigger cannot be resolved.
InstructionWriteTrigger = Literal[
    "tool_call",
    "deliver",
    "bootstrap_init",
    "bootstrap_update",
    "agent_tool_grant",
    "unknown",
]

#: Why a guarded instruction-file write was refused (PRD-FIX-123-FR01/FR02/NFR02).
InstructionRefusalReason = Literal[
    "oversized",
    "non_generated_shrink",
    "total_shrink",
    "unreadable_target",
    "backup_failed",
    "backup_path_escape",
    "write_failed",
]


class InstructionWriteRefusalDict(TypedDict):
    """A refused instruction-file write (PRD-FIX-123-FR01/FR02).

    Structurally a superset of ``InstructionSurfaceOversizedError``
    (``state/claude_md/_agents_md_size_gate.py``): ``error_code``, ``file``,
    ``lines`` and ``limit`` carry the same meaning, so an oversize refusal is
    readable by callers that already understand the size-gate shape. The byte
    counters are the FR02 evidence — the incident this PRD fixes GREW the file
    while destroying user content, so only the non-generated counters detect it.
    """

    error_code: Literal["instruction_surface_oversized", "instruction_write_refused"]
    file: str
    reason: InstructionRefusalReason
    lines: int
    limit: int
    current_non_generated_bytes: int
    candidate_non_generated_bytes: int
    current_total_bytes: int
    candidate_total_bytes: int
    detail: str


class InstructionDiffDict(TypedDict):
    """One target's dry-run unified diff (PRD-FIX-123-FR03).

    ``diff_truncated`` states that the DIFF was bounded to ``diff_line_cap``
    lines. A FILE is never truncated by TRW; only this preview payload is.
    """

    file: str
    diff: str
    diff_truncated: bool
    diff_line_cap: int


class _ClaudeMdSyncResultRequired(TypedDict):
    """Return shape of ``_do_instruction_sync()`` / ``execute_claude_md_sync()``.

    All keys are present on the ``"synced"``, ``"unchanged"``, and wrapped
    ``"success"`` paths. ``hash`` is only present on the ``"unchanged"``
    (cache-hit) path.
    """

    path: str
    scope: str
    status: Literal["synced", "unchanged", "success", "dry_run", "refused"]
    learnings_promoted: int
    patterns_included: int
    total_lines: int
    llm_used: bool
    agents_md_synced: bool
    agents_md_path: str | None
    instruction_file_synced: bool
    instruction_file_path: str | None
    instruction_file_paths: list[str]
    bounded_contexts_synced: int
    review_md: ReviewMdResultDict


class ClaudeMdSyncResultDict(_ClaudeMdSyncResultRequired, total=False):
    """Optional metadata returned by ``execute_claude_md_sync()``.

    ``hash`` is present on the ``"unchanged"`` (cache-hit) path. The PRD-CORE-203
    FR07 carrier-detectability fields (``carrier_mode``, ``pointer_skips``) are
    present on the render path when CLAUDE.md is written.
    """

    hash: str
    # PRD-CORE-203 FR07: how the TRW block was delivered into CLAUDE.md
    # (``"inline"`` | ``"pointer_skip"``).
    carrier_mode: str
    # Single-source pointer files left un-clobbered (with their import targets).
    pointer_skips: list[InstructionPointerSkipDict]
    # PRD-CORE-218-FR06: capability-projection parity drift detail strings.
    # Present (possibly empty) whenever AGENTS.md is a write target. A non-empty
    # list means the generated capability listing diverged from the resolved
    # surface manifest and the capability block was dropped fail-loud.
    capability_parity_drift: list[str]
    # PRD-FIX-123-FR03: per-target unified diffs produced by a ``dry_run=True``
    # sync. Present (possibly empty) only on the ``"dry_run"`` status.
    diffs: list[InstructionDiffDict]
    # PRD-FIX-123-FR01/FR02: per-target refusals. Present when a guarded write
    # was refused rather than performed — a policy refusal, distinguishable from
    # an I/O failure without string-matching a message.
    refusals: list[InstructionWriteRefusalDict]


class CeremonyScoreResult(TypedDict):
    """Return shape of ``compute_ceremony_score()``."""

    score: int
    session_start: bool
    deliver: bool
    checkpoint_count: int
    learn_count: int
    build_check: bool
    build_passed: bool | None
    review: bool


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
    #: Weight of ``outcome_quality`` contributed by components that were NOT
    #: measured. ``outcome_quality`` is a weighted sum, and a component nobody
    #: computed used to be passed in as a literal ``True`` — scoring a full 0.2
    #: for a check that never ran. NotRequired because entries written before
    #: this field existed cannot carry it; absent means "not known", which is
    #: itself accurate for those rows.
    unmeasured_quality_weight: NotRequired[float]


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
    avg_ceremony_score: float | None
    avg_outcome_quality: float | None
    proposals: list[ReductionProposalDict]
    auto_escalation: EscalationResult | None
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


class SessionRecallExtrasDict(TypedDict, total=False):
    """Extra metadata fields returned alongside learnings by ``perform_session_recalls()``.

    ``query`` appears on the focused path, ``query_advisory`` only when a
    focused recall matched zero entries, ``learnings_omitted`` only when the
    presenter left ranked rows out (PRD-CORE-294 FR02).
    """

    query: str
    query_advisory: str
    learnings_omitted: int
    # PRD-FIX-141-FR05: the project store's inventory. Omitted, never zeroed, when unread.
    store_count: int


class FinalizeRunResult(TypedDict, total=False):
    """Return shape of ``finalize_run()``.

    Currently always returns ``{}`` — placeholder for future run-close fields
    such as ``run_id``, ``closed_at``, ``archived_path``.
    """


class TrwHeartbeatResultDict(TypedDict, total=False):
    """Return shape of ``trw_checkpoint(heartbeat=True)`` (PRD-CORE-141 FR07;
    folded from a formerly standalone heartbeat tool by PRD-CORE-300 S6a).

    All fields optional: the success path populates
    ``run_id``/``last_heartbeat_ts``/``stale_after_ts``/``age_hours``/
    ``should_checkpoint``/``rate_limited`` while the missing-pin path
    populates ``error``/``hint`` instead. ``thread_hotspot`` (PRD-FIX-131
    operator-visibility follow-up) is the CALLING server's own hottest-thread
    CPU share of its own process uptime -- omitted entirely on a platform or
    ``/proc`` state that cannot measure it, never a fabricated zero.
    """

    run_id: str
    last_heartbeat_ts: str
    stale_after_ts: str
    age_hours: float
    should_checkpoint: bool
    rate_limited: bool
    error: str
    hint: str
    thread_hotspot: dict[str, float]


class TrwAdoptRunResultDict(TypedDict):
    """Return shape of ``adopt_run`` (PRD-CORE-141 FR08), invoked via ``trw-mcp run adopt`` since PRD-CORE-300 S6b.

    All keys are present on the success path; failures raise ``StateError``.
    ``previous_pin_key`` is ``None`` when the target run had no prior pin.
    """

    adopted_run_id: str
    previous_pin_key: str | None
    to_pin_key: str
    adopted_ts: str
    from_owner_was_live: bool
    force_used: bool
