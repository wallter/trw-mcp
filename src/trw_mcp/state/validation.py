"""Schema validation and output contract checking — re-export facade.

This module was refactored into focused sub-modules:

- risk_profiles.py      — Risk-based validation scaling (PRD-QUAL-013)
- event_helpers.py       — Event I/O helpers for phase gate validation
- contract_validation.py — Output contract validation for shard/wave artifacts
- phase_gates.py         — Phase gate exit/input criteria checks
- phase_gates_build.py   — Build status checks for phase gates
- phase_gates_prd.py     — PRD enforcement for phase gates
- prd_quality.py         — PRD quality validation (V1, V2 scoring)
- prd_progression.py     — PRD auto-progression on phase gate pass
- integration_check.py   — Tool registration and test coverage checks

All public names are re-exported here for backward compatibility.
"""

from __future__ import annotations

from trw_mcp.state.contract_validation import (
    _INTEGRATION_CHECKLIST as _INTEGRATION_CHECKLIST,
)

# --- contract_validation ---
from trw_mcp.state.contract_validation import (
    ContractValidator as ContractValidator,
)
from trw_mcp.state.contract_validation import (
    FileContractValidator as FileContractValidator,
)
from trw_mcp.state.contract_validation import (
    validate_wave_contracts as validate_wave_contracts,
)

# --- event_helpers ---
from trw_mcp.state.event_helpers import (
    _REFLECTION_EVENTS as _REFLECTION_EVENTS,
)
from trw_mcp.state.event_helpers import (
    _SYNC_EVENTS as _SYNC_EVENTS,
)
from trw_mcp.state.event_helpers import (
    _events_contain as _events_contain,
)
from trw_mcp.state.event_helpers import (
    _is_validate_pass as _is_validate_pass,
)
from trw_mcp.state.event_helpers import (
    _read_events as _read_events,
)
from trw_mcp.state.integration_check import (
    _CHECKBOX_RE as _CHECKBOX_RE,
)
from trw_mcp.state.integration_check import (
    _EXIT_CRITERIA_RE as _EXIT_CRITERIA_RE,
)

# --- integration_check ---
from trw_mcp.state.integration_check import (
    check_integration as check_integration,
)
from trw_mcp.state.integration_check import (
    parse_exit_criteria as parse_exit_criteria,
)
from trw_mcp.state.phase_gates import (
    PHASE_EXIT_CRITERIA as PHASE_EXIT_CRITERIA,
)
from trw_mcp.state.phase_gates import (
    PHASE_INPUT_CRITERIA as PHASE_INPUT_CRITERIA,
)
from trw_mcp.state.phase_gates import (
    _build_phase_result as _build_phase_result,
)

# --- phase_gates ---
from trw_mcp.state.phase_gates import (
    check_phase_exit as check_phase_exit,
)
from trw_mcp.state.phase_gates import (
    check_phase_input as check_phase_input,
)

# --- phase_gates_build ---
from trw_mcp.state.phase_gates_build import (
    _BUILD_STALENESS_SECS as _BUILD_STALENESS_SECS,
)
from trw_mcp.state.phase_gates_build import (
    _best_effort_build_check as _best_effort_build_check,
)
from trw_mcp.state.phase_gates_build import (
    _best_effort_integration_check as _best_effort_integration_check,
)
from trw_mcp.state.phase_gates_build import (
    _check_build_status as _check_build_status,
)
from trw_mcp.state.phase_gates_prd import (
    _STATUS_ORDER as _STATUS_ORDER,
)

# --- phase_gates_prd ---
from trw_mcp.state.phase_gates_prd import (
    _check_prd_enforcement as _check_prd_enforcement,
)
from trw_mcp.state.prd_progression import (
    _TERMINAL_STATUSES as _TERMINAL_STATUSES,
)
from trw_mcp.state.prd_progression import (
    PHASE_STATUS_MAPPING as PHASE_STATUS_MAPPING,
)

