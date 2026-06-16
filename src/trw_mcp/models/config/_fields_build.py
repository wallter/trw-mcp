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

from pydantic import Field

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
    # PRD-CORE-184-FR03: task-type-aware deliver gate mode.
    #   advisory     — warn but allow delivery
    #   block_coding — block missing-build-check delivery ONLY for coding/rca/eval
    #                  task types; advisory for docs/research/planning/unknown
    #   block_all    — block for every task type that expects a build artifact
    #                  (excludes docs/research/planning)
    # Default flipped advisory -> block_coding (2026-06-10, framework-canon
    # refinement): deliver-without-build-evidence is the dominant measured
    # false-completion mode (iter-28: universal miss-verification), and the
    # gate is safe by construction — docs/research/planning/unknown task types
    # never block, ceremony-only runs (no work events) never block, and the
    # ``allow_unverified`` + ``unverified_reason`` override path always
    # remains open. Set ``deliver_gate_mode: advisory`` in .trw/config.yaml to
    # restore the old warn-only posture.
    deliver_gate_mode: Literal["advisory", "block_coding", "block_all"] = "block_coding"
    # Optional per-task-type override map, e.g. {"eval": "advisory"}. Empty by
    # default; values must be one of the three modes above.
    deliver_gate_task_type_overrides: dict[str, str] = Field(default_factory=dict)
    # PRD-CORE-192-FR01: review_gate_mode (warn | block). When a STANDARD /
    #   COMPREHENSIVE run reaches deliver with no recorded trw_review, ``warn``
    #   (the brownfield-safe default) emits a soft ``review_warning`` and lets
    #   delivery proceed; ``block`` escalates that to a hard ``review_block`` so
    #   trw_deliver refuses to ship until a review exists (overridable via the
    #   ``allow_unverified`` + structured acceptable-failure path). Mirrors the
    #   ``deliver_gate_mode`` precedent. Default ``warn`` -> zero behavior change.
    review_gate_mode: Literal["warn", "block"] = "warn"
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"
    build_check_pytest_cmd: str | None = None
    # PRD-FIX-077-FR05: freshness window (seconds) for ceremony-state fallback
    # in the deliver-gate hook. Bounded 60..86400 at hook parse time.
    build_freshness_window_secs: int = 1800

    # -- Run maintenance --

    run_auto_close_enabled: bool = True
    run_auto_close_age_days: int = 7
    run_stale_ttl_hours: int = 48

    # -- Auto-checkpoint, auto-recall, auto-prune --

    auto_checkpoint_enabled: bool = True
    auto_checkpoint_tool_interval: int = 25
    auto_checkpoint_pre_compact: bool = True
    auto_recall_enabled: bool = True
    auto_recall_max_results: int = 3
    auto_recall_max_tokens: int = 100
    auto_recall_min_score: float = 0.7
    learning_auto_prune_on_deliver: bool = True
    learning_auto_prune_cap: int = 150
    # Floor between consecutive auto_prune runs. The full pass walks every
    # active YAML entry and runs O(N^2) Jaccard dedup, which is wall-clock
    # expensive at 1k+ entries and held the SQLite writer lock for many
    # minutes per pass before this throttle. Set to 0 to disable throttling.
    learning_auto_prune_min_interval_hours: int = 24
    # Hard wall-clock budget for a single auto_prune pass. When the deadline
    # fires, the pass returns the partial removal it has computed so far and
    # records ``status=deadline_exceeded`` for observability.
    learning_auto_prune_max_seconds: int = 30

    # -- Deferred-delivery batch budgets --
    #
    # The deferred-delivery worker runs ~13 maintenance steps after each
    # trw_deliver. Without budgets, a single slow step (auto_prune,
    # publish_learnings network hang) can wedge the worker for hours,
    # blocking every subsequent trw_learn that needs the SQLite writer
    # lock. The watchdog enforces per-step and per-batch deadlines; on
    # overrun it flips a cancellation event, logs the runaway step, and
    # releases the deliver-deferred file lock so the next batch can run.
    deferred_step_max_seconds: int = 60
    deferred_batch_max_seconds: int = 300
    deferred_lock_stale_seconds: int = 600

    # -- Quality gates (mutation, cross-model, multi-agent, API fuzz) --

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
    # PRD-QUAL-110-FR03: the dependency-audit config flags were removed — they
    # advertised a gate with NO implementation anywhere in the package source
    # (the only references were dead, non-collecting test files). TRWConfig sets
    # ``extra="ignore"``, so an old config that still carries the removed key
    # loads gracefully rather than erroring (RISK-003).
    comment_check_enabled: bool = True
    api_fuzz_enabled: bool = False
    api_fuzz_base_url: str = "http://localhost:8000"
    api_fuzz_level: str = "strict"
    api_fuzz_timeout_secs: int = 120

    # -- LLM augmentation --

    llm_enabled: bool = True
    llm_default_model: str = "haiku"
