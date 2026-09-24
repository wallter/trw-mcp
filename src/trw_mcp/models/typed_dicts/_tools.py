"""MCP tool return TypedDicts (session_start, recall, learn, checkpoint, deliver)."""

from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict

from trw_mcp.models.typed_dicts._ceremony import (
    MovedCheckoutDict,
    OpenHandoffDict,
    ReconciledLocalWritesDict,
)


class Degradation(TypedDict):
    """One typed fail-open degradation on the ceremony hot path (mcp-x-failopen).

    Records a swallowed non-fatal failure so it becomes OBSERVABLE in the tool
    payload instead of vanishing into a debug log. Recording a ``Degradation``
    NEVER flips ``success`` — that stays governed solely by the ``errors`` list.
    All four keys are always present. See
    :mod:`trw_mcp.tools._ceremony_degradations`.

    - ``step``: the failing step's stable key (e.g. ``"recall"``, ``"pipeline_health"``).
    - ``error_class``: the exception class name (``type(exc).__name__``).
    - ``message``: ``str(exc)`` — the exception message (never a secret/PII source).
    - ``severity``: ``"warn"`` for expected fail-open swallows; ``"info"`` for the
      previously-silent control-flow fallbacks whose only purpose is visibility.
    """

    step: str
    error_class: str
    message: str
    severity: Literal["info", "warn"]


class RecallResultDict(TypedDict, total=False):
    """Return shape of ``trw_recall`` (PRD-CORE-294 FR01)."""

    query: str
    #: Stubs ``{id, claim, anchor?}`` by default; full rows for ``ids=``.
    learnings: list[dict[str, object]]
    #: Rows ranked for this query after dedup and the ``max_results`` cap.
    total_matches: int
    #: Ranked rows the byte budget cut; absent when nothing was cut.
    omitted: int
    #: Requested ``ids`` no store holds; only with ``ids=``.
    missing_ids: list[str]
    remote_recall: dict[str, object]  # remote failure/incompleteness or unevaluated temporal coverage
    store_unavailable: str  # the memory store could not be opened; empty results are not "nothing learned"
    # Non-empty only when a requested topic filter was a no-op; explains why.
    topic_filter_warning: str


class RunStatusDict(TypedDict, total=False):
    """Run status sub-dict used in session_start and trw_status."""

    active_run: str | None
    phase: str
    status: str
    task_name: str
    # PRD-CORE-246-FR04: the run's behavioral regime AND where it came from.
    # ``task_type_source`` has exactly three values — ``run_yaml`` (the key was
    # present), ``default_unknown`` (the key was absent and RunState supplied its
    # default) and ``unresolved`` (the run could not be read). Keeping the three
    # distinguishable is the requirement: a silent default is the failure class.
    task_type: str
    task_type_source: str
    capability_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str
    owner_session_id: str | None
    wave_status: dict[str, object] | None
    # PRD-CORE-165 FR-01: caller-supplied recovery context surfaced from the
    # pre-compact state so the post-compaction session resumes exactly.
    directive: str
    context_anchor: str
    # CORE269 FR04: explicit-read location only; not recovered/accepted work.
    checkpoint_log_path: str


