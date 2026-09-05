"""Degraded-mode detection and instruction-budget tunables (PRD-CORE-247).

Domain mixin. Split from ``_fields_ceremony.py`` because that file sits against
the 200-line domain-mixin ceiling
(``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``), and
because these knobs are one subject: what the framework charges an agent
for, and when.

The first three are read by the shell hooks (SessionStart writes the epoch marker
and sweeps stale ones; UserPromptSubmit is the detector), the last two by the
SessionStart framework directive and the instruction renderer. All are tunables
with a defensible
default, never on/off switches for the fix itself: detection, phase scoping, and
the catalogue pointer are default-on. Rationale, interaction analysis, and
deprecation plan for each live in
``_field_admission_degraded_mode.py::DEGRADED_MODE_ADMISSIONS``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field


class _DegradedModeFields:
    """Typed tunables for PRD-CORE-247 degraded mode and the instruction budget."""

    degraded_detect_grace_seconds: int = Field(
        default=180,
        ge=30,
        le=900,
        description=(
            "Seconds after the SessionStart epoch marker before the absent-MCP-surface "
            "detector may conclude the surface is absent. Must exceed the client connect "
            "timeout; the observed failure gave up after 120 s, so 180 leaves 60 s margin."
        ),
    )
    degraded_detect_min_prompts: int = Field(
        default=2,
        ge=1,
        le=10,
        description=(
            "Minimum prompt index in this session before the absent-MCP-surface detector "
            "may fire. A completed first turn with no trw_ tool call is the discriminating "
            "evidence; elapsed time alone fires on a session whose first turn is a long read."
        ),
    )
    degraded_event_tail_lines: int = Field(
        default=500,
        ge=50,
        le=2000,
        description=(
            "PRD-FIX-128 external audit row 7. Number of trailing lines the detector reads "
            "from an event log (owned-run or pinless) when checking for a recent trw_ tool "
            "call. Previously an untyped, unbounded env var (TRW_SESSION_EVENT_TAIL_LINES) "
            "read directly with no schema and no ceiling, so an operator value like "
            "10000000 could buffer an entire log into memory on every prompt and blow the "
            "NFR01 latency budget. Bounded ge=50 (below which a fast-moving session could "
            "miss its own recent call) and le=2000 (past which the read is no longer O(1) "
            "relative to the budget); a malformed or out-of-range value falls back to 500."
        ),
    )
    degraded_marker_retention_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        description=(
            "How long an epoch marker or emission latch belonging to an identity with NO live "
            "pin is kept before the SessionStart sweep reclaims it. Liveness is checked first "
            "and unconditionally: an identity that still holds a pin is never pruned, whatever "
            "its age, because a long idle session is silent by definition and deleting its "
            "marker would reset the very session most likely to be judged degraded next. 24 "
            "hours because a marker older than a day cannot belong to a live client session; "
            "168 is one week, past which a marker is certainly garbage."
        ),
    )
    framework_read_scope: Literal["phase", "full"] = Field(
        default="phase",
        description=(
            "Whether the SessionStart framework read directive names only the sections for "
            "the current phase ('phase', the default, at most 6349 characters) or the whole "
            "35073-character document ('full')."
        ),
    )
    instruction_catalogue_mode: Literal["pointer", "verbatim"] = Field(
        default="pointer",
        description=(
            "Whether a full-ceremony client's generated protocol block carries a pointer to "
            "trw_skill_discovery/trw_status ('pointer', the default) or the verbatim tool "
            "table ('verbatim'). Light-ceremony profiles always render verbatim — the "
            "generated file is their only protocol carrier — regardless of this value."
        ),
    )
