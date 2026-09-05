"""Ceremony, compliance, documentation generation, and enforcement fields.

PRD-CORE-250-FR10's degenerate-result advisory tunables and the nudge-engine
tunables (pool routing, urgency, budget, cooldowns) were split out to
``_fields_degenerate_result.py`` and ``_fields_nudge.py`` respectively when
this file reached the 200-raw-line domain-mixin ceiling
(``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``).
``NudgeMessengerLiteral`` stays declared here -- imported by ``_main.py``'s
TYPE_CHECKING re-declaration, ``tests/test_nudge_messengers.py``, and
``_fields_nudge.py`` -- rather than moved, so those import paths are unchanged.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

NudgeMessengerLiteral = Literal[
    "standard",
    "minimal",
    # PRD-CORE-241-FR07 retired "learning_injection" (iter-22: 50.0% vs 66.7%,
    # n=30, p=0.1527, REJECTED). "contextual" supersedes it — do not reinstate.
    "contextual",
    "contextual_action",
    "contextual_distress",
    "silent_flow",
    "cod",
    "stepback",
    "anchor",
    "negative",
    "governance",
]


class _CeremonyFields:
    """Ceremony domain mixin — mixed into _TRWConfigFields via MI."""

    claude_md_max_lines: int = 500
    sub_claude_md_max_lines: int = 50
    max_auto_lines: int = 300
    agents_md_enabled: bool = True
    target_platforms: list[str] = Field(
        default_factory=lambda: ["claude-code"],
        description="Platforms to sync instruction files for.",
    )
    ceremony_mode: Literal["full", "light"] = "full"
    response_format: Literal["yaml", "json"] = "yaml"
    agents_md_learning_injection: bool = True
    agents_md_learning_max: int = 5
    agents_md_learning_min_impact: float = 0.7

    framework_version: str = "v26.2_TRW"
    aaref_version: str = "v3.2.1"

    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    consistency_validation_min: float = 0.95
    validation_density_weight: float = 20.0
    validation_structure_weight: float = 20.0
    validation_implementation_readiness_weight: float = 25.0
    validation_traceability_weight: float = 35.0
    validation_smell_weight: float = 0.0
    validation_readability_weight: float = 0.0
    validation_ears_weight: float = 0.0
    density_weight_problem_statement: float = Field(default=2.0, ge=0.0, le=10.0)
    density_weight_functional_requirements: float = Field(default=2.0, ge=0.0, le=10.0)
    density_weight_traceability_matrix: float = Field(default=1.5, ge=0.0, le=10.0)
    density_weight_default: float = Field(default=1.0, ge=0.0, le=10.0)
    validation_skeleton_threshold: float = 30.0
    validation_draft_threshold: float = 60.0
    validation_review_threshold: float = 85.0
    validation_fk_optimal_min: float = 8.0
    validation_fk_optimal_max: float = 12.0
    # Wiring gate (PRD-CORE-190): warn=advisory; block=opt-in WIRING_GATE_FAIL.
    wiring_gate_mode: Literal["warn", "block"] = "warn"
    # PRD-CORE-231-FR04: per-PRD-category override consulted BEFORE the global
    # mode, so `block` can be piloted on one category (e.g. {"CORE": "block"})
    # instead of flipping the whole catalogue at once. Keys are compared
    # case-insensitively; empty dict == exact pre-FR04 behavior.
    wiring_gate_mode_overrides: dict[str, Literal["warn", "block"]] = Field(default_factory=dict)

    risk_scaling_enabled: bool = True
    phase_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    prd_min_content_density: float = 0.30
    prd_required_status_for_implement: str = "approved"
    prds_relative_path: str = "docs/requirements-aare-f/prds"
    # Sibling repo roots for multi-repo workspaces: resolves PRD key-file refs
    # into sibling code repos instead of false repo_path_exists errors. Empty
    # by default (single-repo). Absolute or relative to the project root.
    additional_repo_roots: list[str] = Field(default_factory=list)
    index_auto_sync_on_status_change: bool = True
    strict_input_criteria: bool = False

    # The five grooming_* and three findings_* fields were removed 2026-07-28
    # (PRD-QUAL-131-FR01): no production reader, and no grooming or findings
    # subsystem for them to configure. ``finding_dedup_threshold`` (singular) is
    # a separate field and is retained -- it is unread too, but it is not part of
    # a whole-cluster removal and is triaged on its own evidence.
    finding_dedup_threshold: float = 0.6

    reflect_sequence_lookback: int = 3
    reflect_max_positive_learnings: int = 5
    reflect_max_success_patterns: int = 5
    reflect_q_value_threshold: float = 0.6

    reversion_rate_elevated: float = 0.15
    reversion_rate_concerning: float = 0.30

    # The eleven debt_* fields were removed 2026-07-28 (PRD-QUAL-131-FR01).
    # A repository-wide search for technical_debt, DebtRegistry, debt_registry
    # and TechDebt across trw-mcp/src/trw_mcp returned hits in exactly two files
    # -- this declaration and its admission registry -- with `nudge` (85 files)
    # as the non-vacuity control. There was no debt subsystem for them to
    # configure; the whole cluster described a feature that does not exist.

    compliance_strictness: Literal["strict", "lenient", "off"] = "lenient"
    compliance_long_session_event_threshold: int = 5
    compliance_pass_threshold: float = 0.8
    compliance_warning_threshold: float = 0.5
    compliance_dir: str = "compliance"
    compliance_history_file: str = "history.jsonl"
    compliance_changelog_filename: str = "CHANGELOG.md"
    # PRD-LOCAL-049 FR03: package-changelog advisory (opt-in). Default OFF —
    # the session changelog (FR01) always writes; this only warns when source
    # changed without a CHANGELOG.md update. Never fails delivery.
    changelog_advisory_enabled: bool = False
    # PRD-CORE-201-NFR04: up-front REVIEW-mandatory advisory for STANDARD/
    # COMPREHENSIVE runs. Default ON, advisory-only (never gates delivery).
    review_mandate_advisory_enabled: bool = True
    commit_fr_trailer_enabled: bool = True
    # sprint_integration_branch_pattern removed 2026-07-28 (PRD-QUAL-131-FR01)
    # with the rest of the sprint_* cluster. Its only reference outside this
    # file was a test asserting its default, which is not a consumer.
    compliance_review_retention_days: int = 365
    provenance_enabled: bool = True
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)

    atdd_enabled: bool = True
    test_skeleton_dir: str = ""
    completion_hooks_blocking: bool = False

    self_review_blocking: bool = False
    enforcement_variant: str = "baseline"
    incremental_validation_enabled: bool = True
    security_check_enabled: bool = True
    compact_instructions_template: str = ""
    pause_after_compaction: bool = False

    ceremony_alert_threshold: int = 40
    ceremony_alert_consecutive: int = 3
    ceremony_feedback_min_samples: int = 10
    ceremony_feedback_score_threshold: float = 80.0
    ceremony_feedback_quality_threshold: float = 0.9
    ceremony_feedback_escalation_threshold: float = 60.0
    ceremony_feedback_escalation_window: int = 5

    semantic_checks_enabled: bool = True
    assertion_failure_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    assertion_stale_threshold_days: int = Field(default=30, ge=1)
    observation_masking: bool = True
    compact_after_turns: int = 10
    minimal_after_turns: int = 30

    migration_gate_enabled: bool = True
    dry_check_enabled: bool = True
    dry_check_min_block_size: int = 5
    max_audit_cycles: int = Field(default=3, ge=1, le=10, description="Maximum audit cycles before escalation")
    audit_pattern_promotion_threshold: int = Field(
        default=3, ge=1, le=20, description="Minimum distinct PRDs for audit pattern promotion"
    )

    hooks_enabled: bool | None = None
    framework_md_enabled: bool | None = None
    skills_enabled: bool | None = None
    agents_enabled: bool | None = None
