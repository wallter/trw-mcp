"""Admission record for the learn-journal wall-clock drain budget (PRD-FIX-130).

Belongs to the ``_field_admission_registry.py`` table, which splices this
mapping in. Kept in its own module for the same reason every sibling table is:
the registry is a data file that would otherwise cross the module-size gate once
per admitted field.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

DRAIN_BUDGET_ADMISSIONS: dict[str, ConfigAdmission] = {
    "learn_journal_drain_budget_ms": ConfigAdmission(
        field_name="learn_journal_drain_budget_ms",
        owner="PRD-FIX-130-FR01 (bounded learn-journal drain on the session_start hot path)",
        consumer=(
            "trw_mcp.state.learn_journal.drain_pending (budget_seconds=), passed by "
            "trw_mcp.tools._ceremony_maintenance_steps._run_learn_journal_drain"
        ),
        default_rationale=(
            "Defaults to 3000 ms against a measured 3,958 ms zero-pending session_start, so recovery "
            "makes steady progress (1-4 records per sweep at the measured 0.62-2.27 s stored-record "
            "cost) without more than roughly doubling the hot path. The sibling count limit "
            "(learn_journal_drain_limit, 50) cannot bound this work because per-record cost is bimodal "
            "and load-dependent: 0.37-1.18 s idle, up to 7.7 s under live load, and 307 s in the "
            "measured case where the one-time dedup migration fired inside the first replay. Measured "
            "2026-09-04 on a 9,434-row store: 77 pending records took 353,590 ms and 27 took 207,426 ms, "
            "both far past the 120 s client tool bound the journal exists to dodge. 0 disables inline "
            "draining entirely (background-only) and is the config-only rollback for this whole PRD; "
            "le=120000 keeps the value below that same client bound by construction."
        ),
        interaction_analysis=(
            "Bounds WALL TIME over the count the pressure path already decided, so it composes with "
            "rather than replaces learn_journal_drain_limit, learn_journal_drain_min_batch, and "
            "learn_journal_pending_max_age_hours: those three answer 'how many records may this sweep "
            "attempt', this one answers 'for how long'. The age hatch therefore raises only the count "
            "budget and can never raise elapsed time (FR06), which is what turned an overnight backlog "
            "into a 200-350 s first call before this field existed. Gated behind learn_journal_enabled. "
            "Read ONLY on the session_start hot path — the operator CLI drain "
            "(server/_subcommands_learn_drain.py) passes no budget and stays unbounded, because an "
            "operator-invoked drain is not a hot path. It is a SOFT budget by construction: the check "
            "sits between records, so one replay can exceed the whole deadline and the sweep overruns by "
            "at most one record; the hard guarantee that the backlog still lands in the same session is "
            "the FR02 background continuation, not this value. An invalid value (negative, NaN, "
            "infinite) is refused to 0 with learn_journal_drain_budget_invalid at WARNING, NEVER to "
            "unbounded — silently restoring the defect would be the failure this field exists to remove."
        ),
        deprecation_plan=(
            "Retain as the hot-path bound; removing it reinstates a drain whose only limit is a count, "
            "which is the PRD-FIX-130 defect. Set to 0 rather than removing it to fall back to "
            "background-only draining."
        ),
        docs_pointer="docs/requirements-aare-f/prds/PRD-FIX-130.md",
        test_pointer=(
            "trw-mcp/tests/test_learn_journal_drain_liveness.py::"
            "TestWallClockBudget::test_wall_clock_budget_stops_the_sweep_before_the_next_replay"
        ),
        budget_decision="admitted",
    ),
}
