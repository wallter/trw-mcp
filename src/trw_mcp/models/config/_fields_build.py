"""Build verification, mutation testing, and quality gate fields.

Covers sections 29-32, 41-45 of the original _main_fields.py:
  - Adaptive gates
  - Code simplifier
  - Build verification
  - Run maintenance
  - Quality gates (mutation, cross-model, dep audit, API fuzz)
  - Auto-checkpoint, auto-recall, auto-prune
  - LLM augmentation
"""

from __future__ import annotations

from typing import Literal

from trw_mcp.models.config._defaults import (
    DEFAULT_BUILD_CHECK_TIMEOUT_SECS,
    DEFAULT_MUTATION_TIMEOUT_SECS,
)


class _BuildFields:
    """Build domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Adaptive gates --

    gate_default_type: str = "FULL"
    gate_strategy: str = "hybrid"
    gate_early_stop_confidence: float = 0.85
    gate_max_rounds: int = 5
    gate_convergence_epsilon: float = 0.05
    gate_escalation_enabled: bool = True
    gate_max_total_judges: int = 13
    gate_tokens_per_vote: int = 2000
    gate_debate_context_multiplier: float = 1.5
    gate_critic_overhead_multiplier: float = 2.0
    gate_tokens_per_1k_chars: int = 500
    gate_architecture_score_penalty: float = 0.1

    # -- Code simplifier --

    auto_simplify_enabled: bool = False
    simplifier_wave_size: int = 10
    sprint_code_simplifier_wave_size: int = 10
    sprint_commit_pattern: str = "feat(sprint{num}): Track {track}"
    simplifier_verification_timeout_secs: int = 120
    simplifier_backup_dir: str = ".trw/simplifier-backups"

    # -- Build verification --

    build_check_enabled: bool = True
    build_check_timeout_secs: int = DEFAULT_BUILD_CHECK_TIMEOUT_SECS
    build_check_coverage_min: float = 85.0
    build_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"
    build_check_pytest_cmd: str | None = None

    # -- Run maintenance --

    run_auto_close_enabled: bool = True
    run_auto_close_age_days: int = 7
    run_stale_ttl_hours: int = 48

    # -- Auto-checkpoint, auto-recall, auto-prune --

    auto_checkpoint_enabled: bool = True
    auto_checkpoint_tool_interval: int = 25
    auto_checkpoint_pre_compact: bool = True
    auto_recall_enabled: bool = True
    auto_recall_max_results: int = 5
    learning_auto_prune_on_deliver: bool = True
    learning_auto_prune_cap: int = 150

    # -- Quality gates (mutation, cross-model, multi-agent, dep audit, API fuzz) --

    mutation_enabled: bool = False
    mutation_threshold: float = 0.50
    mutation_threshold_critical: float = 0.70
    mutation_threshold_experimental: float = 0.30
    mutation_critical_paths: tuple[str, ...] = ("tools/", "state/", "models/")
    mutation_experimental_paths: tuple[str, ...] = ("scratch/",)
    mutation_timeout_secs: int = DEFAULT_MUTATION_TIMEOUT_SECS
    cross_model_review_enabled: bool = False
    cross_model_provider: str = "gemini-2.5-pro"
    cross_model_review_timeout_secs: int = 30
    cross_model_review_block_on_critical: bool = True
    review_confidence_threshold: int = 80
    dep_audit_enabled: bool = True
    dep_audit_level: str = "high"
    dep_audit_timeout_secs: int = 30
    dep_audit_block_on_patchable_only: bool = True
    comment_check_enabled: bool = True
    api_fuzz_enabled: bool = False
    api_fuzz_base_url: str = "http://localhost:8000"
    api_fuzz_level: str = "strict"
    api_fuzz_timeout_secs: int = 120

    # -- LLM augmentation --

    llm_enabled: bool = True
    llm_default_model: str = "haiku"
