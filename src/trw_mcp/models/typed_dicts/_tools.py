"""MCP tool return TypedDicts (session_start, recall, learn, checkpoint, deliver, usage)."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

from trw_mcp.models.typed_dicts._ceremony import AutoRecalledItemDict

# ---------------------------------------------------------------------------
# trw_usage_report shapes
# ---------------------------------------------------------------------------


class UsageModelEntryDict(TypedDict):
    """Per-model aggregation bucket in ``trw_usage_report`` ``by_model`` dict."""

    calls: int
    input_tokens: int
    output_tokens: int
    cost_estimate_usd: float


class UsageCallerEntryDict(TypedDict):
    """Per-caller aggregation bucket in ``trw_usage_report`` ``by_caller`` dict."""

    calls: int
    input_tokens: int
    output_tokens: int


class UsageGroupEntryDict(TypedDict):
    """Per-bucket aggregation entry in ``trw_usage_report`` ``grouped_by`` dict."""

    calls: int
    input_tokens: int
    output_tokens: int
    cost_estimate_usd: float


class UsageReportResult(TypedDict, total=False):
    """Return shape of ``trw_usage_report`` MCP tool.

    Always-present keys: ``period``, ``log_path``, ``total_calls``,
    ``total_input_tokens``, ``total_output_tokens``, ``total_cost_estimate_usd``,
    ``by_model``, ``by_caller``.

    Optional keys present when ``group_by != "none"``:
    ``group_by``, ``grouped_by``.

    Also present on the empty-log early-exit path: ``message``.
    """

    period: str
    log_path: str
    message: str
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_estimate_usd: float
    by_model: dict[str, UsageModelEntryDict]
    by_caller: dict[str, UsageCallerEntryDict]
    # populated when group_by != "none"
    group_by: str
    grouped_by: dict[str, UsageGroupEntryDict]


# ---------------------------------------------------------------------------
# trw_progressive_expand shape
# ---------------------------------------------------------------------------


class ProgressiveExpandResult(TypedDict):
    """Return shape of ``trw_progressive_expand`` MCP tool."""

    group: str
    expanded_tools: list[str]
    already_expanded: list[str]


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
    count: int
    ceremony_hint: str
    # Token budget fields (PRD-CORE-123 Phase 2)
    tokens_used: int
    tokens_budget: int | None
    tokens_truncated: bool


class RunStatusDict(TypedDict, total=False):
    """Run status sub-dict used in session_start and trw_status."""

    active_run: str | None
    phase: str
    status: str
    task_name: str
    owner_session_id: str | None
    wave_status: dict[str, object] | None


class SessionStartResultDict(TypedDict, total=False):
    """Return shape of ``trw_session_start`` MCP tool."""

    timestamp: str
    learnings: list[dict[str, object]]
    learnings_count: int
    query: str
    query_matched: int
    total_available: int
    run: RunStatusDict
    embeddings_advisory: str
    errors: list[str]
    success: bool
    framework_reminder: str
    ceremony_status: str
    # Auto-recall (phase-contextual, PRD-CORE-049)
    auto_recalled: list[AutoRecalledItemDict]
    auto_recall_count: int
    # Embed health advisory (PRD-FIX-053)
    embed_health: dict[str, object]
    # Assertion health summary (PRD-CORE-086 FR07) — omitted when no assertions
    assertion_health: dict[str, int]
    # Auto-maintenance results merged in from AutoMaintenanceDict
    update_advisory: str
    auto_upgrade: dict[str, object]
    stale_runs_closed: dict[str, object]
    embeddings_backfill: dict[str, int]
    # PRD-CORE-141 FR06: Structured guidance when no pin exists for the
    # caller's ctx — directs agents to ``trw_init`` (new run) or to pass
    # ``run_path`` (resume). Populated only on the no-pin path.
    hint: str
    # PRD-HPO-MEAS-001 FR-2: Resolved surface snapshot id for the session.
    # Empty string during Phase 1 when artifact_registry stamping is
    # unavailable or fails open. Every HPOTelemetryEvent emitted during the
    # session is expected to carry this id (post Wave-2 wiring).
    surface_snapshot_id: str
    # PRD-HPO-MEAS-001 NFR-12: Boot-audit failures surfaced to the caller.
    # Absent on success; populated with ``{key, expected, actual, remediation}``
    # entries when any Phase-1 default cannot be resolved.
    boot_audit_failures: list[dict[str, str]]


class RunReportResultDict(TypedDict, total=False):
    """Return shape of ``trw_run_report`` MCP tool.

    All keys optional via ``total=False``; the success path populates the
    ``RunReport`` model fields, the error path populates ``error`` + ``status``.
    """

    # Success path (RunReport.model_dump())
    run_id: str
    task: str
    status: str
    phase: str
    framework: str
    run_type: str
    generated_at: str
    prd_scope: list[str]
    duration: dict[str, object]
    phase_timeline: list[dict[str, object]]
    event_summary: dict[str, object]
    checkpoint_count: int
    learning_summary: dict[str, object]
    build: dict[str, object] | None
    reversion_rate: float
    session_metrics: dict[str, object]
    # Error path
    error: str


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


class DeliverResultDict(TypedDict, total=False):
    """Return shape of ``trw_deliver`` MCP tool."""

    timestamp: str
    run_path: str | None
    # Gate warnings (merged from DeliveryGatesDict)
    review_warning: str
    review_advisory: str
    review_scope_block: str
    integration_review_block: str
    integration_review_warning: str
    untracked_warning: str
    build_gate_warning: str
    checkpoint_blocker_warning: str
    complexity_drift_warning: str
    warning: str
    # Compliance artifacts (merged from ComplianceArtifactsDict)
    compliance_artifacts_copied: list[str]
    compliance_dir: str
    reflect: dict[str, object]
    checkpoint: dict[str, object]
    claude_md_sync: dict[str, object]
    critical_elapsed_seconds: float
    deferred: str
    errors: list[str]
    success: bool
    critical_steps_completed: int
    deferred_steps: int
    # PRD-CORE-125 FR05: Self-reflection message about learnings
    learning_reflection: str
    # PRD-INFRA-067 (C2): Integrity-on-delivery probe result
    # Shape: {"ok": bool, "detail": str, "db_path": str, "checked_at": str}
    db_integrity: dict[str, object]
    # PRD-INFRA-068 (C3): Memory health dashboard — surfaced here so clients
    # can report health when deliver is a session's last action.
    memory_health: dict[str, object]
    # PRD-HPO-MEAS-001 FR-5: CLEAR 5-dimensional score for the closed
    # session. Populated when load_and_score_run produces a record.
    # Shape matches ``ClearScore.model_dump(mode="json")``.
    clear_score: dict[str, object]


class ToolEventDataDict(TypedDict, total=False):
    """Shape of the ``event_data`` dict written by ``_write_tool_event`` in telemetry.py.

    Always-present keys: ``tool_name``, ``duration_ms``, ``success``,
    ``status``, ``agent_id``, ``agent_role``, ``phase``.
    Optional: ``error``, ``error_type`` (present only when the tool call raised).
    """

    tool_name: str
    duration_ms: float
    success: bool
    status: str
    agent_id: str
    agent_role: str
    phase: str
    error: str
    error_type: str


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
