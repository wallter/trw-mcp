"""Shared default constants for TRWConfig and domain sub-configs.

Single source of truth for values that appear in both _main.py (TRWConfig)
and _sub_models.py (domain sub-configs).  Both modules import from here
instead of hardcoding their own copies.
"""

# -- Build / mutation --
DEFAULT_BUILD_CHECK_TIMEOUT_SECS: int = 300
DEFAULT_MUTATION_TIMEOUT_SECS: int = 300

# -- Learning storage --
DEFAULT_LEARNING_MAX_ENTRIES: int = 500
DEFAULT_RECALL_RECEIPT_MAX_ENTRIES: int = 1000
DEFAULT_RECALL_MAX_RESULTS: int = 25

# -- Orchestration --
DEFAULT_PARALLELISM_MAX: int = 10

# -- Scoring --
DEFAULT_SCORING_DEFAULT_DAYS_UNUSED: int = 30

# -- Ceremony adaptation (CORE-084) --
LIGHT_MODE_RECALL_CAP: int = 10

# -- Compact mode limits --
COMPACT_TAGS_CAP: int = 10  # Max tags per learning in compact mode

# -- Surface area defaults (PRD-CORE-125) --
DEFAULT_NUDGE_BUDGET_CHARS: int = 600
DEFAULT_LEARNING_PREVIEW_CHARS: int = 500

# -- Tool exposure groups (single source of truth) --
TOOL_GROUP_CORE: tuple[str, ...] = (
    "trw_session_start",
    "trw_checkpoint",
    "trw_learn",
    "trw_deliver",
)
TOOL_GROUP_MEMORY: tuple[str, ...] = (
    "trw_recall",
    "trw_learn_update",
)
TOOL_GROUP_QUALITY: tuple[str, ...] = (
    "trw_build_check",
    "trw_review",
    "trw_prd_create",
    "trw_prd_validate",
)
TOOL_GROUP_OBSERVABILITY: tuple[str, ...] = ("trw_status",)
TOOL_GROUP_ADMIN: tuple[str, ...] = (
    "trw_pre_compact_checkpoint",
    "trw_init",
    "trw_claude_md_sync",
    "trw_knowledge_sync",
    # PRD-CORE-141 FR07/FR08 — per-connection pin liveness + adoption.
    "trw_heartbeat",
    "trw_adopt_run",
)

TOOL_PRESETS: dict[str, tuple[str, ...]] = {
    "core": TOOL_GROUP_CORE,
    "minimal": TOOL_GROUP_CORE + TOOL_GROUP_MEMORY,
    "standard": TOOL_GROUP_CORE + TOOL_GROUP_MEMORY + TOOL_GROUP_QUALITY + ("trw_status", "trw_init"),
    "all": (TOOL_GROUP_CORE + TOOL_GROUP_MEMORY + TOOL_GROUP_QUALITY + TOOL_GROUP_OBSERVABILITY + TOOL_GROUP_ADMIN),
}
