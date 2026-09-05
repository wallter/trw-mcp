"""Admission records for the degraded-mode and instruction-budget fields.

Covers PRD-CORE-247's four fields plus the PRD-FIX-128 marker-retention bound.

Belongs to the ``_field_admission_registry.py`` data table, which merges this
mapping into :data:`FIELD_ADMISSIONS`. Split out for the same reason the
PRD-FIX-123 and PRD-FIX-124 tables were: the registry already sits within a
handful of lines of the module-size gate, and the table grows once per new
public field.

Imports nothing from the rest of ``trw_mcp.models.config`` except the record
type, so it cannot create an import cycle with ``TRWConfig``.
"""

from __future__ import annotations

from trw_mcp.models.config._field_admission_registry_types import ConfigAdmission

_PRD = "docs/requirements-aare-f/prds/PRD-CORE-247-degraded-mode-ceremony-and-instruction-budget.md"
_SESSION_START = "trw_mcp.data.hooks.session-start.sh (epoch marker + framework directive)"
_PROMPT_HOOK = "trw_mcp.data.hooks.user-prompt-submit.sh (degraded-mode detector)"
_MARKER_SWEEP = (
    "trw_mcp.data.hooks.session-start.sh (per-session marker sweep) + "
    "trw_mcp.data.hooks.session-end.sh (owned-marker reclamation)"
)
_FIX_128_PRD = "docs/requirements-aare-f/prds/PRD-FIX-128-per-session-degraded-mode-detection.md"

