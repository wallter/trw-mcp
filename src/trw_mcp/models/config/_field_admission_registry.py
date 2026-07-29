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

from typing import Literal

from pydantic import BaseModel, ConfigDict

BudgetDecision = Literal["admitted", "legacy-admitted", "rejected", "deferred"]


class ConfigAdmission(BaseModel):
    """Full admission record for one public configuration field."""

    model_config = ConfigDict(frozen=True)

    field_name: str
    owner: str
    consumer: str
    default_rationale: str
    interaction_analysis: str
    deprecation_plan: str
    docs_pointer: str
    test_pointer: str
    budget_decision: BudgetDecision


#: Explicit full-metadata admissions for public fields added by PRD-CORE-218
#: itself (or later). Every current field outside the frozen legacy baseline
#: MUST have a complete :class:`ConfigAdmission` entry here or the gate rejects
#: it. ``tool_resolution_mode`` is FR04's standard/all selector — the SOLE tool
#: exposure authority (the legacy CORE-125 tool_exposure_mode/list fields and
#: their TOOL_PRESETS vocabulary were removed at activation).
FIELD_ADMISSIONS: dict[str, ConfigAdmission] = {
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
            "A cap lower than a commit's changed-file count reduces the FR01 delivery rate measured by "
            "hint_delivery_rate_min, which is the intended latency/coverage trade-off (OQ-06)."
        ),
        deprecation_plan="Retain; removing it would reintroduce an unbounded per-commit fan-out.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_post_commit_sidecar_refresh.py::test_file_cap_bounds_the_plan",
        budget_decision="admitted",
    ),
    "hint_delivery_rate_min": ConfigAdmission(
        field_name="hint_delivery_rate_min",
        owner="PRD-CORE-231-FR01",
        consumer="hint_delivered telemetry aggregation over .trw/telemetry/channel-events.jsonl (NFR02 gate)",
        default_rationale=(
            "Defaults to 0.90 — the >=90%-of-eligible-edits delivery gate specified by the Track R "
            "synthesis; expressed as a bounded float so the gate is tunable without a code edit."
        ),
        interaction_analysis=(
            "Compared against the delivered/eligible ratio computed over the trailing "
            "hint_delivery_measurement_window_days window; the two are always read together and are "
            "meaningless apart. No interaction with the sidecar-refresh knobs beyond the causal one "
            "(a lower file cap lowers the measured rate)."
        ),
        deprecation_plan="Retain while the T2 delivery gate is measured; superseded by the FR01 expiry decision.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_config_field_bounds.py::test_hint_delivery_rate_min_bounds",
        budget_decision="admitted",
    ),
    "hint_delivery_measurement_window_days": ConfigAdmission(
        field_name="hint_delivery_measurement_window_days",
        owner="PRD-CORE-231-FR01",
        consumer="hint_delivered telemetry aggregation over .trw/telemetry/channel-events.jsonl (NFR02 gate)",
        default_rationale=(
            "Defaults to 14 days, the trailing window the Track R synthesis specifies for both the "
            "delivery-rate and silent-stale gates; ge=1 rejects a zero-length window."
        ),
        interaction_analysis=(
            "Sole companion of hint_delivery_rate_min — defines the window the ratio is computed over. "
            "Widening it smooths the measurement; it changes no runtime behavior, only the gate reading."
        ),
        deprecation_plan="Retain alongside hint_delivery_rate_min; the pair is removed or kept together.",
        docs_pointer="docs/requirements-aare-f/prds/PRD-CORE-231-track-r-memory-truthfulness-repair.md",
        test_pointer="trw-mcp/tests/test_config_field_bounds.py::test_hint_delivery_window_bounds",
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
        docs_pointer="docs/research/framework-simplification/SURFACE-CENSUS-2026-07-24.md",
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
        docs_pointer="docs/research/framework-simplification/SURFACE-CENSUS-2026-07-24.md",
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
        docs_pointer="docs/research/framework-simplification/SURFACE-CENSUS-2026-07-24.md",
        test_pointer=(
            "trw-mcp/tests/test_learn_journal_drain_accounting.py::TestRetryBudget::"
            "test_transient_failure_dead_letters_once_the_budget_is_exhausted"
        ),
        budget_decision="admitted",
    ),
}