# --- prd_progression ---
from trw_mcp.state.prd_progression import (
    auto_progress_prds as auto_progress_prds,
)
from trw_mcp.state.prd_quality import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTION_NAMES,
)
from trw_mcp.state.prd_quality import (
    _GRADE_MAP as _GRADE_MAP,
)
from trw_mcp.state.prd_quality import (
    _HEADING_RE as _HEADING_RE,
)
from trw_mcp.state.prd_quality import (
    _HIGH_WEIGHT_SECTIONS as _HIGH_WEIGHT_SECTIONS,
)
from trw_mcp.state.prd_quality import (
    _PLACEHOLDER_RE as _PLACEHOLDER_RE,
)
from trw_mcp.state.prd_quality import (
    _coerce_v1_failures as _coerce_v1_failures,
)
from trw_mcp.state.prd_quality import (
    _is_substantive_line as _is_substantive_line,
)
from trw_mcp.state.prd_quality import (
    _parse_section_content as _parse_section_content,
)
from trw_mcp.state.prd_quality import (
    classify_quality_tier as classify_quality_tier,
)
from trw_mcp.state.prd_quality import (
    generate_improvement_suggestions as generate_improvement_suggestions,
)
from trw_mcp.state.prd_quality import (
    map_grade as map_grade,
)
from trw_mcp.state.prd_quality import (
    score_content_density as score_content_density,
)
from trw_mcp.state.prd_quality import (
    score_section_density as score_section_density,
)
from trw_mcp.state.prd_quality import (
    score_structural_completeness as score_structural_completeness,
)
from trw_mcp.state.prd_quality import (
    score_traceability_v2 as score_traceability_v2,
)

# --- prd_quality ---
from trw_mcp.state.prd_quality import (
    validate_prd_quality as validate_prd_quality,
)
from trw_mcp.state.prd_quality import (
    validate_prd_quality_v2 as validate_prd_quality_v2,
)
from trw_mcp.state.risk_profiles import (
    RISK_PROFILES as RISK_PROFILES,
)

# --- risk_profiles ---
from trw_mcp.state.risk_profiles import (
    RiskProfile as RiskProfile,
)
from trw_mcp.state.risk_profiles import (
    derive_risk_level as derive_risk_level,
)
from trw_mcp.state.risk_profiles import (
    get_risk_scaled_config as get_risk_scaled_config,
)

__all__ = [
    "PHASE_EXIT_CRITERIA",
    "PHASE_INPUT_CRITERIA",
    "PHASE_STATUS_MAPPING",
    "RISK_PROFILES",
    "_BUILD_STALENESS_SECS",
    "_CHECKBOX_RE",
    "_EXIT_CRITERIA_RE",
    "_EXPECTED_SECTION_NAMES",
    "_GRADE_MAP",
    "_HEADING_RE",
    "_HIGH_WEIGHT_SECTIONS",
    "_INTEGRATION_CHECKLIST",
    "_PLACEHOLDER_RE",
    "_REFLECTION_EVENTS",
    "_STATUS_ORDER",
    "_SYNC_EVENTS",
    "_TERMINAL_STATUSES",
    "ContractValidator",
    "FileContractValidator",
    "RiskProfile",
    "_best_effort_build_check",
    "_best_effort_integration_check",
    "_build_phase_result",
    "_check_build_status",
    "_check_prd_enforcement",
    "_coerce_v1_failures",
    "_events_contain",
    "_is_substantive_line",
    "_is_validate_pass",
    "_parse_section_content",
    "_read_events",
    "auto_progress_prds",
    "check_integration",
    "check_phase_exit",
    "check_phase_input",
    "classify_quality_tier",
    "derive_risk_level",
    "generate_improvement_suggestions",
    "get_risk_scaled_config",
    "map_grade",
    "parse_exit_criteria",
    "score_content_density",
    "score_section_density",
    "score_structural_completeness",
    "score_traceability_v2",
    "validate_prd_quality",
    "validate_prd_quality_v2",
    "validate_wave_contracts",
]
