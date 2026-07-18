"""Directory structure, project source paths, and platform fields.

Covers sections 13, 27, 37, 38 of the original _main_fields.py:
  - Directory structure & paths
  - Project source paths
  - Platform & update channel
  - Knowledge topology (CORE-021)
"""

from __future__ import annotations

from pydantic import Field, SecretStr


class _PathsFields:
    """Paths domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Directory structure & paths --

    task_root: str = "docs"
    runs_root: str = ".trw/runs"
    trw_dir: str = ".trw"
    worktree_dir: str = ".trees"
    learnings_dir: str = "learnings"
    entries_dir: str = "entries"
    receipts_dir: str = "receipts"
    reflections_dir: str = "reflections"
    scripts_dir: str = "scripts"
    patterns_dir: str = "patterns"
    context_dir: str = "context"
    scratch_dir: str = "scratch"
    events_file: str = "events.jsonl"
    checkpoints_file: str = "checkpoints.jsonl"
    frameworks_dir: str = "frameworks"
    templates_dir: str = "templates"

    # -- Project source paths --

    source_package_path: str = "trw-mcp/src"
    source_package_name: str = "trw_mcp"
    tests_relative_path: str = "trw-mcp/tests"
    test_map_filename: str = "test-map.yaml"

    # -- Platform & update channel --

    platform_telemetry_enabled: bool = False
    update_channel: str = "latest"
    platform_url: str = ""
    platform_urls: list[str] = Field(default_factory=list)
    platform_api_key: SecretStr = SecretStr("")
    installation_id: str = ""
    auto_upgrade: bool = False

    # -- Knowledge topology (CORE-021) --

    knowledge_sync_threshold: int = 50
    knowledge_jaccard_threshold: float = Field(default=0.3, ge=0.0, le=1.0)
    knowledge_min_cluster_size: int = Field(default=3, ge=1)
    knowledge_output_dir: str = "knowledge"
    # F5 suggestion 2: opportunistic time-boxed knowledge-graph backfill on
    # deliver. Builds edges for un-graphed entries within the deadline budget
    # (WAL gives the singleton reader/writer isolation), then leaves the rest
    # for the next deliver or a forced ``trw_knowledge_sync(force=True)``.
    deliver_graph_backfill_enabled: bool = True
    deliver_graph_backfill_deadline_seconds: float = Field(default=2.0, ge=0.0)

    # -- Version drift check --

    version_check_interval_seconds: float = Field(
        default=300.0,
        ge=0.0,
        description=(
            "Interval (seconds) between cached booted-vs-installed trw-mcp version-drift "
            "checks on the tool-call middleware path (PRD-CORE-215-FR02). The narrow comparator "
            "(booted trw_mcp.__version__ vs importlib.metadata.version('trw-mcp')) is recomputed "
            "lazily at most once per interval, so it never adds measurable hot-path latency. On "
            "drift a non-blocking advisory is attached to the tool result once per drift value per "
            "session; 0 rechecks on every call."
        ),
    )

    # -- Pin isolation & stale-run lifecycle (PRD-CORE-141 FR13) --
    # Knobs governing per-connection pin isolation, the boot-time stale-run
    # sweep, and the heartbeat liveness window. All fields are env-overridable
    # via TRW_<UPPER> and reload into the pydantic-settings flat access path.

    run_staleness_hours: int = Field(
        default=48,
        ge=1,
        description="Age (hours) beyond which an active run with no activity is eligible for the stale-run sweep.",
    )
    run_staleness_grace_hours: int = Field(
        default=12,
        ge=0,
        description="Grace window (hours) appended to run_staleness_hours; runs inside this window emit run_near_stale_warning but are not abandoned.",
    )
    pin_ttl_hours: int = Field(
        default=24,
        ge=1,
        description="Time-to-live (hours) for entries in the persistent pin store before GC evicts them.",
    )
    run_archive_hours: int = Field(
        default=720,  # 30 days; reserved for future archive PRD
        ge=1,
        description="Age (hours) beyond which abandoned runs become eligible for archival (reserved for future PRD).",
    )
    cleanup_on_boot: bool = Field(
        default=True,
        description="When True, the MCP server runs the stale-pin + stale-run sweep on startup.",
    )
    boot_gc_deferred: bool = Field(
        default=True,
        description=(
            "When True, the boot-time stale-run + stale-pin sweep runs in a daemon "
            "background thread ('trw-boot-gc') instead of synchronously before the MCP "
            "initialize handshake, so large repos do not stall client connect (production "
            "feedback sub_psVs_nUWnLJGvOs3). Set False to force the legacy synchronous "
            "pre-transport sweep for deterministic startup ordering. Independent of "
            "cleanup_on_boot, which gates whether the sweep runs at all."
        ),
    )
    checkpoint_suggest_hours: int = Field(
        default=4,
        ge=1,
        description="Heartbeat-age threshold (hours) at which trw_heartbeat reports should_checkpoint=True to the caller.",
    )
    ctx_isolation_enabled: bool = Field(
        default=True,
        description="Master kill-switch for per-connection pin isolation. When False, resolve_pin_key returns the process UUID regardless of ctx — matches pre-PRD-CORE-141 behavior (Wave 3 rollback).",
    )

    # -- User-space memory tier (PRD-CORE-185) --
    # Machine-layer knobs for the machine-local user-space memory tier. The
    # EFFECTIVE gate is presence of the user-scope store (installer-detected,
    # FR09); ``user_tier_enabled`` is an installer-seeded machine-layer knob +
    # emergency kill switch. Absent a user-scope store, behavior is project-only
    # regardless of this value (NFR02). Typically set at the machine layer
    # (``~/.trw/config.yaml``) so it applies box-wide via the FR04 cascade.

    user_tier_enabled: bool = Field(
        default=False,
        description="Installer-seeded machine-layer knob / emergency kill switch for the user-space memory tier. Effective gate is presence of the user-scope store; absent that store behavior is project-only regardless of this value (PRD-CORE-185 NFR02).",
    )
    recall_user_tier_cap: int = Field(
        default=5,
        ge=0,
        description="Maximum number of user-tier hits that may enter a single federated recall result, so a flood of low-value user hits cannot bury a precise project hit (PRD-CORE-185 FR06 / D3 / R4).",
    )
