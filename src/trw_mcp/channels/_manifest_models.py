"""Channel manifest Pydantic models and shared constants.

Defines the canonical 38-field ChannelEntry schema, all supporting enums,
marker registry, and cross-client meta-tune constants for PRD-DIST-2400 Phase A.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class WriteStrategy(str, Enum):
    FULL_REWRITE = "FULL_REWRITE"
    MARKER_REPLACE = "MARKER_REPLACE"
    APPEND_WITH_TTL = "APPEND_WITH_TTL"
    JSON_KEY_MERGE = "JSON_KEY_MERGE"
    EPHEMERAL_STDOUT = "EPHEMERAL_STDOUT"
    NONE = "NONE"


class ChannelSurface(str, Enum):
    AGENTS_MD_SEGMENT = "agents_md_segment"
    CLAUDE_MD_SEGMENT = "claude_md_segment"
    COPILOT_INSTRUCTIONS_SEGMENT = "copilot_instructions_segment"
    CURSOR_MDC_FILE = "cursor_mdc_file"
    CODEX_AGENTS_MD_SEGMENT = "codex_agents_md_segment"
    OPENCODE_RULES_SEGMENT = "opencode_rules_segment"
    ANTIGRAVITY_RULES_SEGMENT = "antigravity_rules_segment"
    INSTRUCTION_FILE_SEGMENT = "instruction_file_segment"
    VSCODE_MCP_JSON = "vscode_mcp_json"
    GEMINI_MD_SEGMENT = "gemini_md_segment"
    EXPLORER_PANEL = "explorer_panel"
    EPHEMERAL_STDOUT = "ephemeral_stdout"


class ChannelStatus(str, Enum):
    ACTIVE = "active"
    ASPIRATIONAL = "aspirational"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class HumanEditDetection(str, Enum):
    NONE = "NONE"
    MARKER_BOUNDARY = "MARKER_BOUNDARY"
    SHA256_SEGMENT = "SHA256_SEGMENT"
    KEY_NAMESPACE = "KEY_NAMESPACE"
    RENDER_LOG = "RENDER_LOG"


class CleanupTrigger(str, Enum):
    TTL_EXCEEDED = "TTL_EXCEEDED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    DISABLED = "DISABLED"
    NONE = "NONE"


class CleanupAction(str, Enum):
    TIER_DOWN = "TIER_DOWN"
    TIER_DOWN_TO_T0 = "TIER_DOWN_TO_T0"
    FULL_PRUNE = "FULL_PRUNE"
    CLEAR_SEGMENT = "CLEAR_SEGMENT"
    SUPPRESS = "SUPPRESS"
    TOMBSTONE = "TOMBSTONE"
    NONE = "NONE"


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class MarkersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    start: str = ""
    end: str = ""


class ProvenanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    enabled: bool = True
    detection: HumanEditDetection = HumanEditDetection.SHA256_SEGMENT


class CleanupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    trigger: CleanupTrigger = CleanupTrigger.NONE
    action: CleanupAction = CleanupAction.NONE


# ---------------------------------------------------------------------------
# ChannelEntry — canonical 38-field manifest entry
# ---------------------------------------------------------------------------

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class ChannelEntry(BaseModel):
    model_config = ConfigDict(
        use_enum_values=True,
        extra="forbid",
        populate_by_name=True,
    )

    # --- Core identity (required) ---
    id: str
    client: str
    surface: ChannelSurface
    telemetry_tag: str

    # --- File targeting ---
    file: str | None = None
    lock_file: str | None = None

    # --- Status and strategy ---
    status: ChannelStatus = ChannelStatus.ACTIVE
    write_strategy: WriteStrategy = WriteStrategy.MARKER_REPLACE

    # --- Tier configuration ---
    tier_default: str = "T2"
    tier_min: str = "T0"
    operator_tier_override_key: str | None = None

    # --- Marker configuration ---
    markers: MarkersConfig = Field(default_factory=MarkersConfig)

    # --- Record types ---
    distill_record_types: list[str] = Field(default_factory=list)

    # --- TTL configuration ---
    ttl_commits: int | None = None
    ttl_days: int | None = None

    # --- Quota configuration ---
    quota_total_bytes: int | None = None
    quota_warn_bytes: int | None = None

    # --- Provenance ---
    provenance: ProvenanceConfig = Field(default_factory=ProvenanceConfig)

    # --- Cleanup ---
    cleanup: CleanupConfig = Field(default_factory=CleanupConfig)

    # --- Lock lifecycle ---
    lock_lifecycle: str = "auto_cleanup_on_channel_disable"

    # --- Human-edit detection ---
    human_edit_detection: HumanEditDetection = HumanEditDetection.SHA256_SEGMENT

    # --- Metadata / docs ---
    description: str | None = None
    regenerate_cmd: str | None = None
    client_version_min: str | None = None

    # --- MDC-specific (Cursor) ---
    mdc_description: str | None = None
    mdc_globs: str | None = None
    mdc_always_apply: bool = False

    # --- Telemetry and correlation ---
    session_correlation: bool = True
    emit_on_ttl_skip: bool = True
    emit_on_conflict_skip: bool = True
    emit_on_lock_skip: bool = True

    # --- Sidecar contract ---
    sidecar_schema: str = "risk-report-sidecar/v0"
    sidecar_path: str | None = None

    # --- Hook integration ---
    hook_schema_confirmed_at: str | None = None

    # --- Aspirational gating ---
    activation_gate: str | None = None

    # --- Extended metadata ---
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(
                f"Channel id {v!r} must be alphanumeric with hyphens/underscores only"
            )
        return v


# ---------------------------------------------------------------------------
# Marker registry — canonical marker strings (SYS-05 collision check)
# ---------------------------------------------------------------------------

MARKER_REGISTRY: dict[str, str] = {
    "trw_start_generic": "<!-- trw:start -->",
    "trw_end_generic": "<!-- trw:end -->",
    "trw_distill_start": "<!-- trw:distill:start -->",
    "trw_distill_end": "<!-- trw:distill:end -->",
    "trw_memory_start": "<!-- trw:memory:start -->",
    "trw_memory_end": "<!-- trw:memory:end -->",
    "trw_cursor_mdc_start": "<!-- trw:cursor:mdc:start -->",
    "trw_cursor_mdc_end": "<!-- trw:cursor:mdc:end -->",
    "trw_codex_start": "<!-- trw:codex:start -->",
    "trw_codex_end": "<!-- trw:codex:end -->",
    "trw_copilot_start": "<!-- trw:copilot:start -->",
    "trw_copilot_end": "<!-- trw:copilot:end -->",
    "trw_mdc_gitignore_begin": "# TRW:MDC:BEGIN",
    "trw_mdc_gitignore_end": "# TRW:MDC:END",
    "trw_provenance_start": "<!-- TRW:PROVENANCE",
    "trw_provenance_end": "-->",
}

# ---------------------------------------------------------------------------
# Cross-client meta-tune contract constants (FR22-FR24)
# ---------------------------------------------------------------------------

JOIN_KEY_FIELDS: tuple[str, str] = ("session_id", "file_path")

DEFAULT_CORRELATION_WINDOW_SECONDS: int = 3600

CLIENT_CORRECTION_FACTORS: dict[str, float] = {
    "claude-code": 0.85,
    "codex": 0.70,
    "antigravity-cli": 0.50,
    "opencode": 0.40,
    "cursor-ide": 0.75,
    "cursor-cli": 0.75,
    "copilot": 0.50,
}

# Values are (threshold, window_count)
CLIENT_THROTTLE_THRESHOLDS: dict[str, tuple[float, int]] = {
    "claude-code": (0.25, 3),
    "codex": (0.20, 3),
    "antigravity-cli": (0.15, 5),
    "opencode": (0.15, 5),
    "cursor-ide": (0.20, 3),
    "cursor-cli": (0.20, 3),
    "copilot": (0.15, 5),
}

# Copilot requires minimum N=50 before throttle applies (not the default 30)
COPILOT_THROTTLE_MIN_N: int = 50
DEFAULT_THROTTLE_MIN_N: int = 30