class SessionStartResultDict(TypedDict, total=False):
    """Return shape of ``trw_session_start`` MCP tool."""

    timestamp: str
    learnings: list[dict[str, object]]
    learnings_count: int
    query: str
    # Present ONLY when a focused query matched zero entries — says why the
    # learning block is empty and points the caller at trw_recall.
    query_advisory: str
    # The project store's inventory, omitted when it could not be read (PRD-FIX-141-FR05).
    store_count: int
    # PRD-CORE-215 FR01 connection fingerprint. Full ten-field block under
    # verbose=True; compact mode keeps only build_identity + connection_nonce
    # (see tools/_session_start_trim.py::_FINGERPRINT_COMPACT_FIELDS).
    connection_fingerprint: dict[str, object]
    run: RunStatusDict
    first_session_emitted: bool
    # The daemon-measured share of this namespace's entries holding a vector,
    # from session start's pipeline-health probe; omitted when not measured.
    embeddings_coverage_ratio: float
    # What recall can use on this install (state/_retrieval_capability.py):
    # "active", "keyword-only: ..." or "degraded: <component> (...) fix: ...".
    retrieval: str
    errors: list[str]
    success: bool
    framework_reminder: str
    ceremony_status: str
    # Sync-push health advisory (PRD-FIX-COMPOUNDING-1) — degraded when the
    # backend push has stalled (consecutive_failures >= threshold or stale push)
    sync_health: dict[str, object]
    # Assertion health summary (PRD-CORE-086 FR07) — omitted when no assertions
    assertion_health: dict[str, int]
    # Knowledge-graph health advisory (PRD-FIX-COMPOUNDING-2 FR04) — present
    # only when the graph is empty AND there are >10 memories.
    graph_health: dict[str, object]
    # Offline-write reconciliation report (PRD-CORE-247-FR05) — always present:
    # a zero count is the honest answer to "what bypassed the online path", and
    # an absent field would be indistinguishable from a step that never ran.
    reconciled_local_writes: ReconciledLocalWritesDict
    # PRD-CORE-249-FR03: project-scoped open handoff rows, read back from the
    # managed block of the configured handoff file. Always present: ``status`` is
    # measured / absent / not_measured, ``total`` is the UNTRUNCATED count and is
    # present only when measured, and ``items`` is oldest-first and capped. A
    # block over the parse cap reports ``not_measured`` with a reason and is
    # never reported as zero open items.
    open_handoff: OpenHandoffDict
    # PRD-CORE-253-FR01: evidence that this checkout was moved or renamed --
    # the current project identity has zero rows while a same-slug sibling has
    # some. Present ONLY when that signal fires, so a normal session pays no
    # tokens for it. Carries ``repair_command``; nothing here re-labels a row.
    moved_checkout: MovedCheckoutDict
    # Unified compounding-pipeline health advisory (PRD-FIX-COMPOUNDING-6 FR03).
    # Compact single-line string injected ONLY when any of the five pipeline
    # signals is degraded (PRD-INFRA-068 lesson: absent on healthy sessions
    # to avoid focus-distraction). Use trw_pipeline_health() for the full report.
    pipeline_health_advisory: str
    # Auto-maintenance results merged in from AutoMaintenanceDict
    update_advisory: str
    auto_upgrade: dict[str, object]
    stale_runs_closed: dict[str, object]
    # PRD-CORE-141 FR06: Structured guidance when no pin exists for the
    # caller's ctx — directs agents to ``trw_init`` (new run) or to pass
    # ``run_path`` (resume). Populated only on the no-pin path.
    hint: str
    candidate_runs: list[dict[str, object]]
    # PRD-HPO-MEAS-001 FR-2: Resolved surface snapshot id for the session.
    # Empty string during Phase 1 when artifact_registry stamping is
    # unavailable or fails open. Every HPOTelemetryEvent emitted during the
    # session is expected to carry this id (post Wave-2 wiring).
    surface_snapshot_id: str
    # PRD-HPO-PROF-001 FR-4: Resolved hierarchical profile for the session.
    # ``resolved_profile`` is the effective (merged) surface; ``profile_snapshot_id``
    # is the PERSISTENT-surface content hash (distinct from the MEAS-001
    # artifact-registry ``surface_snapshot_id`` above); ``session_override_hash``
    # is the session-layer delta hash (FR-13). All omitted when the profile
    # system is disabled or resolution fails open.
    resolved_profile: dict[str, object]
    # PRD-FIX-141-FR06: what the profile above was resolved FROM (run dir,
    # whether the Scout's session layer existed yet, layers applied, tier).
    # trw_profile_explain emits the identical block, so two reports of the same
    # session are reconcilable instead of contradictory.
    profile_resolution_basis: dict[str, object]
    profile_layers_applied: list[str]
    profile_snapshot_id: str
    session_override_hash: str
    # PRD-HPO-PROF-001 FR-12 (audit F-02): when a persistent profile layer
    # (org/domain/task-type) is malformed/schema-invalid, the resolver fails
    # CLOSED rather than silently degrading to defaults — but session start
    # still succeeds. The structured error is surfaced here with ``{path,
    # reason}`` so the operator can fix the offending layer file. Present ONLY
    # on a LayerLoadError; absent on success and when the profile system is
    # disabled.
    profile_resolution_error: dict[str, str]
    # PRD-HPO-MEAS-001 NFR-12: Boot-audit failures surfaced to the caller.
    # Absent on success; populated with ``{key, expected, actual, remediation}``
    # entries when any Phase-1 default cannot be resolved.
    boot_audit_failures: list[dict[str, str]]
    # PRD-FIX-084: Per-step latency telemetry (milliseconds). Keys: recall,
    # run_resolve, surface_stamp, log_event, telemetry, counter,
    # sanitize_maintain, total. Absent keys mean the step
    # did not start (e.g. exited via partial-failure earlier). Future
    # regressions of the "step accidentally O(corpus)" class are visible
    # from a single log line via the ``session_start_ok`` event payload.
    step_durations_ms: dict[str, float]
    # PRD-IMPROVE-MCP-04 FR1: payload trimming (compact-by-default).
    # ``compact`` is True when the trimmed payload was returned (verbose=False),
    # False when the full payload was returned (verbose=True). ``health_summary``
    # is the one-line collapse of the diagnostic sub-blocks (embed/assertion/
    # sync health + total latency) present ONLY in compact mode.
    # ``learnings_omitted`` is the "N more" indicator — how many ranked rows the
    # PRD-CORE-294 FR02 presenter left out of the learning block; absent when none.
    compact: bool
    health_summary: str
    learnings_omitted: int
    # Non-fatal degradations that do NOT flip ``success``. A recall failure is
    # fail-open by contract (recall must never block session start), so it is
    # surfaced here for visibility rather than appended to ``errors`` — where it
    # would set ``success=False`` and mislead agents into needless retries of an
    # otherwise-successful session_start.
    warnings: list[str]
    # mcp-x-failopen: typed fail-open degradations. Each entry records a
    # swallowed non-fatal failure {step, error_class, message, severity} so the
    # previously-invisible ceremony-hot-path swallows are observable in the
    # payload. Recording a degradation NEVER flips ``success`` (governed solely
    # by ``errors``); both keys are ABSENT on a fully-clean session.
    degradations: list[Degradation]
    degraded_steps: int


