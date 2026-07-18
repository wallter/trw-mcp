"""MCP tool return TypedDicts (session_start, recall, learn, checkpoint, deliver)."""

from __future__ import annotations

from typing import Literal

from typing_extensions import NotRequired, TypedDict

from trw_mcp.models.typed_dicts._ceremony import AutoRecalledItemDict


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


class RecallContextDict(TypedDict, total=False):
    """Shape of the context dict returned by ``collect_context()`` and embedded in ``RecallResultDict``.

    Both keys are optional — populated only when the corresponding YAML file
    exists in the ``.trw/context/`` directory.
    """

    architecture: object
    conventions: object


class RecallResultDict(TypedDict, total=False):
    """Return shape of ``trw_recall`` MCP tool."""

    query: str
    learnings: list[dict[str, object]]
    patterns: list[dict[str, object]]
    context: RecallContextDict
    total_matches: int
    total_available: int
    compact: bool
    max_results: int
    topic_filter_ignored: bool
    # Non-empty when topic_filter_ignored=True — explains why the filter was a no-op.
    topic_filter_warning: str
    count: int
    ceremony_hint: str
    # Token budget fields (PRD-CORE-123 Phase 2)
    tokens_used: int
    tokens_budget: int | None
    tokens_truncated: bool
    # Post-rank near-duplicate dedup (F-DEDUP-001): count of entries collapsed.
    duplicates_collapsed: int


class RunStatusDict(TypedDict, total=False):
    """Run status sub-dict used in session_start and trw_status."""

    active_run: str | None
    phase: str
    status: str
    task_name: str
    capability_tier: str
    model_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str
    owner_session_id: str | None
    wave_status: dict[str, object] | None
    # PRD-CORE-165 FR-01: caller-supplied recovery context surfaced from the
    # pre-compact state so the post-compaction session resumes exactly.
    directive: str
    context_anchor: str


class SessionStartResultDict(TypedDict, total=False):
    """Return shape of ``trw_session_start`` MCP tool."""

    timestamp: str
    learnings: list[dict[str, object]]
    learnings_count: int
    query: str
    query_matched: int
    total_available: int
    response_compacted: bool
    side_effects_deferred: dict[str, object]
    recall_degraded: dict[str, object]
    run: RunStatusDict
    first_session_emitted: bool
    embeddings_advisory: str
    errors: list[str]
    success: bool
    framework_reminder: str
    ceremony_status: str
    nudge_deferred: dict[str, object]
    # Auto-recall (phase-contextual, PRD-CORE-049)
    auto_recalled: list[AutoRecalledItemDict]
    auto_recall_count: int
    # Embed health advisory (PRD-FIX-053)
    embed_health: dict[str, object]
    # Sync-push health advisory (PRD-FIX-COMPOUNDING-1) — degraded when the
    # backend push has stalled (consecutive_failures >= threshold or stale push)
    sync_health: dict[str, object]
    # Assertion health summary (PRD-CORE-086 FR07) — omitted when no assertions
    assertion_health: dict[str, int]
    # Knowledge-graph health advisory (PRD-FIX-COMPOUNDING-2 FR04) — present
    # only when the graph is empty AND there are >10 memories.
    graph_health: dict[str, object]
    # Unified compounding-pipeline health advisory (PRD-FIX-COMPOUNDING-6 FR03).
    # Compact single-line string injected ONLY when any of the five pipeline
    # signals is degraded (PRD-INFRA-068 lesson: absent on healthy sessions
    # to avoid focus-distraction). Use trw_pipeline_health() for the full report.
    pipeline_health_advisory: str
    # Auto-maintenance results merged in from AutoMaintenanceDict
    update_advisory: str
    auto_upgrade: dict[str, object]
    stale_runs_closed: dict[str, object]
    stale_runs_deferred: dict[str, object]
    auto_upgrade_check_deferred: dict[str, object]
    embeddings_backfill: dict[str, int]
    embeddings_backfill_deferred: dict[str, object]
    embeddings_backfill_scheduled: dict[str, object]  # PRD-FIX-105-FR01
    wal_checkpoint_deferred: dict[str, object]
    auto_recall_deferred: dict[str, object]
    ceremony_status_deferred: dict[str, object]
    # Compact-mode fold of the individual ``*_deferred`` blocks above:
    # ``{reason: [step, ...]}`` plus a single writer_count. The per-step
    # blocks are only present with ``verbose=True``.
    deferred: dict[str, list[str]]
    deferred_writer_count: int
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
    profile_layers_applied: list[str]
    profile_snapshot_id: str
    session_override_hash: str
    profile_explanation: dict[str, object]
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
    # sanitize_maintain, phase_recall, total. Absent keys mean the step
    # did not start (e.g. exited via partial-failure earlier). Future
    # regressions of the "step accidentally O(corpus)" class are visible
    # from a single log line via the ``session_start_ok`` event payload.
    step_durations_ms: dict[str, float]
    # PRD-IMPROVE-MCP-04 FR1: payload trimming (compact-by-default).
    # ``compact`` is True when the trimmed payload was returned (verbose=False),
    # False when the full payload was returned (verbose=True). ``health_summary``
    # is the one-line collapse of the diagnostic sub-blocks (embed/assertion/
    # sync health + total latency) present ONLY in compact mode.
    # ``learnings_omitted`` is the "N more" indicator — how many top-K-capped
    # learnings were dropped from the returned list (0 when nothing was capped).
    # ``payload_token_estimate`` is an approximate (~4 chars/token) size estimate
    # so the token-cost reduction is measurable from the response itself.
    compact: bool
    health_summary: str
    learnings_omitted: int
    payload_token_estimate: int
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


