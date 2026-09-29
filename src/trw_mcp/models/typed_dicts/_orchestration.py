"""Orchestration TypedDicts — trw_init, trw_checkpoint, trw_status."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

# ---------------------------------------------------------------------------
# trw_init / trw_checkpoint local shapes
# ---------------------------------------------------------------------------


class CheckpointEventDataDict(TypedDict, total=False):
    """Shape of the ``event_data`` dict logged by ``trw_checkpoint`` to events.jsonl."""

    message: str
    shard_id: str
    slice_done: str


class CheckpointRecordDict(TypedDict, total=False):
    """Shape of the checkpoint record appended to checkpoints.jsonl by ``trw_checkpoint``."""

    ts: str
    message: str
    state: dict[str, object]
    shard_id: str


# ---------------------------------------------------------------------------
# trw_status / reversion metrics
# ---------------------------------------------------------------------------


class StatusReflectionDict(TypedDict):
    """Nested reflection sub-dict within ``TrwStatusDict``."""

    count: int


class StatusReversionLatestDict(TypedDict, total=False):
    """Nested latest-reversion entry within ``StatusReversionMetricsDict``."""

    from_phase: str
    to_phase: str
    trigger: str
    reason: str
    ts: str


class StatusReversionMetricsDict(TypedDict):
    """Return shape of ``_compute_reversion_metrics()`` in orchestration.py.

    ``_compute_reversion_metrics`` always populates all five keys, but the
    ``trw_status`` response compacts the empty cases: ``by_trigger`` is omitted
    when there are no reverts (empty dict) and ``latest`` is omitted when null,
    hence both are ``NotRequired`` on the wire. ``count``/``rate``/
    ``classification`` are always present.
    """

    count: int
    rate: float
    by_trigger: NotRequired[dict[str, int]]
    classification: str
    latest: NotRequired[StatusReversionLatestDict | None]


class DeliverGateScanDict(TypedDict):
    """Return shape of ``compute_deliver_gate_status()`` (PRD-QUAL-105).

    Computed by ``_orchestration_gate_scan.py`` and merged into ``TrwStatusDict``
    by ``trw_status``. All three keys are always present on a successful scan;
    the orchestration wrapper omits them entirely on a fail-open scan error.
    """

    build_gate_ready: bool
    review_gate_ready: bool
    deliver_gate_summary: str


class TrwStatusDict(TypedDict, total=False):
    """Internal construction type for the ``trw_status`` MCP tool.

    The MCP boundary return is typed ``dict[str, object]`` (FastMCP
    serialisation requirement).  This TypedDict documents the internal shape
    and is used to annotate the local ``result`` variable inside the tool.
    """

    run_id: str
    task: str
    phase: str
    status: str
    confidence: str
    framework: str
    # PRD-CORE-184-FR05: task-type regime surfaced for observability.
    task_type: str
    # Canonical task/model policy. The legacy ``model_tier`` output alias was
    # removed 2026-07-27 — it duplicated capability_tier byte-for-byte on
    # every response and no caller read it. Persisted run state is still
    # read under the old key; see _task_profile_observability.
    capability_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str
    # PRD-CORE-184-FR04/FR06: effective task-type nudge weights + recall hint.
    nudge_pool_weights: dict[str, int]
    recall_policy: str
    event_count: int
    reflection: StatusReflectionDict
    # PRD-CORE-329-FR02/NFR01: the oldest pending decision, or an
    # ``{"status": "unreadable"}`` block (FR05). Omitted entirely (never
    # null/empty) when nothing is pending and the queue reads cleanly.
    blocked_decision: dict[str, object]
    blocked_decisions_pending: int
    phase_durations: dict[str, object]
    reversions: StatusReversionMetricsDict
    last_activity_ts: str
    hours_since_activity: float
    # PRD-CORE-338-FR05: present only for a tracked run (NFR02).
    time: dict[str, object]
    version_warning: str
    stale_count: int
    stale_runs_advisory: str
    stale_count_error: bool
    # PRD-QUAL-105: deliver-gate audit trail surfaced at status-check time.
    # Omitted entirely (not None) when the gate scan fails open (FR04).
    build_gate_ready: bool
    review_gate_ready: bool
    deliver_gate_summary: str
    # PRD-CORE-265-FR07: the derived formation board. Present ONLY when a
    # formation is active for the calling run, so its absence is a fact about
    # the run and never a scan failure — an unreadable manifest reports
    # ``formation_error`` instead, which is the distinction NFR02 requires.
    formation: dict[str, object]
    formation_error: str
    # PRD-CORE-311-FR08: the SAME sync-push read `trw-mcp doctor`'s
    # `sync_health` row uses, surfaced here ONLY when degraded -- omitted
    # entirely on a healthy (or NOT_MEASURED) read, per the response
    # token-budget rule (no field on every call for a signal that carries no
    # action). See `_orchestration_status_assembly.py::_apply_sync_push_field`.
    sync_push: dict[str, object]
    # PRD-CORE-305-FR06 (B80-37): names this response's minority "project"
    # keys (a value that looks past the current run -- a multi-run scan, the
    # deployed framework version, project config + session ceremony state,
    # a sibling-run formation board); every other present key is run-scoped
    # per its "note". See _orchestration_status_assembly.py::field_scope_label.
    field_scope: dict[str, object]
