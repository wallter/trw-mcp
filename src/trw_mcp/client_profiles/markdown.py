"""Markdown renderers for client-profile documentation."""

from __future__ import annotations

from trw_mcp.client_profiles.catalog import build_client_profile_rows, tool_preset_counts


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
            row.tool_exposure_mode,
            _enabled_label(row.learning_recall_enabled),
            _enabled_label(row.mcp_instructions_enabled),
            _enabled_label(row.hooks_enabled),
            _enabled_label(row.skills_enabled),
            _enabled_label(row.framework_ref_enabled),
            _enabled_label(row.agent_teams_enabled),
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
            "Agent Teams",
            "Delegation",
        ],
        table_rows,
    )


def render_tool_preset_section() -> str:
    counts = tool_preset_counts()
    lines = ["**Tool exposure presets** (`tool_exposure_mode`):"]
    for name, count in counts:
        lines.append(f"- `{name}` — {count} tools")
    return "\n".join(lines)


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
            render_tool_preset_section(),
            "",
            "## Per-Profile Nudge Configuration",
            "",
            render_nudge_matrix(),
            "",
            "Light profiles use `ceremony=0` in nudge pool weights because ceremony reminders arrive through bootstrap and instruction files rather than mid-tool nudges.",
            "",
        ]
    )
