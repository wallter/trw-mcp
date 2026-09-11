"""Ceremony scoring, feedback, escalation, and delivery-gate TypedDicts."""

from __future__ import annotations

from typing import Literal

from typing_extensions import TypedDict


class WalCheckpointResultDict(TypedDict, total=False):
    """Return shape of ``maybe_checkpoint_wal()`` (PRD-QUAL-050-FR05, PRD-CORE-248-FR04).

    Exactly one of three outcomes is populated, distinguished by which key is
    present:

    * **Skipped** — ``skipped=True`` plus ``reason`` (``"no_wal_file"`` or
      ``"under_threshold"``). No checkpoint ran.
    * **Checkpointed** — ``checkpointed=True`` plus the size/mode telemetry
      (``mode``/``wal_size_before_mb``/``wal_size_after_mb``/
      ``pages_checkpointed``/``backlog_cleared``/``reclaimed``/``busy``).
      ``mode`` is the lowercase mode that actually ran
      (``"truncate"``/``"passive"``; ``"error"`` is mapped from the backend's
      error sentinel). ``markers_persisted`` reports whether the checkpoint
      timestamp actually reached disk; when it is ``False`` the checkpoint ran
      but its clock did not, so ``reason`` and ``advisory`` are populated and
      the success is a PARTIAL one.

      **Read ``checkpointed`` narrowly.** It means the operation RAN, not that
      it accomplished anything — it is a hardcoded ``True`` on every non-error
      path, and ``pages_checkpointed`` counts frames written back, which PASSIVE
      does on every run of a busy store. ``backlog_cleared`` is the health
      field; ``reclaimed``/``reclaimed_mb`` are disk facts. When the backlog was
      NOT cleared, ``advisory`` says so in frames and carries the remedy when
      there is one.

      **Do not re-derive health from file size.** That was tried and it was
      wrong in the other direction: SQLite reuses a fully checkpointed WAL's
      allocation instead of shrinking it, so a healthy store that clears its
      entire backlog reports no size change at all. Reporting the operation as
      the outcome is what let a WAL climb from 16 MB to 24 MB across four days
      of green checkpoints (``sub_RDVsKkpVeG5nDHoE`` / ``sub_alqafW7Pst40zAIK``,
      2026-09-07); reporting file size as the outcome would have WARNed forever
      on stores that were fine.
    * **Errored** — ``error=True`` plus ``reason="checkpoint_failed"`` (fail-open:
      the WAL checkpoint must never block session start).

    **What is deliberately NOT here, and why this docstring says so.** The frame
    counts (``wal_frames``), the reclaimed byte delta (``reclaimed_mb``) and the
    four-state ``truncate_state`` classification are emitted on the
    ``wal_checkpoint_complete`` structlog event and are NOT returned. This shape
    is paid on every ``trw_session_start`` by every calling agent; carrying all
    of them measured 76 tokens against this repo's 60-token hot-path budget
    (``test_session_start_step_latency``), so per the standing rule the bloat was
    cut rather than the ceiling raised. A maintainer reads them in the log, where
    they are free.

    They were declared here for a while after being cut from the implementation.
    Because this is ``total=False``, mypy could not see that nothing populated
    them, so the contract promised a consumer three keys that ``.get()`` would
    always answer ``None`` for — the same we-checked-versus-we-never-checked
    collapse the checkpoint reporting itself was being fixed for. Do not re-add
    a field here without a line in ``maybe_checkpoint_wal`` that sets it.
    """

    skipped: bool
    reason: str
    advisory: str
    markers_persisted: bool
    checkpointed: bool
    mode: str
    wal_size_before_mb: float
    wal_size_after_mb: float
    pages_checkpointed: int
    #: Whether this checkpoint caught up with the WAL backlog
    #: (``pages_checkpointed >= wal_frames`` and not busy). THIS is checkpoint
    #: effectiveness. File size is not: SQLite REUSES a fully checkpointed
    #: WAL's allocation rather than shrinking it, so a perfectly healthy store
    #: clears its whole backlog and leaves the file exactly as large.
    backlog_cleared: bool
    #: Whether the WAL file got smaller on disk. Read this as a disk fact only,
    #: and read ``backlog_cleared`` for health: a retained allocation is NORMAL
    #: and is not a fault.
    reclaimed: bool
    busy: int
    error: bool