class FailureAttributionItemDict(TypedDict):
    """Per-failure triage tag (PRD-IMPROVE-MCP-02 FR1).

    A fast, best-effort signal — NOT proof. ``classification`` is
    ``likely_introduced`` when the failure's test file (or an
    obviously-related source file) appears in the current working-tree
    diff, ``likely_pre_existing`` when nothing in the diff touches it,
    and ``unknown`` when git was unavailable or the failure string could
    not be parsed.
    """

    failure: str
    test_file: str | None
    classification: Literal["likely_introduced", "likely_pre_existing", "unknown"]
    reason: str


class FailureAttributionDict(TypedDict):
    """Aggregate failure-attribution triage block (PRD-IMPROVE-MCP-02 FR1).

    Surfaced on ``BuildCheckResultDict.failure_attribution`` and mirrored
    into the summary line so an agent instantly sees "N failures: X likely
    yours, Y pre-existing on this tree" without git archaeology.

    HONEST LIMITS: heuristic file-to-test mapping (test path stem ->
    candidate source). It can mis-tag a failure whose root cause lives in
    an untouched dependency of a touched file, or vice-versa. Fail-open:
    any git/parse error degrades the whole block to ``unknown`` and never
    raises into ``trw_build_check``.
    """

    likely_introduced: int
    likely_pre_existing: int
    unknown: int
    changed_files_count: int
    per_failure: list[FailureAttributionItemDict]
    summary: str


