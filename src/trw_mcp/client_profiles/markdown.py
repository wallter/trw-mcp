"""Markdown renderers for client-profile documentation."""

from __future__ import annotations

from trw_mcp.client_profiles.catalog import build_client_profile_rows
from trw_mcp.dispatch._client_specs import CLIENT_SPECS


def _enabled_label(value: bool) -> str:
    return "on" if value else "off"


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    header = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def render_quick_reference_table() -> str:
    rows = build_client_profile_rows()
    table_rows = [
        [
            f"`{row.client_id}`",
            row.ceremony_mode,
            row.context_label,
            row.ceremony_label,
            f"`{row.write_target_label}`",
            str(row.review_weight),
        ]
        for row in rows
    ]
    return _render_table(
        ["Client", "Mode", "Context", "Ceremony", "Write Target", "Review Weight"],
        table_rows,
    )


def render_surface_matrix() -> str:
    rows = build_client_profile_rows()
    table_rows = [
        [
            f"`{row.client_id}`",
            _enabled_label(row.nudge_enabled),
            row.tool_resolution_mode,
            _enabled_label(row.learning_recall_enabled),
            _enabled_label(row.mcp_instructions_enabled),
            _enabled_label(row.hooks_enabled),
            _enabled_label(row.skills_enabled),
            _enabled_label(row.framework_ref_enabled),
            _enabled_label(row.delegation_enabled),
        ]
        for row in rows
    ]
    return _render_table(
        [
            "Client",
            "Nudges",
            "Tools",
            "Recall",
            "MCP Instructions",
            "Hooks",
            "Skills",
            "Framework Ref",
            "Delegation",
        ],
        table_rows,
    )


def render_tool_resolution_section() -> str:
    """Describe the single global tool-exposure authority (PRD-CORE-218 FR04)."""
    return (
        "**Tool resolution** (`tool_resolution_mode`): the kernel/pack resolver "
        "(SurfaceAuthorityMiddleware) is the sole exposure authority. `standard` "
        "(default) exposes the 9-tool kernel plus the packs a run's `task_type` "
        "selects; `all` is the explicit operator escape that exposes the full "
        "eligible surface. Masked pack tools stay grantable via "
        "`trw_request_tool_access`."
    )


def render_nudge_matrix() -> str:
    rows = build_client_profile_rows()
    table_rows = [
        [
            f"`{row.client_id}`",
            _enabled_label(row.nudge_enabled),
            row.nudge_messenger,
            row.nudge_density,
            str(row.nudge_budget_chars),
            row.nudge_pool_weights_label,
            str(row.nudge_cooldown_after),
        ]
        for row in rows
    ]
    return _render_table(
        [
            "Profile",
            "nudge_enabled",
            "nudge_messenger",
            "nudge_density",
            "nudge_budget_chars",
            "Pool weights (workflow/learnings/ceremony/context)",
            "Cooldown after (N ignores)",
        ],
        table_rows,
    )


def _agent_surface_label(value: bool | None) -> str:
    """Render a derived agent surface, keeping "absent" distinct from "false".

    ``None`` means the client has no entry in the client-profile registry at all,
    which is a different fact from a registered client that documents no agent
    surface. Collapsing the two to `off` would report an unmeasured gap as a
    measured absence.
    """
    if value is None:
        return "no client profile"
    return _enabled_label(value)


def render_dispatch_targets_table() -> str:
    """Render the per-client dispatch capability table (PRD-CORE-266-FR07).

    Every cell is read from ``CLIENT_SPECS``; nothing here is restated. That is
    the point: a hand-written per-client capability value is census data that
    stops enforcing anything the moment the registry changes, without announcing
    that it has stopped (PRD-INFRA-174). Adding a registry entry grows this table
    by exactly one row with no edit to this function.
    """
    table_rows = [
        [
            f"`{spec.client_id}`",
            f"`{spec.binary}`",
            f"`{spec.prompt_flag}`" if spec.prompt_flag else "positional",
            " ".join(f"`{tok}`" for tok in spec.structured_output_argv) or "none",
            spec.sandbox,
            spec.sub_agents,
            _agent_surface_label(spec.agent_surface),
            spec.verification.method,
            spec.verification.verified_at.isoformat(),
        ]
        for spec in CLIENT_SPECS.values()
    ]
    return _render_table(
        [
            "Client",
            "Binary",
            "Headless prompt",
            "Structured output",
            "Sandbox",
            "Sub-agents",
            "Agent surface",
            "Verification",
            "Verified",
        ],
        table_rows,
    )


def render_matrix_page() -> str:
    return "\n".join(
        [
            "# Client Profile Matrices",
            "",
            "> Generated from runtime profile code via `trw_mcp.client_profiles`.",
            "",
            "## Quick Reference",
            "",
            render_quick_reference_table(),
            "",
            "Ceremony weight columns: `session_start / deliver / checkpoint / learn / build_check / review`.",
            "",
            "## Surface Control Flags",
            "",
            render_surface_matrix(),
            "",
            render_tool_resolution_section(),
            "",
            "## Per-Profile Nudge Configuration",
            "",
            render_nudge_matrix(),
            "",
            "Light profiles use `ceremony=0` in nudge pool weights because ceremony reminders arrive through bootstrap and instruction files rather than mid-tool nudges.",
            "",
            "## Dispatch Targets",
            "",
            render_dispatch_targets_table(),
            "",
            "Generated from the `trw_mcp.dispatch` client-spec registry. `Verification` records HOW each row was established — `executable` means the binary was run on a box and its own output read, `primary_source` means a dated vendor page stated it and the binary was not run, and `unverified` means neither, in which case `trw-mcp dispatch` REFUSES that client rather than launching it on provisional data.",
            "",
            "`Sandbox` is a tri-state, not a boolean: `enforced` means TRW emits an explicit sandbox flag on the read-only path, `available_default_off` means the client has a sandbox that TRW does not turn on (so the row is not a protection claim), and `none` means the client exposes no sandbox flag TRW can use. `Sub-agents` of `unknown` is a recorded state, not a `no`.",
            "",
        ]
    )
