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
    # PRD-CORE-241-FR07 retired "learning_injection": the measured difference did
    # not reach significance. "contextual" supersedes it — do not reinstate.
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

    framework_version: str = "v27.4_TRW"
    aaref_version: str = "v3.2.1"

    ambiguity_rate_max: float = 0.05
    completeness_min: float = 0.85
    traceability_coverage_min: float = 0.90
    validation_density_weight: float = 20.0
    validation_structure_weight: float = 20.0
    validation_implementation_readiness_weight: float = 25.0
    validation_traceability_weight: float = 35.0
    # consistency_validation_min, validation_smell_weight,
    # validation_readability_weight, and validation_ears_weight were removed
    # under PRD-CORE-291 (slice 2): no production reader outside this module
    # and no originating PRD kept them advisory-live (validation_smell_weight
    # and validation_ears_weight were already documented as permanently-0
    # advisory tunables the scorer never multiplies by).
    density_weight_problem_statement: float = Field(default=2.0, ge=0.0, le=10.0)
    density_weight_functional_requirements: float = Field(default=2.0, ge=0.0, le=10.0)
    density_weight_traceability_matrix: float = Field(default=1.5, ge=0.0, le=10.0)
    density_weight_default: float = Field(default=1.0, ge=0.0, le=10.0)
    validation_skeleton_threshold: float = 30.0
    validation_draft_threshold: float = 60.0
    validation_review_threshold: float = 85.0
    # validation_fk_optimal_min/_max (the Flesch-Kincaid "optimal" band) were
    # removed 2026-09-16 (PRD-QUAL-139-FR05): no consumer under the corrected
    # scan, no originating PRD, no test. The readability scorer never consulted
    # a band. Retired keys are listed in trw_mcp/data/config-retired-keys.json.
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
    # subsystem for them to configure. ``finding_dedup_threshold`` (singular)
    # was held back then as "triaged on its own evidence"; that triage finished
    # on 2026-09-16 with the same answer, and it left with
    # reflect_sequence_lookback and reflect_q_value_threshold under
    # PRD-QUAL-139-FR05. reflect_max_positive_learnings followed the same path
    # under PRD-CORE-291 (slice 2): its originating PRD-FIX-021 is done and
    # nothing reads it, so the delivered-requirement carve-out no longer
    # applies -- removed.

    reflect_max_success_patterns: int = 5

    reversion_rate_elevated: float = 0.15
    reversion_rate_concerning: float = 0.30

    # The eleven debt_* fields were removed 2026-07-28 (PRD-QUAL-131-FR01).
    # A repository-wide search for technical_debt, DebtRegistry, debt_registry
    # and TechDebt across trw-mcp/src/trw_mcp returned hits in exactly two files
    # -- this declaration and its admission registry -- with `nudge` (85 files)
    # as the non-vacuity control. There was no debt subsystem for them to
    # configure; the whole cluster described a feature that does not exist.

    # compliance_long_session_event_threshold, compliance_warning_threshold and
    # compliance_history_file were removed 2026-09-16 (PRD-QUAL-139-FR05); their
    # siblings compliance_strictness and compliance_pass_threshold (PRD-INFRA-027
    # and PRD-CORE-060, both done) followed under PRD-CORE-291 (slice 2): no
    # consumer under the corrected scan, no test beyond a default pin.
    compliance_dir: str = "compliance"
    compliance_changelog_filename: str = "CHANGELOG.md"
    # PRD-LOCAL-049 FR03: package-changelog advisory (opt-in). Default OFF —
    # the session changelog (FR01) always writes; this only warns when source
    # changed without a CHANGELOG.md update. Never fails delivery.
    changelog_advisory_enabled: bool = False
    # PRD-CORE-201-NFR04: up-front REVIEW-mandatory advisory for STANDARD/
    # COMPREHENSIVE runs. Default ON, advisory-only (never gates delivery).
    review_mandate_advisory_enabled: bool = True
    # sprint_integration_branch_pattern removed 2026-07-28 (PRD-QUAL-131-FR01)
    # with the rest of the sprint_* cluster. Its only reference outside this
    # file was a test asserting its default, which is not a consumer.
    # commit_fr_trailer_enabled, provenance_enabled, confidence_threshold,
    # atdd_enabled, test_skeleton_dir, completion_hooks_blocking,
    # incremental_validation_enabled, security_check_enabled,
    # pause_after_compaction, ceremony_alert_threshold, and
    # ceremony_alert_consecutive were removed under PRD-CORE-291 (slice 2) for
    # the same reason: no production reader, only a test pinning a default.
    compliance_review_retention_days: int = 365

    # self_review_blocking KEPT under PRD-CORE-291 (slice 2) despite scanning
    # unread: `.claude/hooks/self-review.sh:21-22,133` reads this key straight
    # out of `.trw/config.yaml` (not an env-var twin), a real consumer outside
    # the scanner's corpus (trw-mcp/src/trw_mcp/data/hooks only) -- class E,
    # `.trw/compliance/config-field-consumers-baseline.json`.
    self_review_blocking: bool = False
    compact_instructions_template: str = ""

    ceremony_feedback_min_samples: int = 10
    ceremony_feedback_score_threshold: float = 80.0
    ceremony_feedback_quality_threshold: float = 0.9
    ceremony_feedback_escalation_threshold: float = 60.0
    ceremony_feedback_escalation_window: int = 5

    semantic_checks_enabled: bool = True
    assertion_failure_penalty: float = Field(default=0.15, ge=0.0, le=1.0)
    assertion_stale_threshold_days: int = Field(default=30, ge=1)

    migration_gate_enabled: bool = True
    dry_check_enabled: bool = True
    dry_check_min_block_size: int = 5
    # max_audit_cycles removed under PRD-CORE-291 (slice 2): no production
    # reader, only a bundled skill doc restating its default.

    # The one switch for every bundled hook. Hooks cannot walk this cascade, so
    # the resolved value is published to .trw/runtime/hook-flags (state/_hook_flags.py)
    # and lib-trw.sh reads only that file. Exempt by design: the intent-contract
    # write guards stay on when this is false. They are security enforcement, not
    # ablation surface (lead ruling 2026-09-23).
    hooks_enabled: bool = True
    framework_md_enabled: bool | None = None
    skills_enabled: bool | None = None
    agents_enabled: bool | None = None