class BuildCheckResultDict(TypedDict, total=False):
    """Return shape of ``trw_build_check`` MCP tool.

    PRD-FIX-088 FR03: ``step_durations_ms`` mirrors the
    ``SessionStartResultDict`` precedent set by PRD-FIX-084. Keys
    populated on the success path: persist, run_resolve, log_event,
    finalize, total.
    """

    tests_passed: bool
    static_checks_clean: bool
    mypy_clean: bool
    timed_out: bool
    coverage_pct: float
    test_count: int
    failure_count: int
    failures: list[str]
    scope: str
    duration_secs: float | None
    cache_path: str
    status: str
    reason: str
    coverage_threshold_failed: bool
    coverage_threshold: float
    coverage_threshold_message: str
    step_durations_ms: dict[str, float]
    failure_attribution: FailureAttributionDict
    summary: str


class LearnResultDict(TypedDict, total=False):
    """Return shape of ``trw_learn`` MCP tool.

    Always-present key: ``status`` ("recorded" | "skipped" | "rejected").

    Recorded path: ``learning_id``, ``path``.
    Optional on recorded path: ``distribution_warning``, ``ceremony_status``,
    ``impact``, ``tags``.
    Present on skip (dedup): ``duplicate_of``, ``similarity``.
    Incomplete or skipped retirement: ``consolidation_warning``.
    Present on rejection (noise filter): ``reason``, ``message``.
    """

    learning_id: str
    status: str  # "recorded" | "skipped" | "rejected"
    path: str
    distribution_warning: str
    consolidation_warning: str
    ceremony_status: str
    # Populated when impact/tags are surface-returned (delivery path)
    impact: NotRequired[float]
    tags: NotRequired[list[str]]
    # Present on skip (dedup):
    duplicate_of: str
    similarity: float
    # Present on rejection (noise filter):
    reason: str
    message: str
    # PRD-CORE-244-FR05: advisory window proposal for a state-asserting learning.
    # Advisory ONLY — the persisted ``expires`` is exactly what the caller supplied.
    validity_window_nudge: str


class CheckpointResultDict(TypedDict, total=False):
    """Return shape of ``trw_checkpoint`` MCP tool and ``_maybe_auto_checkpoint``."""

    timestamp: str
    status: str
    message: str
    ceremony_status: str
    # auto-checkpoint path (returned by _maybe_auto_checkpoint in checkpoint.py)
    auto_checkpoint: bool
    tool_calls: int
    # wave-aware checkpoint path (returned by trw_checkpoint in orchestration.py)
    wave_id: str


class KnowledgeSyncResultDict(TypedDict, total=False):
    """Return shape of ``trw_knowledge_sync`` MCP tool."""

    threshold_met: bool
    entry_count: int
    threshold: int
    topics_generated: int
    entries_clustered: int
    output_dir: str
    dry_run: bool
    clusters: list[str]
    errors: list[str]
    elapsed_seconds: float
    graph_backfill: dict[str, int]


