"""Admission records for the PRD-CORE-250 degenerate-result advisory tunables.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`FIELD_ADMISSIONS`. Split out for the same reason the
PRD-FIX-123, PRD-FIX-124, PRD-CORE-244 and PRD-CORE-247 tables were: the
registry grows once per new public field and already sits within a handful of
lines of the module-size gate, while five records at full metadata would push it
over on their own.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_OWNER = "PRD-CORE-250-FR10"
_CONSUMER = "trw_mcp.data.hooks.post-tool-degenerate-result.sh via lib-trw.sh::trw_degenerate_result_setting"
_DOCS = "docs/requirements-aare-f/prds/PRD-CORE-250-bundled-hook-adapters.md#prd-core-250-fr10"
_TEST = "trw-mcp/tests/hooks/test_degenerate_result_adapter.py::test_tunables_are_typed_and_configurable"

#: All five are read by ONE shell accessor rather than five, so the
#: env -> config.yaml -> default precedence and the non-numeric fallback are
#: defined once. The adapter carries no bare literal for any of them, which is
#: what FR10's `grep_absent: "=50"` assertion pins.
DEGENERATE_RESULT_ADMISSIONS: dict[str, ConfigAdmission] = {
    "degenerate_result_cooldown_calls": ConfigAdmission(
        field_name="degenerate_result_cooldown_calls",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 20 matching results skipped after one advisory fires, per session. The "
            "budget it serves is NFR06 (at most 5 advisories per 100 results); replaying every recorded "
            "live tool result with the cooldown DISABLED already holds well under it (see .trw/compliance/degenerate-result-calibration.json), "
            "so 20 buys margin rather than being the thing that makes the budget. "
            "Bounded ge=1 (0 would be a disable switch, which this is deliberately not) and le=200 "
            "so a mistyped value cannot silence the adapter for a whole session."
        ),
        interaction_analysis=(
            "Counts MATCHING results only, so it is independent of how many tool calls a session "
            "makes. Keyed by trw_pin_key so two sessions in one repository hold independent "
            "counters (NFR05). Interacts with degenerate_result_freshness_commands rather than "
            "with the other numeric fields: widening that list raises the match rate the cooldown "
            "then has to absorb."
        ),
        deprecation_plan=(
            "Retain; it is the operator's documented recourse when the advisory misfires, and the "
            "adapter has no on/off switch beyond the global HOOKS_ENABLED contract."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TEST,
        budget_decision="admitted",
    ),
    "degenerate_result_max_read_bytes": ConfigAdmission(
        field_name="degenerate_result_max_read_bytes",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 65536, which covers the live distribution with room to spare: the measured "
            "median and p95 result sizes are recorded in .trw/compliance/degenerate-result-calibration.json. The cap "
            "exists for NFR03 — a 10 MB tool_response must not drive unbounded memory or CPU in a "
            "hook the client waits on — not to make the classification cheaper. Bounded ge=1024 so "
            "it cannot be set below a realistic result, le=1048576 so it cannot defeat its purpose. Both "
            "bounds are CLAMPED in the shell accessor, not merely declared: the hook reads config.yaml "
            "with grep and never imports this model, so before the clamp a hand-edited 999999999 flowed "
            "straight into `head -c` and defeated NFR03."
        ),
        interaction_analysis=(
            "Bounds the stdin read only. Because a truncation marker is emitted at the END of a "
            "capped result, a cap smaller than the result can hide rule 2 — which is the honest "
            "trade and the reason the default sits an order of magnitude above p95. Independent of "
            "the deadline: this bounds bytes, that bounds time."
        ),
        deprecation_plan="Retain; removing it reinstates an unbounded read in a hook on the client's critical path.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/hooks/test_degenerate_result_adapter.py::test_payload_is_byte_capped",
        budget_decision="admitted",
    ),
    "degenerate_result_deadline_ms": ConfigAdmission(
        field_name="degenerate_result_deadline_ms",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Defaults to 50 ms, the NFR01/SLO figure. Self-imposed rather than inherited from the "
            "3000 ms hook timeout registered in settings.json: an advisory that costs the user "
            "measurable latency is a worse trade than a missed advisory, so the adapter gives up "
            "on itself long before the client would. Bounded ge=5 (below that the deadline would "
            "fire on process startup alone) and le=500."
        ),
        interaction_analysis=(
            "Checked after the bounded read and again before emitting, so it bounds the whole "
            "invocation rather than one step. Past the deadline the adapter exits 0 emitting "
            "nothing, which is the same silence every other degraded path produces (NFR02) — a "
            "deadline miss is never an error and never a block."
        ),
        deprecation_plan="Retain; it is the NFR01 enforcement point and the rollback lever for the latency risk.",
        docs_pointer=_DOCS,
        test_pointer="trw-mcp/tests/hooks/test_degenerate_result_adapter.py::test_p95_latency_under_budget",
        budget_decision="admitted",
    ),
    "degenerate_result_truncation_markers": ConfigAdmission(
        field_name="degenerate_result_truncation_markers",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "Harvested, not guessed (FR06 acceptance step 1), from real PostToolUse tool_result "
            "bodies; hit counts with their N and date are in .trw/compliance/degenerate-result-calibration.json. "
            "The candidates that came to mind instead — '<response clipped>', 'PARTIAL view', "
            "'[truncated]', 'lines truncated' — scored ZERO and are deliberately absent, because a "
            "marker set padded with plausible strings is how a shape test becomes a guess. The list "
            "is typed rather than baked in precisely because the real markers are client-specific."
        ),
        interaction_analysis=(
            "Matched as literals with grep -F against the capped read — never as a regex and never "
            "interpolated into a command (NFR03) — so a hostile tool output cannot turn an entry "
            "into a pattern. Interacts with degenerate_result_max_read_bytes, which decides how "
            "much of the result the markers are looked for in."
        ),
        deprecation_plan=(
            "Retain; a new client whose truncation marker differs is a config edit rather than a "
            "code change, which is the whole reason this is a field."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TEST,
        budget_decision="admitted",
    ),
    "degenerate_result_freshness_commands": ConfigAdmission(
        field_name="degenerate_result_freshness_commands",
        owner=_OWNER,
        consumer=_CONSUMER,
        default_rationale=(
            "The seven prefixes that survived calibration: git log, date, ls -l, stat, curl, "
            "gh api, gh run list. Matching them as first-line command PREFIXES holds the NFR06 "
            "budget; the substring-matched superset tried first blew it several times over on the "
            "same seven entries. Both measurements are in .trw/compliance/degenerate-result-calibration.json — "
            "so the allowlist is not a formality, it is the difference between the rule shipping "
            "and OQ-02 dropping it."
        ),
        interaction_analysis=(
            "Gates rule 3 only; rules 1 and 2 are unconditional shape tests. Emptying this list "
            "disables rule 3 without touching the other two, which is exactly the OQ-02 rollback "
            "the PRD authorises and the reason it is a list rather than a boolean."
        ),
        deprecation_plan=(
            "Retain; trimming it is the documented first response to a misfire, and it carries the OQ-02 escape hatch."
        ),
        docs_pointer=_DOCS,
        test_pointer=_TEST,
        budget_decision="admitted",
    ),
}

__all__ = ["DEGENERATE_RESULT_ADMISSIONS"]
