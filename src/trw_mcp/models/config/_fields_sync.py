"""Sync pipeline configuration fields — PRD-INFRA-051-FR07.

Covers backend sync connectivity and push/pull settings.
All fields default to disabled/offline so existing users see zero behavior change.
"""

from __future__ import annotations

from pydantic import Field


class _SyncFields:
    """Sync domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Backend connectivity --
    backend_url: str = ""
    backend_api_key: str = Field(default="", json_schema_extra={"secret": True})

    # -- Push/Pull cadence --
    sync_interval_seconds: int = 300
    sync_push_batch_size: int = 100
    sync_push_timeout_seconds: float = 10.0

    # -- Pull settings (PRD-INFRA-053) --
    sync_pull_timeout_seconds: float = 5.0
    intel_cache_ttl_seconds: int = 3600
    intel_cache_enabled: bool = True

    # -- Feature gates --
    meta_tune_enabled: bool = False
    team_sync_enabled: bool = False

    # -- Sync health surface (PRD-FIX-COMPOUNDING-1) --
    # Env-overridable via TRW_SYNC_HEALTH_FAILURE_THRESHOLD / TRW_SYNC_HEALTH_STALE_HOURS.
    sync_health_failure_threshold: int = Field(
        default=10,
        ge=1,
        description="Consecutive push failures before sync_health marks degraded.",
    )
    sync_health_stale_hours: float = Field(
        default=6.0,
        ge=0.1,
        description="Hours since last successful push before sync_health marks degraded.",
    )

    # -- Pipeline-health enforcement gate (PRD-FIX-107 FR06) --
    # "Enforce, don't suggest": the fail-closed gate that surfaces push
    # staleness, an empty knowledge graph, or a localhost-only sync target so a
    # silent compounding-pipeline outage can never recur. Env-overridable via
    # TRW_PIPELINE_HEALTH_GATE_ENABLED / TRW_PIPELINE_HEALTH_GATE_FAILURE_THRESHOLD /
    # TRW_PIPELINE_HEALTH_GATE_STALE_HOURS / TRW_PIPELINE_HEALTH_GATE_GRAPH_MIN_CORPUS.
    pipeline_health_gate_enabled: bool = Field(
        default=True,
        description="Master kill switch for the FR06 fail-closed pipeline-health gate.",
    )
    pipeline_health_gate_failure_threshold: int = Field(
        default=10,
        ge=1,
        description="Consecutive push failures before the FR06 gate fails closed.",
    )
    pipeline_health_gate_stale_hours: float = Field(
        default=6.0,
        ge=0.1,
        description="Hours since last successful push before the FR06 gate fails closed.",
    )
    pipeline_health_gate_graph_min_corpus: int = Field(
        default=10,
        ge=1,
        description="Minimum memories before an empty knowledge graph fails the FR06 gate.",
    )

    # -- Pipeline-health bandit probe (PRD-FIX-105-FR02) --
    # The bandit_state.json file is written by the BACKEND meta-tune policy
    # (backend/services/bandit_policy.py), NOT by the MCP runtime. In a dev repo
    # or any deployment where the backend bandit is not actively driven, the file
    # legitimately goes stale and the probe cries wolf. These knobs let operators
    # tune the SLA or disable the probe entirely where no local writer exists.
    pipeline_health_bandit_probe_enabled: bool = Field(
        default=True,
        description="Whether the pipeline-health bandit_state staleness probe is active.",
    )
    pipeline_health_bandit_stale_days: float = Field(
        default=7.0,
        ge=0.1,
        description="Days since bandit_state.json mtime before the probe marks degraded.",
    )