class DeliverResultDict(TypedDict, total=False):
    """Return shape of ``trw_deliver`` MCP tool."""

    timestamp: str
    run_path: str | None
    # Gate warnings (merged from DeliveryGatesDict)
    review_block: str
    review_warning: str
    review_advisory: str
    # PRD-CORE-255-FR05: {receipt_id, scope_digest, age_seconds} of the typed
    # ReviewReceipt that satisfied the review gate. Absent when none did.
    review_evidence: dict[str, object]
    # PRD-CORE-255-FR04: safety-critical adversarial-audit gate outcome, plus the
    # FR03 resolution word when the run declared no PRD scope (``not_declared``).
    safety_critical_adversarial_block: str
    safety_critical_adversarial_advisory: str
    safety_critical: str
    # PRD-CORE-192-FR04: pre-deliver REVIEW nudge surfaced before the gate result.
    review_nudge: str
    review_scope_block: str
    integration_review_block: str
    integration_review_warning: str
    untracked_warning: str
    build_gate_warning: str
    build_gate_block: str
    build_gate_override: str
    truthfulness_gate_bypassed: str
    # PRD-CORE-191: structured acceptable-failure override.
    #   acceptable_failure_record  — parsed schema dict when the override was accepted.
    #   acceptable_failure_error   — validation error (prose/missing field/expired) when rejected.
    #   acceptable_failure_advisory — deprecation advisory when a soft-gate override
    #                                 used a free-text (non-schema) reason.
    acceptable_failure_record: dict[str, object]
    acceptable_failure_error: str
    acceptable_failure_advisory: str
    # PRD-CORE-184-FR03: task-type-aware deliver gate mode block.
    delivery_blocked: str
    missing_gate: str
    # Task type that triggered a deliver_gate_mode hard block (surfaced for audit).
    blocked_task_type: str
    checkpoint_blocker_warning: str
    complexity_drift_warning: str
    instruction_parity_warning: str
    warning: str
    # F24 (legibility): aggregate of the advisory (soft, non-blocking) warning
    # keys present on a SUCCESSFUL deliver. Lets eval / false-completion scoring
    # distinguish a clean deliver (warning_count=0, warnings_present=False) from
    # a warned-but-delivered one. Does NOT reflect blocking gates.
    warning_count: int
    warnings_present: bool
    warnings: list[str]
    # Compliance artifacts (merged from ComplianceArtifactsDict)
    compliance_artifacts_copied: list[str]
    compliance_dir: str
    reflect: dict[str, object]
    checkpoint: dict[str, object]
    candidate_runs: list[dict[str, object]]
    critical_elapsed_seconds: float
    deferred: str
    errors: list[str]
    success: bool
    critical_steps_completed: int
    # PRD-CORE-125 FR05: Self-reflection message about learnings
    learning_reflection: str
    # PRD-FIX-COMPOUNDING-2 FR03: knowledge-graph topic-sync result. Populated
    # post-deliver (fail-open); below threshold reports threshold_met=False.
    knowledge_sync: dict[str, object]
    # F5 suggestion 2: opportunistic time-boxed graph backfill result on deliver.
    # Shape: {"processed": int, "edges_built": int, "skipped": int, "failed": int}.
    graph_backfill: dict[str, int]
    # PRD-INFRA-067 (C2): Integrity-on-delivery probe result. Surfaced in the
    # response ONLY on a real regression (ok=False, incl. "not measured"), and
    # then only the actionable {"ok": bool, "detail": str}. The full record
    # (incl. namespace / checked_at) always persists to events.jsonl.
    db_integrity: dict[str, object]
    # PRD-INFRA-068 (C3): Memory health dashboard — surfaced here so clients
    # can report health when deliver is a session's last action.
    memory_health: dict[str, object]
    # PRD-HPO-MEAS-001 FR-5: CLEAR 5-dimensional score for the closed
    # session. Populated when load_and_score_run produces a record.
    # Shape matches ``ClearScore.model_dump(mode="json")``.
    clear_score: dict[str, object]
    # PRD-LOCAL-049 FR01: absolute path to the session changelog markdown
    # artifact written under ``<run>/reports/session-changelog.md``. Present
    # whenever a run dir exists and the (fail-open) write succeeded.
    session_changelog_path: str
    # PRD-LOCAL-049 FR02 fail-open marker: present only when the changelog
    # step failed — ``{"status": "failed", "error": str}`` (never blocks deliver).
    session_changelog: dict[str, object]
    # PRD-LOCAL-049 FR03: advisory package-changelog coverage. Present only when
    # ``changelog_advisory_enabled`` policy is on. Each entry:
    # ``{package_root, changed_files, changelog_path, changelog_updated}``.
    package_changelog_advisory: list[dict[str, object]]
    # Nudge-deep-dive work target #1/#2: live nudge-effectiveness summary
    # computed on deliver from this session's ceremony-state + surface stream.
    # Full artifact at ``.trw/context/nudge-analysis.json``. When no nudge fired
    # this session it collapses to just {applicable: False}; otherwise it is the
    # compact summary — {applicable, total_nudges, responsiveness,
    # recall_pull_rate, resistance_steps, resistance_flagged,
    # timing_validity_rate, variant_breakdown, artifact}.
    nudge_analysis: dict[str, object]
    # mcp-x-failopen: typed fail-open degradations on the deliver hot path. Each
    # entry records a swallowed non-fatal failure {step, error_class, message,
    # severity}. Recording NEVER flips ``success`` (governed solely by
    # ``errors``); both keys are ABSENT on a fully-clean deliver.
    degradations: list[Degradation]
    degraded_steps: int
    # PRD-CORE-208: crash-safe idempotent delivery-operation journal projection.
    # Compact, redaction-safe summary of the claimed operation that owned this
    # delivery — {operation_id, caller_recoverable, mode, enabled,
    # journaled_effect_count}. On an explicit-ID conflict/rejection the delivery
    # is BLOCKED with zero effects and this carries {operation_id, status,
    # reason_code, effect_calls: 0, caller_recoverable}. ABSENT when
    # delivery_operations_mode="off".
    delivery_operation: dict[str, object]
    # PRD-CORE-249-FR04: plan-acceptance gate. ``plan_acceptance_block`` is the
    # STRUCTURED hard block (present only when the block STANDS — a successful
    # acceptable-failure override leaves it absent); ``plan_acceptance_warning``
    # is the advisory form under a non-blocking mode or task type;
    # ``unresolved_scope_entries`` names every ``prd_scope`` entry that resolved
    # to no PRD file, so zero enumerated identifiers is never reported as a pass.
    # These are set by the self-computing gate directly on the result and are
    # deliberately NOT DeliveryGatesDict keys: ``check_delivery_gates`` does not
    # compute them, and a key there with no producer is the presence-unconsumed
    # pattern this PRD exists to close.
    plan_acceptance_block: str
    plan_acceptance_warning: str
    # PRD-CORE-265-FR11: formation gate on the ORCHESTRATOR run.
    # ``formation_gate_block`` is present only when the hard block STANDS (a
    # valid acceptable-failure record leaves it absent); ``formation_gate_warning``
    # is the same condition under ``formation_deliver_gate: advisory``.
    formation_gate_block: str
    formation_gate_warning: str
    unresolved_scope_entries: list[str]
    # PRD-CORE-249-FR02: outcome of the deliver-time handoff write. Fail-open —
    # ``{"status": "failed", "error": ...}`` records the failure and delivery
    # still succeeds, so this is never absent-meaning-succeeded.
    project_handoff: dict[str, object]


class PreCompactResultDict(TypedDict, total=False):
    """Return shape of ``trw_pre_compact_checkpoint`` MCP tool.

    Always-present key: ``status``.
    Success path: ``run_path``, ``compact_instructions_path``,
    ``prd_scope``, ``failing_tests``.
    Skip path: ``reason``.
    Failure path: ``error``.
    """

    status: str
    run_path: str
    compact_instructions_path: str
    prd_scope: list[str]
    failing_tests: list[str]
    reason: str
    error: str
    # PRD-CORE-165 FR-01: caller-supplied directive + context-anchor persisted
    # into the pre-compact state (echoed back on the success path when set).
    directive: str
    context_anchor: str
