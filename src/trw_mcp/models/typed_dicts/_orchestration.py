"""Orchestration TypedDicts — trw_init, trw_checkpoint, trw_status, wave progress."""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

# ---------------------------------------------------------------------------
# trw_init / trw_checkpoint local shapes
# ---------------------------------------------------------------------------


class TrwInitConfigDataDict(TypedDict, total=False):
    """Shape of ``config_data`` written to ``.trw/config.yaml`` during ``trw_init``.

    Always-present keys: ``framework_version``, ``telemetry``,
    ``parallelism_max``, ``timebox_hours``.  Extra keys may be merged in from
    ``config_overrides``, hence ``total=False``.
    """

    framework_version: str
    telemetry: bool
    parallelism_max: int
    timebox_hours: float


class CheckpointEventDataDict(TypedDict, total=False):
    """Shape of the ``event_data`` dict logged by ``trw_checkpoint`` to events.jsonl."""

    message: str
    shard_id: str
    wave_id: str


class CheckpointRecordDict(TypedDict, total=False):
    """Shape of the checkpoint record appended to checkpoints.jsonl by ``trw_checkpoint``."""

    ts: str
    message: str
    state: dict[str, object]
    shard_id: str
    wave_id: str


class DeployFrameworksVersionDataDict(TypedDict):
    """Shape of ``version_data`` written to ``frameworks/VERSION.yaml`` by ``_deploy_frameworks``."""

    framework_version: str
    aaref_version: str
    trw_mcp_version: str
    deployed_at: str


# ---------------------------------------------------------------------------
# trw_status / wave progress / reversion metrics
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


class WaveShardCountsDict(TypedDict):
    """Shard status counts within a single ``WaveDetailDict``."""

    total: int
    complete: int
    active: int
    pending: int
    failed: int
    partial: int


class WaveDetailDict(TypedDict):
    """One wave detail entry within ``WaveProgressDict``."""

    wave: int
    status: str
    shards: WaveShardCountsDict


class WaveProgressDict(TypedDict):
    """Return shape of ``_compute_wave_progress()`` in orchestration.py."""

    total_waves: int
    completed_waves: int
    active_wave: int | None
    wave_details: list[WaveDetailDict]


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
    # Canonical task/model policy. ``model_tier`` is a compatibility alias.
    capability_tier: str
    model_tier: str
    recommended_effort: str
    effort_source: str
    effort_adapter_status: str
    # PRD-CORE-184-FR04/FR06: effective task-type nudge weights + recall hint.
    nudge_pool_weights: dict[str, int]
    recall_policy: str
    event_count: int
    reflection: StatusReflectionDict
    phase_durations: dict[str, object]
    waves: list[dict[str, object]]
    wave_progress: WaveProgressDict
    wave_status: dict[str, object]
    reversions: StatusReversionMetricsDict
    last_activity_ts: str
    hours_since_activity: float
    version_warning: str
    stale_count: int
    stale_runs_advisory: str
    stale_count_error: bool
    # PRD-QUAL-105: deliver-gate audit trail surfaced at status-check time.
    # Omitted entirely (not None) when the gate scan fails open (FR04).
    build_gate_ready: bool
    review_gate_ready: bool
    deliver_gate_summary: str
