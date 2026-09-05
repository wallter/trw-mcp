"""Explicit configuration-admission records (PRD-CORE-218-FR05 registry).

Belongs to the ``_field_admission.py`` facade, which re-exports every symbol
here. Split out because the registry is a DATA TABLE that grows once per new
public config field, while its parent holds the admission CONTRACT (the frozen
legacy census plus the build/verify API) and changes almost never — keeping them
in one file made the contract drift past the module-size gate every time a field
was admitted.

Imports nothing from the rest of ``trw_mcp.models.config``, so neither this
module nor its parent can create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_auto_recall import AUTO_RECALL_ADMISSIONS
from trw_mcp.models.config._field_admission_degenerate_result import DEGENERATE_RESULT_ADMISSIONS
from trw_mcp.models.config._field_admission_degraded_mode import DEGRADED_MODE_ADMISSIONS
from trw_mcp.models.config._field_admission_drain_budget import DRAIN_BUDGET_ADMISSIONS
from trw_mcp.models.config._field_admission_formation import FORMATION_ADMISSIONS
from trw_mcp.models.config._field_admission_formation_readiness import FORMATION_READINESS_ADMISSIONS
from trw_mcp.models.config._field_admission_instruction_writes import INSTRUCTION_WRITE_ADMISSIONS
from trw_mcp.models.config._field_admission_memory_truth import MEMORY_TRUTH_ADMISSIONS
from trw_mcp.models.config._field_admission_project_handoff import PROJECT_HANDOFF_ADMISSIONS
from trw_mcp.models.config._field_admission_registry_types import (
    BudgetDecision as BudgetDecision,
)
from trw_mcp.models.config._field_admission_registry_types import (
    ConfigAdmission as ConfigAdmission,
)
from trw_mcp.models.config._field_admission_review_verdict import REVIEW_VERDICT_ADMISSIONS
from trw_mcp.models.config._field_admission_surface_role import SURFACE_ROLE_ADMISSIONS
from trw_mcp.models.config._field_admission_wal_checkpoint import WAL_CHECKPOINT_ADMISSIONS
from trw_mcp.models.config._field_admission_writer_pressure import WRITER_PRESSURE_ADMISSIONS

#: Explicit full-metadata admissions for public fields added by PRD-CORE-218
#: itself (or later). Every current field outside the frozen legacy baseline
#: MUST have a complete :class:`ConfigAdmission` entry here or the gate rejects
#: it. ``tool_resolution_mode`` is FR04's standard/all selector — the SOLE tool
#: exposure authority (the legacy CORE-125 tool_exposure_mode/list fields and
#: their TOOL_PRESETS vocabulary were removed at activation).
FIELD_ADMISSIONS: dict[str, ConfigAdmission] = {
    "profile_domain_path_map": ConfigAdmission(
        field_name="profile_domain_path_map",
        owner="PRD-HPO-PROF-001-FR-6",
        consumer="trw_mcp.profile.session_resolve.resolve_session_profile -> trw_mcp.profile.inference.infer_domain",
        default_rationale=(
            "Defaults to a small table of GENERIC source-layout conventions "
            "(frontend/web/ui -> frontend, api/server -> backend, eval/evals -> eval). The prior "
            "table was a code constant naming one specific repository's package tree, which made "
            "domain inference silently wrong for every other project that installs this package. "
            "A layout is a property of the consuming project, so it is stated by the project."
        ),
        interaction_analysis=(
            "Read only on the FR-6 inference branch, i.e. when trw_session_start resolves a profile "
            "and no explicit domain was supplied; an explicit domain still wins. The resolved value "
            "selects which .trw/profiles/domain-*.yaml layer discover_layers loads, so a mapping "
            "naming a domain with no layer file simply contributes nothing (fail-open). Setting the "
            "field REPLACES the defaults rather than merging, and matching is longest-prefix-wins so "
            "the result does not depend on YAML key order."
        ),
        deprecation_plan=(
            "Retain; removing it would return a project-specific directory table to shipped code, "
            "which is the defect this field exists to make impossible."
        ),
        docs_pointer="docs/requirements-aare-f/prds/agentic-hpo/PRD-HPO-PROF-001-profile-system.md",
        test_pointer="trw-mcp/tests/unit/profile/test_inference.py::test_infer_domain_uses_config_supplied_map",
        budget_decision="admitted",
    ),
    "tool_resolution_mode": ConfigAdmission(
        field_name="tool_resolution_mode",
        owner="PRD-CORE-218-FR04",
        consumer="trw_mcp.middleware.surface_authority (SurfaceAuthorityMiddleware) via resolve_tool_surface",
        default_rationale=(
            "Defaults to 'standard' so the bounded kernel+task-pack surface is the default; "
            "'all' is an explicit operator selection (FR04)."
        ),
        interaction_analysis=(
            "Two-valued selector consumed by the manifest tool-surface resolver and enforced by "
            "SurfaceAuthorityMiddleware; 'all' remains subject to phase-exposure/policy/authorization "
            "(NFR02). It is now the sole exposure authority — the legacy tool_exposure_mode was removed."
        ),
        deprecation_plan="Retain; the sole tool-exposure authority after the CORE-125 preset filter removal.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-218.md#prd-core-218-fr04",
        test_pointer="trw-mcp/tests/test_tool_presets.py::test_prd_core_218_fr04",
        budget_decision="admitted",
    ),
    "telemetry_log_max_bytes": ConfigAdmission(
        field_name="telemetry_log_max_bytes",
        owner="PRD-CORE-181-FR04",
        consumer="trw_mcp.telemetry.publisher.rotate_pipeline_telemetry_log",
        default_rationale=(
            "Defaults to 10 MiB (10485760), matching the per-JSONL rotation threshold used "
            "elsewhere in state/; promotes the former TRW_TELEMETRY_LOG_MAX_BYTES env-only knob to a "
            "typed field so the bound is validated (gt=0) and configurable via config.yaml."
        ),
        interaction_analysis=(
            "Single-purpose size threshold read only by the deliver-step telemetry-log rotation "
            "maintenance path; no interaction with other config fields. The TRW_TELEMETRY_LOG_MAX_BYTES "
            "env var still overrides it via BaseSettings env_prefix precedence."
        ),
        deprecation_plan="Retain; the sole FR04 rotation knob. Env override folds in via BaseSettings.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-181.md",
        test_pointer=(
            "trw-mcp/tests/test_jsonl_rotation_parity.py::test_telemetry_log_default_threshold_reads_config_field"
        ),
        budget_decision="admitted",
    ),
    "maintain_verify_batch_limit": ConfigAdmission(
        field_name="maintain_verify_batch_limit",
        owner="PRD-CORE-231-FR02",
        consumer="trw_mcp.tools._maintain_verify.run_maintain_verify",
        default_rationale=(
            "Defaults to 1000, the entry count PRD-CORE-231-NFR01 budgets at <30s for the bulk "
            "assertion sweep; the cap keeps a single scheduled run bounded on a large store."
        ),
        interaction_analysis=(
            "Read only by the maintain-verify sweep as the `limit` on one "
            "SQLiteBackend.entries_with_assertions() bulk fetch. Independent of "
            "assertion_stale_threshold_days (which decides the verdict, not how many rows are scanned)."
        ),
        deprecation_plan="Retain; the sole sweep-size knob. Removing it would reintroduce a magic number.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_assertion_sweep_performance.py::test_bulk_sweep_1000_entries",
        budget_decision="admitted",
    ),
    "hint_sidecar_refresh_enabled": ConfigAdmission(
        field_name="hint_sidecar_refresh_enabled",
        owner="PRD-CORE-231-FR01",
        consumer="trw_mcp.tools._hint_sidecar_refresh.resolve_refresh_plan (data/git_hooks/trw-post-commit.sh)",
        default_rationale=(
            "Defaults True so the T2 tier is live rather than dormant; flipping it False is the "
            "FR01 rollback path (\u00a79), which degrades trw_before_edit_hint to its existing T1/T0 behavior."
        ),
        interaction_analysis=(
            "Master gate read before hint_sidecar_refresh_file_cap; when False the refresh is a no-op and "
            "the cap is never consulted. Independent of the entitlement check, which still fails open."
        ),
        deprecation_plan="Retain while the T2 tier ships; removal requires the FR01 dormant-tier decision.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_post_commit_sidecar_refresh.py::test_disabled_flag_is_a_no_op",
        budget_decision="admitted",
    ),
    "hint_sidecar_refresh_file_cap": ConfigAdmission(
        field_name="hint_sidecar_refresh_file_cap",
        owner="PRD-CORE-231-FR01",
        consumer="trw_mcp.tools._hint_sidecar_refresh.resolve_refresh_plan (data/git_hooks/trw-post-commit.sh)",
        default_rationale=(
            "Defaults to 20 changed files per commit (NFR01) so a large commit cannot stall git commit; "
            "typed rather than hardcoded because the right cap depends on a repo's file-edit fan-out."
        ),
        interaction_analysis=(
            "Bounds the per-commit subprocess fan-out only; gated behind hint_sidecar_refresh_enabled. "
            "A cap lower than a commit's changed-file count reduces the share of eligible edits that "
            "receive a T2 hint, which is the intended latency/coverage trade-off (OQ-06). The typed "
            "delivery-rate pair that once named this trade-off was deleted by PRD-FIX-125-FR04: "
            "neither field had a reader and both admission grandfathers expired 2026-08-31."
        ),
        deprecation_plan="Retain; removing it would reintroduce an unbounded per-commit fan-out.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_post_commit_sidecar_refresh.py::test_file_cap_bounds_the_plan",
        budget_decision="admitted",
    ),
    "wiring_gate_mode_overrides": ConfigAdmission(
        field_name="wiring_gate_mode_overrides",
        owner="PRD-CORE-231-FR04",
        consumer="trw_mcp.state.validation._prd_quality_refresh._resolve_wiring_mode (trw_prd_validate)",
        default_rationale=(
            "Defaults to an EMPTY dict, which reproduces pre-FR04 behavior byte-for-byte: with no "
            "entries every PRD keeps using the global wiring_gate_mode. Populating it is the explicit "
            "operator act that starts a scoped block-mode pilot."
        ),
        interaction_analysis=(
            "Consulted BEFORE the global wiring_gate_mode and only for a PRD whose frontmatter category "
            "matches a key (case-insensitively); an unlisted category falls through to the global mode. "
            "Follows the deliver_gate_task_type_overrides pattern. Clearing the dict is a config-only "
            "rollback of a pilot with no code change (\u00a79 Rollback Plan)."
        ),
        deprecation_plan=(
            "Retain as the scoping mechanism; it is removed only if the wiring gate is flipped to block "
            "repo-wide, which would make per-category scoping moot."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_wiring_gate_scoped_override.py::test_category_override_blocks",
        budget_decision="admitted",
    ),
    "learn_journal_enabled": ConfigAdmission(
        field_name="learn_journal_enabled",
        owner="learn-durability fix (SURFACE-CENSUS-2026-07-24 §6, specimen #6)",
        consumer="trw_mcp.tools._learn_journal_wiring (journal_accepted/consume_journal) + _ceremony_helpers._run_learn_journal_drain",
        default_rationale=(
            "Defaults True so an accepted trw_learn is durably journaled BEFORE the slow pre-store "
            "pipeline (active-set load + semantic dedup + embedding cold-start) that can exceed the "
            "client's 120s tool timeout; a session that exits mid-pipeline would otherwise lose the "
            "learning silently. False is the rollback to the legacy loss-prone path."
        ),
        interaction_analysis=(
            "Master gate for the write-ahead journal: when False neither the append (in execute_learn) "
            "nor the session_start drain (run_auto_maintenance) run, and learn_journal_drain_limit is "
            "never consulted. Independent of dedup_enabled — the journal replays through the same dedup, "
            "so exact-content/semantic dedup still collapses a replay against an existing row (exactly-once)."
        ),
        deprecation_plan="Retain as the durability kill switch; removal requires proving the loss window is closed by other means.",
        docs_pointer="trw-mcp/CHANGELOG.md — learn-journal durability + recovery entries",
        test_pointer="trw-mcp/tests/test_learn_journal.py::TestDurability::test_disabled_journal_writes_nothing",
        budget_decision="admitted",
    ),
    "learn_journal_drain_limit": ConfigAdmission(
        field_name="learn_journal_drain_limit",
        owner="learn-durability fix (SURFACE-CENSUS-2026-07-24 §6, specimen #6)",
        consumer="trw_mcp.state.learn_journal.drain_pending (via _ceremony_helpers._run_learn_journal_drain)",
        default_rationale=(
            "Defaults to 50 pending records replayed per session_start sweep so a large recovery backlog "
            "cannot stall boot; the remainder drains on the next sweep. Typed rather than hardcoded "
            "because the right bound depends on a store's write volume."
        ),
        interaction_analysis=(
            "Bounds the per-sweep replay fan-out only; gated behind learn_journal_enabled and skipped "
            "entirely under writer pressure (session_start deferral). No interaction with other fields."
        ),
        deprecation_plan="Retain; removing it would reintroduce an unbounded recovery loop on session_start.",
        docs_pointer="trw-mcp/CHANGELOG.md — learn-journal durability + recovery entries",
        test_pointer="trw-mcp/tests/test_learn_journal.py::TestJournalModule::test_drain_respects_limit_and_defers_remainder",
        budget_decision="admitted",
    ),
    "learn_journal_drain_min_batch": ConfigAdmission(
        field_name="learn_journal_drain_min_batch",
        owner="PRD-INFRA-171-FR06 (journal-drain liveness)",
        consumer="trw_mcp.state.learn_journal.pressure_drain_budget (via _ceremony_maintenance_steps._run_learn_journal_drain)",
        default_rationale=(
            "Defaults to 2 records replayed per sweep EVEN under writer pressure. Measured across 122 "
            "trw-mcp log files: 42 records journaled, 0 sweeps ever, because a single peer MCP writer "
            "deferred the drain permanently. A small floor makes progress inevitable while keeping the "
            "pressured sweep far below learn_journal_drain_limit (50), so the deferral's intent — do not "
            "fight a live writer for the backend — survives. 0 restores the pre-FR06 defer-always path."
        ),
        interaction_analysis=(
            "Clamped to learn_journal_drain_limit - 1, so it can never become a full sweep; raised only by "
            "learn_journal_pending_max_age_hours when an aged record would otherwise be stranded. Gated "
            "behind learn_journal_enabled and consulted ONLY on the pressured branch — with no peer writers "
            "the full-limit sweep runs exactly as before."
        ),
        deprecation_plan=(
            "Retain as the liveness floor; removal reinstates the measured 42-in/0-drained defect unless the "
            "pressure gate itself is re-based on a real-contention signal (PRD-INFRA-171 OQ-8)."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-INFRA-171.md",
        test_pointer=(
            "trw-mcp/tests/test_learn_journal_drain_liveness.py::TestMinimumProgressBatch::"
            "test_pending_drains_under_sustained_writer_pressure"
        ),
        budget_decision="admitted",
    ),
    "learn_journal_pending_max_age_hours": ConfigAdmission(
        field_name="learn_journal_pending_max_age_hours",
        owner="PRD-INFRA-171-FR06 (journal-drain liveness)",
        consumer="trw_mcp.state.learn_journal.aged_pending_count / pressure_drain_budget (via _ceremony_maintenance_steps._run_learn_journal_drain)",
        default_rationale=(
            "Defaults to 6 hours: a record at or past that age drains regardless of writer pressure, so a "
            "journaled learning always reaches the store within a bounded time instead of waiting for a "
            "quiet session_start that a busy fleet never produces. Typed in hours because the right bound "
            "is an operator durability decision (how long a learning may stay un-recallable), not a constant."
        ),
        interaction_analysis=(
            "Measured from the pending file's mtime and INCLUSIVE at the bound. Raises the pressured sweep "
            "budget above learn_journal_drain_min_batch only when aged records exist, and the result is still "
            "capped by learn_journal_drain_limit so a large backlog cannot make one sweep unbounded. Poison "
            "records (unknown version / corrupt) are excluded, so the hatch cannot spin on an unreplayable "
            "record. 0 disables the hatch, leaving only the minimum-progress floor."
        ),
        deprecation_plan=(
            "Retain as the eventual-drain guarantee; removal would make drain liveness depend on the fleet "
            "happening to go quiet."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-INFRA-171.md",
        test_pointer=(
            "trw-mcp/tests/test_learn_journal_drain_liveness.py::TestAgeEscapeHatch::"
            "test_aged_record_drains_under_pressure_with_zero_min_batch"
        ),
        budget_decision="admitted",
    ),
    "learn_journal_max_replay_attempts": ConfigAdmission(
        field_name="learn_journal_max_replay_attempts",
        owner="learn-journal drain accounting fix (adversarial audit I-2, 2026-07-25)",
        consumer="trw_mcp.state.learn_journal.drain_pending (via _ceremony_maintenance_steps._run_learn_journal_drain + server._subcommands_learn_drain)",
        default_rationale=(
            "Defaults to 5 replay attempts before a TRANSIENTLY failing record (backend unavailable, DB "
            "lock) is dead-lettered. The pre-fix answer was 'retry forever', which is why a record the "
            "accept gates refuse at replay time occupied active journal capacity permanently while the "
            "sweep booked it as recovered. 5 is high enough that a multi-session backend outage does not "
            "move a real learning aside, low enough that a stuck record cannot re-run on every sweep for "
            "the life of the project. Typed rather than hardcoded because the right budget depends on how "
            "long an operator's backend outages last. 0 disables the budget (retain indefinitely)."
        ),
        interaction_analysis=(
            "Bounds ONLY the transient branch. A deterministic refusal (accept-gate 'rejected'/'invalid' "
            "status, or a ValueError/TypeError from enum/schema validation) dead-letters on attempt 1 "
            "regardless of this value, so setting it to 0 cannot restore the infinite-retry defect for "
            "content the gates will always refuse. The attempt count is persisted onto the pending record "
            "with its ORIGINAL mtime preserved, so the budget survives restarts without disturbing "
            "learn_journal_pending_max_age_hours (the age hatch) or the FIFO replay order. Gated behind "
            "learn_journal_enabled like the rest of the journal."
        ),
        deprecation_plan=(
            "Retain as the termination bound; removal reinstates 'a record may be re-attempted forever', "
            "which is the defect this field exists to make impossible."
        ),
        docs_pointer="trw-mcp/CHANGELOG.md — learn-journal durability + recovery entries",
        test_pointer=(
            "trw-mcp/tests/test_learn_journal_drain_accounting.py::TestRetryBudget::"
            "test_transient_failure_dead_letters_once_the_budget_is_exhausted"
        ),
        budget_decision="admitted",
    ),
    "deliver_gate_unclassified_change_threshold": ConfigAdmission(
        field_name="deliver_gate_unclassified_change_threshold",
        owner="PRD-CORE-246-FR03",
        consumer="trw_mcp.tools._deliver_gate_mode._meets_change_threshold (resolve_deliver_gate_decision)",
        default_rationale=(
            "Defaults to 1, meaning ANY recorded file modification in the current session arms the "
            "missing-build-check gate for a task type that does not inherently expect a build artifact. "
            "1 is chosen because the neighbouring review-scope gate already owns the 'large change' case "
            "at its own threshold of 5; this gate is about the PRESENCE of change, not its size. Bounded "
            "ge=1/le=1000 so 0 (which would arm the gate on a ceremony-only run) and an absurd value are "
            "rejected at config load rather than silently clamped."
        ),
        interaction_analysis=(
            "Read ONLY inside the block_coding/block_all branch of resolve_deliver_gate_decision, where "
            "it is the right-hand side of an OR with the build-artifact task-type set. It therefore "
            "cannot restore the pre-CORE-246 never-block-on-unknown behavior at any value: raising it "
            "narrows the change-evidence clause but leaves coding/rca/eval blocking as before. The count "
            "it is compared against is the same distinct-path, session-scoped count the review-scope gate "
            "uses (_count_file_modified_current_session), so the framework keeps one notion of 'code "
            "changed'. An uncomputable count is treated as meeting the threshold (fail-closed, NFR02), "
            "and deliver_gate_task_type_overrides still selects the mode before this field is consulted."
        ),
        deprecation_plan=(
            "Retain as the evidence threshold; removal reinstates 'a misclassified run switches the "
            "build gate off', which is the defect PRD-CORE-246 exists to close."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-246-fail-closed-unknown-task-type.md",
        test_pointer=(
            "trw-mcp/tests/test_task_type_visibility.py::test_rationale_is_bounded_and_threshold_is_validated"
        ),
        budget_decision="admitted",
    ),
    # PRD-FIX-123: instruction-write guard tunables (own table, see module docstring).
    **INSTRUCTION_WRITE_ADMISSIONS,
    # PRD-FIX-124: auto-recall scan cap (own table, see module docstring).
    **AUTO_RECALL_ADMISSIONS,
    # PRD-CORE-244: memory-truth invariants (own table, see module docstring).
    **MEMORY_TRUTH_ADMISSIONS,
    # PRD-CORE-247: degraded-mode + instruction-budget tunables (own table).
    **DEGRADED_MODE_ADMISSIONS,
    # PRD-CORE-250: degenerate-result advisory tunables (own table).
    **DEGENERATE_RESULT_ADMISSIONS,
    # PRD-CORE-249: project-handoff write location (own table, see module docstring).
    **PROJECT_HANDOFF_ADMISSIONS,
    # PRD-CORE-248: WAL-checkpoint trigger + resetting-permit tunables (own table).
    **WAL_CHECKPOINT_ADMISSIONS,
    # PRD-CORE-255: review-verdict TTL (own table, see module docstring).
    **REVIEW_VERDICT_ADMISSIONS,
    # PRD-CORE-257: bounded writer-pressure deferral (own table).
    **WRITER_PRESSURE_ADMISSIONS,
    # PRD-CORE-265: formation manifest + enforcement tunables (own table).
    **FORMATION_ADMISSIONS,
    # PRD-FIX-130: learn-journal wall-clock drain budget (own table).
    **DRAIN_BUDGET_ADMISSIONS,
    # PRD-CORE-266: doctor formation-readiness probe bound (own table).
    **FORMATION_READINESS_ADMISSIONS,
    # PRD-SEC-015: reviewer-role selector (own table, see module docstring).
    **SURFACE_ROLE_ADMISSIONS,
}
