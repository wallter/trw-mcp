"""Admission records for the PRD-CORE-248 tunables.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`~trw_mcp.models.config._field_admission.FIELD_ADMISSIONS`.
Split out for the same reason every other per-PRD admission table is: the
registry grows once per new public field and would otherwise drift past the
module-size gate each time one is admitted.

Two fields are admitted: the WAL age knob (FR04) and the deferred-boot
budget (FR01). A fourth, ``wal_truncate_exclusive_wait_ms``, was withdrawn when
review reversed OQ-1: a resetting checkpoint is now refused outright below
SQLite 3.51.3, so there is no exclusive-window probe left to bound. ``wal_checkpoint_threshold_mb`` is NOT among them —
it predates PRD-CORE-218 and stays on the frozen legacy baseline; FR04 only
converts it from a bare annotated integer to a bounded, described ``Field``,
which changes no admission class.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_DOCS = "docs/requirements-aare-f/prds/PRD-CORE-248-stdio-concurrency-boot-and-wal.md"
_TESTS = "trw-mcp/tests/test_maintenance_config.py::test_wal_tunables_are_bounded_documented_fields"

WAL_CHECKPOINT_ADMISSIONS: dict[str, ConfigAdmission] = {
    "wal_checkpoint_max_age_seconds": ConfigAdmission(
        field_name="wal_checkpoint_max_age_seconds",
        owner="PRD-CORE-248-FR04",
        consumer="trw_mcp.server._doctor_memory_wal.memory_wal_row",
        default_rationale=(
            "Defaults to 3600 s. The SLO this field exists to make achievable is 'at least one "
            "checkpoint per hour while two or more writers are live', so the age trigger fires at "
            "exactly that period. A shorter default would checkpoint an idle store for no reason; a "
            "longer one could not satisfy the SLO at all."
        ),
        interaction_analysis=(
            "The OR-partner of wal_checkpoint_threshold_mb: either condition alone makes a "
            "checkpoint due, so the age trigger is what keeps a small-but-stale WAL from going "
            "un-checkpointed forever on a low-write machine. trw-mcp no longer checkpoints (the "
            "daemon owns its WAL); the doctor WAL row reads this age as its staleness bound."
        ),
        deprecation_plan=(
            "Retain; removing it restores the size-only trigger that let a stale WAL survive "
            "indefinitely below the threshold."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TESTS,
        budget_decision="admitted",
    ),
    "boot_deferred_work_budget_ms": ConfigAdmission(
        field_name="boot_deferred_work_budget_ms",
        owner="PRD-CORE-248-FR01",
        consumer="trw_mcp.server._app._resolve_deferred_budget_ms (passed to _boot_deferred.schedule_deferred_boot_work)",
        default_rationale=(
            "Defaults to 5000 ms. The deferred block measures 12.6 ms today, so the budget is "
            "three orders of magnitude of headroom on purpose: it exists to bound a step that "
            "acquired unexpected I/O, not to trim the step that exists. A non-zero "
            "boot_deferred_work_budget_exceeded rate is therefore a signal about the WORK, not "
            "about the budget being tight."
        ),
        interaction_analysis=(
            "Bounds the scheduled attempt only. Exceeding it never leaves sync unconfigured: the "
            "first tool call re-runs the resolution inline (NFR02 fail-closed toward correctness), "
            "so this knob trades boot latency against first-tool-call latency and nothing else. "
            "Independent of boot_gc_deferred, which governs a different background step."
        ),
        deprecation_plan=(
            "Retain; without a bound, a deferred step that hangs would hold the sync configuration "
            "unresolved with no warning and no observable phase event."
        ),
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/test_boot_initialize_ordering.py::test_budget_exceeded_warns_and_first_tool_call_runs_it_inline",
        budget_decision="admitted",
    ),
}

__all__ = ["WAL_CHECKPOINT_ADMISSIONS"]
