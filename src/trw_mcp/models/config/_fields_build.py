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

    # The "Adaptive gates" (gate_*) and "Code simplifier" (simplifier_* /
    # sprint_*) sections were removed 2026-07-28 (PRD-QUAL-131-FR01): 8 typed,
    # documented, user-settable fields with no production reader anywhere in
    # trw_mcp, and no adaptive-gate or code-simplifier subsystem for them to
    # configure. See trw-mcp/CHANGELOG.md for the full removed-key list.

    # -- Build verification --

    build_check_enabled: bool = True
    build_check_timeout_secs: int = DEFAULT_BUILD_CHECK_TIMEOUT_SECS
    build_check_coverage_min: float = 85.0
    build_gate_enforcement: Literal["strict", "lenient", "off"] = "lenient"
    # PRD-CORE-184-FR03 + PRD-CORE-246-FR03: evidence-keyed deliver gate mode.
    #   advisory     — warn but allow delivery
    #   block_coding — block missing-build-check delivery when the task type
    #                  expects a build artifact (coding/rca/eval) OR when the
    #                  session recorded at least
    #                  ``deliver_gate_unclassified_change_threshold`` distinct
    #                  modified files, whatever the task type
    #   block_all    — same predicate as block_coding
    # Default flipped advisory -> block_coding (2026-06-10, framework-canon
    # refinement): deliver-without-build-evidence is the dominant measured
    # false-completion mode observed in evaluation. CORE-246
    # then removed the never-block-on-unknown branch: the gate's strength no
    # longer depends on the task-type heuristic being right, because a run that
    # modified files blocks whatever it was classified as. Ceremony-only runs
    # (no work events, no file modifications) still never block, and the
    # ``allow_unverified`` + ``unverified_reason`` override path always remains
    # open. Set ``deliver_gate_mode: advisory`` in .trw/config.yaml to restore
    # the warn-only posture.
    deliver_gate_mode: Literal["advisory", "block_coding", "block_all"] = "block_coding"
    # Optional per-task-type override map, e.g. {"eval": "advisory"}. Empty by
    # default; values must be one of the three modes above.
    deliver_gate_task_type_overrides: dict[str, str] = Field(default_factory=dict)
    # PRD-CORE-246-FR03/NFR03: how many DISTINCT files the current session must
    # have modified before a missing build check blocks a task type that does
    # not inherently expect a build artifact. The comparison is ``>=``, so the
    # default of 1 means "any recorded file modification arms the gate". Bounded
    # (never silently clamped): 0 and >1000 are rejected at config load. This is
    # a threshold, NOT an on/off switch — the task-type clause is an OR, so no
    # value restores the pre-CORE-246 never-block-on-unknown behavior.
    deliver_gate_unclassified_change_threshold: int = Field(default=1, ge=1, le=1000)
    # PRD-CORE-192-FR01: review_gate_mode (warn | block). When a STANDARD /
    #   COMPREHENSIVE run reaches deliver with no recorded trw_review, ``warn``
    #   (the brownfield-safe default) emits a soft ``review_warning`` and lets
    #   delivery proceed; ``block`` escalates that to a hard ``review_block`` so
    #   trw_deliver refuses to ship until a review exists (overridable via the
    #   ``allow_unverified`` + structured acceptable-failure path). Mirrors the
    #   ``deliver_gate_mode`` precedent. Default ``warn`` -> zero behavior change.
    review_gate_mode: Literal["warn", "block"] = "warn"
    # PRD-CORE-213-NFR01: acceptance-integrity transition gate mode (warn | block).
    #   When a session's path-limited PRD diff moves a PRD to ``status:
    #   implemented`` under a build-bearing task type (coding/rca/eval) in
    #   ``deliver_gate_mode=block_coding``/``block_all``:
    #     warn  (brownfield-safe default) — detect the transition and, if it is
    #           incoherent (missing functionality_level coherence / wiring /
    #           build evidence / independent P0/P1 review receipt), log
    #           ``acceptance_integrity_warn`` but let delivery proceed.
    #     block — escalate an incoherent transition to a hard
    #           ``acceptance_integrity_block`` (overridable ONLY via the
    #           ``allow_unverified`` + structured acceptable-failure record path,
    #           identical to the build gate).
    #   Default ``block`` (PRD-QUAL-119 P09 activation, 2026-07-11): an
    #   uncertifiable ``->implemented`` transition hard-blocks delivery by
    #   default; the structured acceptable-failure record remains the only
    #   escape. The ``warn`` posture stays available as an explicit opt-DOWN
    #   during brownfield remediation, but the shipped default enforces
    #   completion truth (rollout observation is not completion).
    prd_transition_gate: Literal["warn", "block"] = "block"
    # PRD-CORE-205-FR08: content-bound evidence receipt compatibility mode.
    #   observe — new writers dual-write typed receipts AND legacy projections;
    #             readers prefer receipts; a legacy artifact keeps its existing
    #             gate behavior as ``legacy_unbound`` ONLY when NO typed receipt
    #             exists. Typed-present-invalid evidence is non-positive and never
    #             falls back to a legacy positive path.
    #   enforce — receipt-required positive evidence for build-bearing work; a
    #             legacy-unbound artifact is treated as missing.
    # v26.1 closes the in-repo compatibility window: typed review/build writers
    # are live and the safe default is enforce. Projects with a documented
    # external migration may temporarily opt back into observe.
    # An unknown value is treated as non-positive by readers (fail-toward-no-evidence).
    evidence_receipt_mode: Literal["observe", "enforce"] = "enforce"
    # PRD-CORE-255-FR01: hours a typed ReviewReceipt stays positive evidence, from
    # completed_at; independent of the CORE-205 binding, fail-closed when unreadable.
    review_verdict_ttl_hours: int = Field(default=24, ge=1, le=8760)
    build_check_pytest_args: str = ""
    build_check_mypy_args: str = "--strict"
    build_check_pytest_cmd: str | None = None
    # PRD-FIX-077-FR05: freshness window (seconds) for ceremony-state fallback
    # in the deliver-gate hook. Bounded 60..86400 at hook parse time.
    build_freshness_window_secs: int = 1800

    # -- Run maintenance --

    run_auto_close_enabled: bool = True
    run_stale_ttl_hours: int = 48

    # -- Auto-checkpoint, auto-recall, auto-prune --

    auto_checkpoint_enabled: bool = True
    auto_checkpoint_tool_interval: int = 25
    auto_checkpoint_pre_compact: bool = True
    auto_recall_enabled: bool = True
    auto_recall_max_results: int = 3
    auto_recall_max_tokens: int = 100
    # PRD-FIX-124-FR06: the minimum relevance a stored learning must reach for
    # the UserPromptSubmit hook to inject it. This is an IDF-WEIGHTED PROMPT-
    # COVERAGE FRACTION, not a probability: the share of the prompt's keyword
    # mass that appears in the learning's own summary+tags token set. The
    # previous 0.7 was picked as if it were a probability and proved
    # unreachable (2/20 in-domain firings). Calibrated to 0.35 against this
    # repo's live store: 13/20 in-domain, 0/10 off-domain, with 0.08 of margin
    # over the highest off-domain score observed. See
    # docs/documentation/operational-knowledge/auto-recall-calibration.md.
    auto_recall_min_score: float = Field(default=0.35, ge=0.0, le=1.0)
    # PRD-FIX-124-FR07: how many learning entries one auto-recall scan reads,
    # most-recently-modified first. The former hard-coded 500 covered 7.8% of a
    # 6,436-entry store and excluded the rest by AGE rather than irrelevance.
    # 10000 covers a store that size whole in ~190ms against the hook's 500ms
    # deadline; the ordering survives only as the tie-break for a larger store.
    auto_recall_scan_cap: int = Field(default=10000, ge=1)
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
    # PRD-CORE-270-FR02: dispatch CLIENT id, not a model. "" = no reviewer.
    cross_model_provider: str = ""
    cross_model_review_timeout_secs: int = 30
    review_confidence_threshold: int = 80
    # PRD-QUAL-110-FR03: the dependency-audit config flags were removed — they
    # advertised a gate with NO implementation anywhere in the package source
    # (the only references were dead, non-collecting test files). TRWConfig sets
    # ``extra="ignore"``, so an old config that still carries the removed key
    # loads gracefully rather than erroring (RISK-003).
    api_fuzz_base_url: str = "http://localhost:8000"
    api_fuzz_level: str = "strict"
    api_fuzz_timeout_secs: int = 120

    # -- LLM augmentation --

    llm_default_model: str = "haiku"
