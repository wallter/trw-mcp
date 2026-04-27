"""Runtime-derived registry for client-profile documentation."""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.models.config import resolve_client_profile
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._defaults import DEFAULT_NUDGE_BUDGET_CHARS, TOOL_PRESETS

_CLIENT_ORDER: tuple[str, ...] = (
    "claude-code",
    "opencode",
    "cursor-ide",
    "cursor-cli",
    "codex",
    "copilot",
    "gemini",
    "aider",
)


@dataclass(frozen=True, slots=True)
class ClientProfileDocRow:
    """Documentation-facing summary for one built-in client profile."""

    client_id: str
    ceremony_mode: str
    context_label: str
    ceremony_label: str
    write_target_label: str
    review_weight: int
    nudge_enabled: bool
    tool_exposure_mode: str
    learning_recall_enabled: bool
    mcp_instructions_enabled: bool
    hooks_enabled: bool
    skills_enabled: bool
    framework_ref_enabled: bool
    agent_teams_enabled: bool
    delegation_enabled: bool
    instruction_path: str
    nudge_messenger: str
    nudge_density: str
    nudge_budget_chars: int
    nudge_pool_weights_label: str
    nudge_cooldown_after: int


def _format_context_window(tokens: int) -> str:
    if tokens >= 1_000_000 and tokens % 1_000_000 == 0:
        return f"{tokens // 1_000_000}M"
    if tokens % 1_000 == 0:
        return f"{tokens // 1_000}K"
    return str(tokens)


def _format_ceremony_label(profile: ClientProfile) -> str:
    weights = profile.ceremony_weights
    return "/".join(
        str(value)
        for value in (
            weights.session_start,
            weights.deliver,
            weights.checkpoint,
            weights.learn,
            weights.build_check,
            weights.review,
        )
    )


def _write_target_label(profile: ClientProfile) -> str:
    targets = profile.write_targets
    if targets.cursor_rules:
        return ".cursor/rules/"
    if targets.copilot_instructions:
        return ".github/copilot-instructions.md"
    if targets.gemini_md:
        return "GEMINI.md"
    if targets.claude_md:
        return "CLAUDE.md"
    return "AGENTS.md"


def _format_pool_weights(profile: ClientProfile) -> str:
    weights = profile.nudge_pool_weights
    return f"{weights.workflow} / {weights.learnings} / {weights.ceremony} / {weights.context}"


def build_client_profile_rows() -> tuple[ClientProfileDocRow, ...]:
    """Return documentation rows for all built-in client profiles."""
    rows: list[ClientProfileDocRow] = []
    for client_id in _CLIENT_ORDER:
        profile = resolve_client_profile(client_id)
        rows.append(
            ClientProfileDocRow(
                client_id=client_id,
                ceremony_mode=profile.ceremony_mode,
                context_label=_format_context_window(profile.context_window_tokens),
                ceremony_label=_format_ceremony_label(profile),
                write_target_label=_write_target_label(profile),
                review_weight=profile.ceremony_weights.review,
                nudge_enabled=profile.nudge_enabled,
                tool_exposure_mode=profile.tool_exposure_mode,
                learning_recall_enabled=profile.learning_recall_enabled,
                mcp_instructions_enabled=profile.mcp_instructions_enabled,
                hooks_enabled=profile.hooks_enabled,
                skills_enabled=profile.skills_enabled,
                framework_ref_enabled=profile.include_framework_ref,
                agent_teams_enabled=profile.include_agent_teams,
                delegation_enabled=profile.include_delegation,
                instruction_path=profile.write_targets.instruction_path,
                nudge_messenger="standard",
                nudge_density=profile.nudge_density or "None",
                nudge_budget_chars=DEFAULT_NUDGE_BUDGET_CHARS,
                nudge_pool_weights_label=_format_pool_weights(profile),
                nudge_cooldown_after=3,
            )
        )
    return tuple(rows)


def tool_preset_counts() -> tuple[tuple[str, int], ...]:
    """Return tool preset names and counts in stable display order."""
    return tuple((name, len(TOOL_PRESETS[name])) for name in ("all", "standard", "minimal", "core"))