class QLearningDeferredDict(TypedDict):
    """Stable shape of ``BuildCheckResultDict.q_learning_deferred`` (PRD-FIX-088 FR01).

    Always-present fields. Returned by ``_dispatch_q_learning_async`` and
    surfaced to MCP callers so log readers can correlate the eventual
    async ``q_learning_complete`` / ``outcome_correlation_applied``
    events back to the originating ``trw_build_check`` call.
    """

    reason: Literal["deferred_always"]
    scheduled_at: str
    thread_state: Literal["launched", "queued", "queue_full"]
    tool_call_id: str


class QLearningHealthDict(TypedDict):
    """Return shape of ``get_q_learning_health()`` (PRD-FIX-088 FR01)."""

    queue_size: int
    error_count: int
    last_error: str | None
    worker_alive: bool


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
    q_learning_dispatch, finalize, total.

    PRD-FIX-088 FR01: ``q_learning_deferred`` is ALWAYS present when
    Q-learning was scheduled (which is now every successful call,
    not only under writer pressure).
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
    duration_secs: float
    cache_path: str
    status: str
    reason: str
    coverage_threshold_failed: bool
    coverage_threshold: float
    coverage_threshold_message: str
    q_learning_deferred: QLearningDeferredDict
    q_learning_error: str
    q_learning_error_count: int
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
    Present on rejection (noise filter): ``reason``, ``message``.
    """

    learning_id: str
    status: str  # "recorded" | "skipped" | "rejected"
    path: str
    distribution_warning: str
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
    # response ONLY on a real corruption event (ok=False), and then only the
    # actionable {"ok": bool, "detail": str}. The full record (incl. db_path /
    # checked_at) always persists to events.jsonl for the audit trail.
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


class ToolEventDataDict(TypedDict, total=False):
    """Shape of the ``event_data`` dict written by ``_write_tool_event`` in telemetry.py.

    Always-present keys: ``tool_name``, ``duration_ms``, ``success``,
    ``status``, ``agent_id``, ``agent_role``, ``phase``.
    Optional: trace fields plus ``error``, ``error_type`` (present only when the tool call raised).
    """

    tool_name: str
    duration_ms: float
    success: bool
    status: str
    agent_id: str
    agent_role: str
    phase: str
    capability_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str
    error: str
    error_type: str
    event_id: str
    parent_event_id: str | None
    tool_call_id: str
    turn_index: int
    input_hash: str
    output_hash: str
    task_profile_hash: str
    causal_relation: str


class TelemetryRecordDict(TypedDict):
    """Shape of the detailed record written by ``_write_telemetry_record`` in telemetry.py.

    Written to ``.trw/logs/tool-telemetry.jsonl`` (FR04).
    All keys are always present.
    """

    tool: str
    args_hash: str
    duration_ms: float
    result_summary: str
    success: bool


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