class AutoMaintenanceDict(TypedDict, total=False):
    """Return shape of ``run_auto_maintenance()``.

    All keys are optional — only populated when the corresponding maintenance
    operation produced a non-empty result.
    """

    update_advisory: str
    auto_upgrade: dict[str, object]
    stale_runs_closed: dict[str, object]
    stale_runs_deferred: dict[str, object]
    embeddings_advisory: str
    embeddings_backfill: dict[str, int]
    embeddings_backfill_deferred: dict[str, object]
    # PRD-CORE-263 DEF-11: NOT a deferral — the standing hot-path policy that
    # session start never runs bulk embedding backfill; nothing schedules it
    # for later, so it is named "not performed" rather than "deferred".
    embeddings_backfill_not_performed: dict[str, object]
    embeddings_backfill_scheduled: dict[str, object]  # PRD-FIX-105-FR01: background backfill on low coverage
    embedder_warmup_scheduled: dict[str, object]  # Option A+ (2026-06-10): first-recall download warm-up guard
    embeddings_coverage_ratio: float  # PRD-FIX-COMPOUNDING-3-FR02: vector coverage ratio (0.0-1.0)
    wal_checkpoint: WalCheckpointResultDict  # PRD-QUAL-050-FR05
    auto_upgrade_check_deferred: dict[str, object]
    # Learn write-ahead-journal recovery: only present when a prior interrupted
    # session left accepted-but-unstored learnings to replay (omit-when-empty).
    pending_learns_replayed: dict[str, object]
    pending_learns_deferred: dict[str, object]
    # PRD-CORE-257-FR03: covered steps whose deferral streak reached
    # session_start_max_deferral_hours and ran anyway, this process winning the
    # single-winner claim. Omitted when empty.
    deferral_expired_ran: list[str]
    # PRD-CORE-257-FR12: one outcome per covered step, from the closed
    # vocabulary executed | deferred | expired_ran | failed. The aggregate event
    # is auto_maintenance_evaluated precisely because this map can be all
    # "deferred" — "complete" is reserved for a pass in which every step ran.
    step_outcomes: dict[str, str]


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

    status: Literal["generated", "failed"]
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
    FR07 carrier-detectability fields (``carrier_mode``, ``pointer_skips``,
    ``external_path``) are present on the render path when CLAUDE.md is written.
    """

    hash: str
    # PRD-CORE-203 FR07: how the TRW block was delivered into CLAUDE.md
    # (``"inline"`` | ``"import"`` | ``"pointer_skip"``).
    carrier_mode: str
    # Single-source pointer files left un-clobbered (with their import targets).
    pointer_skips: list[InstructionPointerSkipDict]
    # Repo-root-relative sidecar path when the block was externalized.
    external_path: str | None
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


class AutoRecalledItemDict(TypedDict, total=False):
    """Single entry in the phase-contextual auto-recall result list.

    Returned by ``_phase_contextual_recall()`` — a ranked subset of
    ``LearningEntryDict`` projected down to summary fields only.
    """

    id: str | None
    summary: str | None
    impact: float | None
    verification_evidence: dict[str, object]


class SessionRecallExtrasDict(TypedDict, total=False):
    """Extra metadata fields returned alongside learnings by ``perform_session_recalls()``.

    Keys present on the focused-query path: ``query``, ``query_matched``,
    ``total_available``.  Only ``total_available`` is always populated.
    ``query_advisory`` appears only when a focused recall matched zero entries.
    """

    query: str
    query_matched: int
    query_advisory: str
    total_available: int
    response_compacted: bool
    side_effects_deferred: dict[str, object]


class FinalizeRunResult(TypedDict, total=False):
    """Return shape of ``finalize_run()``.

    Currently always returns ``{}`` — placeholder for future run-close fields
    such as ``run_id``, ``closed_at``, ``archived_path``.
    """


class TrwHeartbeatResultDict(TypedDict, total=False):
    """Return shape of ``trw_heartbeat`` (PRD-CORE-141 FR07).

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
    """Return shape of ``trw_adopt_run`` (PRD-CORE-141 FR08).

    All keys are present on the success path; failures raise ``StateError``.
    ``previous_pin_key`` is ``None`` when the target run had no prior pin.
    """

    adopted_run_id: str
    previous_pin_key: str | None
    to_pin_key: str
    adopted_ts: str
    from_owner_was_live: bool
    force_used: bool