DEGRADED_MODE_ADMISSIONS: dict[str, ConfigAdmission] = {
    "degraded_detect_grace_seconds": ConfigAdmission(
        field_name="degraded_detect_grace_seconds",
        owner="PRD-CORE-247-FR01",
        consumer=_PROMPT_HOOK,
        default_rationale=(
            "Defaults to 180 s. The detector concludes the MCP surface is absent only after "
            "this much wall-clock has elapsed since the SessionStart epoch marker. The bound "
            "is set by the client's own connect timeout: the observed failure gave up after "
            "120000 ms, so any window shorter than that would fire on healthy sessions that "
            "simply had not called a tool yet. 180 leaves 60 s of margin. Bounded ge=30 so a "
            "value cannot be lowered into the false-positive regime and le=900 so it cannot "
            "be raised past the point where the verdict is useless; a non-numeric value falls "
            "back to this default rather than propagating."
        ),
        interaction_analysis=(
            "Read only by the UserPromptSubmit detector, in conjunction with "
            "degraded_detect_min_prompts: BOTH must hold before a degraded verdict is "
            "reached, so raising either narrows detection and neither alone can widen it. It "
            "also gates PRD-CORE-247-FR07: while the verdict stands, the SessionStart "
            "framework read directive is suppressed, because the document describes tools "
            "that do not exist in that session."
        ),
        deprecation_plan=(
            "Retain; it is the rollback lever for the false-positive risk in the FR01 risk "
            "register. Setting it to its ceiling effectively disables the detector without "
            "shipping a dormant on/off flag."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_core_247_degraded_mode_hooks.py::test_degraded_detector_fires_only_without_observed_tool_call",
        budget_decision="admitted",
    ),
    "degraded_detect_min_prompts": ConfigAdmission(
        field_name="degraded_detect_min_prompts",
        owner="PRD-CORE-247-FR01",
        consumer=_PROMPT_HOOK,
        default_rationale=(
            "Defaults to 2. A completed first turn with no trw_ tool invocation is the "
            "discriminating evidence that the surface is absent rather than merely unused; "
            "elapsed time alone fires on a session whose first turn is a long read. Bounded "
            "ge=1 (a value of 0 would make the prompt condition vacuous) and le=10 (past "
            "which a real outage is detected too late to help)."
        ),
        interaction_analysis=(
            "Read only by the UserPromptSubmit detector, as the second of two independent "
            "conditions alongside degraded_detect_grace_seconds. The prompt index is read "
            "from the SessionStart epoch marker, which the prompt hook increments; a marker "
            "that cannot be read or parsed resolves to 'surface present' and the hook emits "
            "nothing (NFR02)."
        ),
        deprecation_plan=(
            "Retain; it is the second half of the two-condition guard that makes a false "
            "degraded verdict structurally hard, which is the safety property FR01 rests on."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_core_247_degraded_mode_hooks.py::test_degraded_detector_fires_only_without_observed_tool_call",
        budget_decision="admitted",
    ),
    "degraded_event_tail_lines": ConfigAdmission(
        field_name="degraded_event_tail_lines",
        owner="PRD-FIX-128 (external audit row 7)",
        consumer=_PROMPT_HOOK,
        default_rationale=(
            "Defaults to 500. Was previously TRW_SESSION_EVENT_TAIL_LINES, an untyped env "
            "var read directly by _trw_scan_log_for_trw_call with no schema entry and no "
            "upper bound, so a value like 10000000 could buffer an entire event log into "
            "the hook's memory on every prompt. Bounded ge=50 (a smaller tail risks missing "
            "this session's own recent trw_ call) and le=2000 (a larger tail is no longer "
            "the O(1)-relative-to-budget read NFR01 requires); a malformed or out-of-range "
            "value falls back to 500 rather than propagating."
        ),
        interaction_analysis=(
            "Read by _trw_scan_log_for_trw_call, the shared tail-reader both FR01 scan "
            "targets (the owned run's meta/events.jsonl and the pinless "
            ".trw/context/session-events.jsonl) go through, via trw_config_int. TRW_SESSION_"
            "EVENT_TAIL_LINES remains the env override name for backward compatibility with "
            "operators already setting it; it is now validated and bounded rather than used "
            "raw."
        ),
        deprecation_plan=(
            "Retain; it is the NFR01 latency-budget lever for the tail read, same role as "
            "the sibling degraded_* tunables in this table."
        ),
        docs_pointer=_FIX_128_PRD,
        test_pointer="trw-mcp/tests/test_fix_128_audit_fixes.py::test_degraded_event_tail_lines_is_bounded",
        budget_decision="admitted",
    ),
    "degraded_marker_retention_hours": ConfigAdmission(
        field_name="degraded_marker_retention_hours",
        owner="PRD-FIX-128-FR08",
        consumer=_MARKER_SWEEP,
        default_rationale=(
            "Defaults to 24 hours. Keying the epoch marker and the emission latch on the "
            "session (FR02/FR03) turned two overwritten files into one file per session, so "
            "markers now accumulate and something has to reclaim them. This is the SECONDARY "
            "bound only: the sweep skips every identity that still holds a live pin, whatever "
            "the marker's age, and the age bound applies solely to identities with no live pin "
            "record. 24 hours because a marker older than a day cannot belong to a live client "
            "session; bounded ge=1 so the sweep cannot be turned into an immediate purge and "
            "le=168 (one week) past which a marker is certainly garbage. A malformed or "
            "out-of-range value falls back to this default rather than pruning everything or "
            "nothing."
        ),
        interaction_analysis=(
            "Read by the SessionStart marker sweep and, through it, bounded by pin_ttl_hours: "
            "liveness is evaluated FIRST with the server's own predicate (state/_pin_ttl.py -- "
            "expired only when the recorded creator process is gone AND the heartbeat is older "
            "than pin_ttl_hours; a non-positive TTL disables expiry entirely), and only an "
            "identity with no live pin reaches this age bound. A pin store that is absent, "
            "unreadable, or malformed prunes nothing at all, so this value can never widen "
            "reclamation past what the pin store supports. SessionEnd removes its own two "
            "markers directly and does not consult this field."
        ),
        deprecation_plan=(
            "Retain; it is the secondary bound on the FR08 sweep and its ceiling is the "
            "documented stopgap while a liveness-gate defect is reverted. It is a bounded "
            "typed knob, not an on/off switch for the reclamation itself."
        ),
        docs_pointer=_FIX_128_PRD,
        test_pointer="trw-mcp/tests/test_core_247_degraded_mode_hooks.py::test_session_markers_are_reclaimed",
        budget_decision="admitted",
    ),
    "framework_read_scope": ConfigAdmission(
        field_name="framework_read_scope",
        owner="PRD-CORE-247-FR07",
        consumer=_SESSION_START,
        default_rationale=(
            "Defaults to 'phase'. The unconditional whole-document directive charged an "
            "estimated 9230 tokens (35073 characters) of methodology on every session, "
            "including sessions with no MCP surface at all. Naming only the sections for the "
            "phase in hand costs at most 6349 characters, 18.1 percent of the document. "
            "'full' is retained as a documented tunable because whether a phase-scoped read "
            "preserves ceremony adherence as well as a whole-document read is not yet "
            "measured (PRD-CORE-247 OQ-03) — it is an operator lever, not an on/off switch "
            "for the fix."
        ),
        interaction_analysis=(
            "Read by the SessionStart framework directive alongside "
            "TRW_FRAMEWORK_MD_ENABLED and the FR01 degraded verdict. The three compose as a "
            "conjunction: the directive is emitted only when the framework reference is "
            "enabled AND the surface is not known-absent, and its scope is then decided by "
            "this field. The phase-to-section mapping is a documented table in the hook, not "
            "a magic constant."
        ),
        deprecation_plan=(
            "Retain until the adherence comparison in OQ-03 has run with N and an interval. "
            "If phase scoping is shown to preserve adherence, 'full' can be retired and this "
            "field with it."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_core_247_framework_read_budget.py::test_framework_directive_is_gated_and_phase_scoped",
        budget_decision="admitted",
    ),
    "instruction_catalogue_mode": ConfigAdmission(
        field_name="instruction_catalogue_mode",
        owner="PRD-CORE-247-FR08",
        consumer="trw_mcp.state.claude_md._renderer.ProtocolRenderer.render_behavioral_protocol",
        default_rationale=(
            "Defaults to 'pointer'. The verbatim ceremony table is 2777 characters, 53 "
            "percent of the 5244-character full-ceremony protocol block, and a hand-copied "
            "list can drift from what the server actually exposes while a pointer to "
            "trw_skill_discovery/trw_status cannot. 'verbatim' is retained for an operator "
            "whose client cannot make the discovery call at all."
        ),
        interaction_analysis=(
            "Read only when the resolved client profile's ceremony_mode is 'full'. A "
            "light-ceremony profile renders the catalogue verbatim unconditionally, because "
            "the generated instruction file IS its only protocol carrier "
            "(.trw/frameworks/FRAMEWORK-CORE.md, RIGID / FLEXIBLE section) — so this field "
            "cannot strip the catalogue from a client that has no other way to learn the "
            "tool surface. The profile wins; this field only decides the full-ceremony case."
        ),
        deprecation_plan=(
            "Retain; it is the FR08 rollback lever named in the PRD's rollout plan, and "
            "restoring the verbatim table for full-ceremony clients is a config change "
            "rather than a revert."
        ),
        docs_pointer=_PRD,
        test_pointer="trw-mcp/tests/test_core_247_instruction_budget.py::test_catalogue_becomes_a_pointer_for_full_mode_only",
        budget_decision="admitted",
    ),
}

__all__ = ["DEGRADED_MODE_ADMISSIONS"]
