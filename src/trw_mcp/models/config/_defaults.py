"""Shared default constants for TRWConfig and domain sub-configs.

Single source of truth for values that appear in both _main.py (TRWConfig)
and _sub_models.py (domain sub-configs).  Both modules import from here
instead of hardcoding their own copies.

Also the FR05 facade: re-exports the public-configuration admission budget API
(``ConfigAdmission``, ``FIELD_ADMISSIONS``, ``verify_field_admissions``, ...)
from ``_field_admission`` so consumers reach it via the canonical config-
defaults module (PRD-CORE-218-FR05 implementation reference).
"""

# FR05 admission budget (PRD-CORE-218). Imported at top to satisfy ruff E402;
# _field_admission imports nothing from this package, so there is no cycle.
from trw_mcp.models.config._field_admission import (
    FIELD_ADMISSIONS as FIELD_ADMISSIONS,
)
from trw_mcp.models.config._field_admission import (
    LEGACY_ADMITTED_FIELDS as LEGACY_ADMITTED_FIELDS,
)
from trw_mcp.models.config._field_admission import (
    PUBLIC_FIELD_BUDGET as PUBLIC_FIELD_BUDGET,
)
from trw_mcp.models.config._field_admission import (
    ConfigAdmission as ConfigAdmission,
)
from trw_mcp.models.config._field_admission import (
    FieldAdmissionReport as FieldAdmissionReport,
)
from trw_mcp.models.config._field_admission import (
    build_field_admissions as build_field_admissions,
)
from trw_mcp.models.config._field_admission import (
    verify_field_admissions as verify_field_admissions,
)

# PRD-CORE-218 FR02/FR03: pack membership single source of truth. ``surface_packs``
# is a pure stdlib module (zero trw_mcp imports) so importing it during config
# load introduces no cycle and no side effect.
from trw_mcp.models.surface_packs import (
    CAPABILITY_PACKS as _CAPABILITY_PACKS,
)
from trw_mcp.models.surface_packs import (
    KERNEL_TOOLS as _KERNEL_TOOLS,
)
from trw_mcp.models.surface_packs import (
    STANDARD_TASK_PACKS as _STANDARD_TASK_PACKS,
)

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

# -- PRD-CORE-218-FR03 capability-pack fixture (derived, NOT a second table) --
# The pack membership (kernel + the 11 non-kernel packs + the standard task
# mapping) has ONE source of truth: ``trw_mcp.models.surface_packs``. The
# authoritative registry (``server/_surface_manifest_registry.py``) reads the
# SAME module, so ``_defaults`` and the registry can never diverge again (the
# prior duplicate tables silently disagreed on run_maintenance / experimentation
# / telemetry_security / memory_management membership). ``_profiles`` consumes
# these re-exports for FR03 standard-task resolution.
KERNEL_TOOLS = _KERNEL_TOOLS
CAPABILITY_PACKS = _CAPABILITY_PACKS
STANDARD_TASK_PACKS = _STANDARD_TASK_PACKS

#: High-risk packs may be granted ONLY by an explicit phase rule or operator
#: grant — never by provider identity or a vague keyword (FR03 guard). This is
#: FR03 authorization policy (not surface membership), so it stays here.
HIGH_RISK_PACKS: frozenset[str] = frozenset(
    {"dispatch", "experimentation", "run_maintenance", "code_risk", "delivery_operations", "telemetry_security"}
)

#: Vague keyword -> pack hint. Vague keywords may only grant LOW-risk packs; any
#: high-risk suggestion is refused so keyword text can never widen exposure.
KEYWORD_PACK_HINTS: dict[str, str] = {
    "search": "code_navigation",
    "navigate": "code_navigation",
    "review": "verification",
    "dispatch": "dispatch",
    "experiment": "experimentation",
    "maintain": "run_maintenance",
}


# trw_recall response projection (tools/_recall_projection.py): internal
# ranking/telemetry state stripped from RESPONSE entries at the MCP boundary
# (stored rows untouched). Measured 2026-07-12: these fields were ~3x the
# content size, inflating a default recall to ~22k tokens. Empty set disables.
DEFAULT_RECALL_INTERNAL_FIELDS: frozenset[str] = frozenset(
    {
        "access_count",
        "anchor_validity",
        "avg_rework_delta",
        "combined_score",
        "helpful_count",
        "last_accessed_at",
        "outcome_correlation",
        "outcome_history",
        "q_observations",
        "q_value",
        "recall_count",
        "recurrence",
        "session_count",
        "sessions_surfaced",
        "unhelpful_count",
    }
)
