"""Built-in client profile registry and resolution.

Eight profiles (claude-code, opencode, cursor-ide, cursor-cli, codex, copilot, gemini, aider)
with eval-data-calibrated ceremony and scoring weights. Unknown client IDs
fall back to claude-code with a structured warning.

Migration note: the bare ``cursor`` profile ID was removed in Sprint 91.
Use ``cursor-ide`` for interactive Cursor IDE or ``cursor-cli`` for headless
``cursor-agent`` CI runs.
"""

from __future__ import annotations

import structlog

from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    ModelTier,
    NudgePoolWeights,
    ScoringDimensionWeights,
    WriteTargets,
)

logger = structlog.get_logger(__name__)

# Shared constants for light-mode profiles (opencode, codex, aider) — DRY (P1-B)
_LIGHT_CEREMONY = CeremonyWeights(
    session_start=30,
    deliver=30,
    checkpoint=5,
    learn=20,
    build_check=15,
    review=0,
)
_LIGHT_SCORING = ScoringDimensionWeights(
    outcome=0.60,
    plan_quality=0.05,
    implementation=0.15,
    ceremony=0.05,
    knowledge=0.15,
)
_LIGHT_PHASES = ["implement", "deliver"]

# Model tier adjustments for resolve_client_profile (F06 -- model_copy, not mutate)
# NOTE: This is a merged type for pydantic fields (int, str, bool) - but we only use int/string fields
_TIER_OVERRIDES: dict[ModelTier, dict[str, object]] = {
    "cloud-opus": {"context_window_tokens": 200_000, "instruction_max_lines": 500},
    "cloud-sonnet": {"context_window_tokens": 200_000, "instruction_max_lines": 500},
    "local-30b": {"context_window_tokens": 128_000, "instruction_max_lines": 350},
    "local-8b": {"context_window_tokens": 32_000, "instruction_max_lines": 200},
}


def _light_profile(client_id: str, display_name: str, instruction_path: str) -> ClientProfile:
    """Construct a light-mode profile with eval-calibrated defaults."""
    return ClientProfile(
        client_id=client_id,
        display_name=display_name,
        write_targets=WriteTargets(agents_md=True, instruction_path=instruction_path),
        instruction_max_lines=200,
        context_window_tokens=32_000,
        ceremony_mode="light",
        ceremony_weights=_LIGHT_CEREMONY,
        nudge_pool_weights=NudgePoolWeights(workflow=60, learnings=30, ceremony=0, context=10),
        mandatory_phases=_LIGHT_PHASES,
        scoring_weights=_LIGHT_SCORING,
        default_model_tier="local-8b",
        hooks_enabled=False,
        agents_md_enabled=True,
        include_framework_ref=False,
        include_agent_teams=False,
        include_delegation=False,
        # Surface control (PRD-CORE-125)
        nudge_enabled=False,
        tool_exposure_mode="standard",
        learning_recall_enabled=True,
        mcp_instructions_enabled=False,
        skills_enabled=False,
    )


_PROFILES: dict[str, ClientProfile] = {
    "claude-code": ClientProfile(
        client_id="claude-code",
        display_name="Claude Code",
        write_targets=WriteTargets(claude_md=True, instruction_path=".claude/INSTRUCTIONS.md"),
        ceremony_weights=CeremonyWeights(),  # defaults = claude-code
        nudge_pool_weights=NudgePoolWeights(),  # defaults: 40/30/20/10
        scoring_weights=ScoringDimensionWeights(),  # defaults = claude-code
        hooks_enabled=True,
        include_framework_ref=True,
        include_agent_teams=True,
        include_delegation=True,
        # Surface control (PRD-CORE-125)
        nudge_enabled=True,
        tool_exposure_mode="all",
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
        # PRD-FIX-078: claude-code exposes MCP tools under mcp__{server}__{tool}
        tool_namespace_prefix="mcp__trw__",
    ),
    "opencode": _light_profile("opencode", "OpenCode", ".opencode/INSTRUCTIONS.md"),
    "cursor-ide": ClientProfile(
        client_id="cursor-ide",
        display_name="Cursor IDE",
        write_targets=WriteTargets(
            cursor_rules=True,
            agents_md=True,
            instruction_path=".cursor/rules/trw-ceremony.mdc",
        ),
        instruction_max_lines=400,
        context_window_tokens=128_000,
        ceremony_mode="full",
        ceremony_weights=CeremonyWeights(),
        nudge_pool_weights=NudgePoolWeights(workflow=50, learnings=30, ceremony=10, context=10),
        scoring_weights=ScoringDimensionWeights(),
        response_format="json",
        hooks_enabled=True,
        agents_md_enabled=True,
        include_framework_ref=True,
        include_agent_teams=False,
        include_delegation=True,
        nudge_enabled=True,
        tool_exposure_mode="all",
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
    ),
    "cursor-cli": ClientProfile(
        client_id="cursor-cli",
        display_name="Cursor CLI",
        write_targets=WriteTargets(
            agents_md=True,
            agents_md_primary=True,
            cli_config=True,
            instruction_path="AGENTS.md",
        ),
        instruction_max_lines=250,
        context_window_tokens=128_000,
        ceremony_mode="light",
        ceremony_weights=CeremonyWeights(
            session_start=30,
            deliver=30,
            checkpoint=10,
            learn=20,
            build_check=10,
            review=0,
        ),
        nudge_pool_weights=NudgePoolWeights(workflow=60, learnings=30, ceremony=0, context=10),
        mandatory_phases=["implement", "deliver"],
        scoring_weights=ScoringDimensionWeights(
            outcome=0.55,
            plan_quality=0.05,
            implementation=0.20,
            ceremony=0.05,
            knowledge=0.15,
        ),
        default_model_tier="cloud-sonnet",
        response_format="json",
        hooks_enabled=True,
        agents_md_enabled=True,
        include_framework_ref=False,
        include_agent_teams=False,
        include_delegation=False,
        nudge_enabled=True,
        tool_exposure_mode="standard",
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
    ),
    "codex": _light_profile("codex", "Codex CLI", ".codex/INSTRUCTIONS.md"),
    "copilot": ClientProfile(
        client_id="copilot",
        display_name="GitHub Copilot CLI",
        write_targets=WriteTargets(
            claude_md=False,
            agents_md=True,
            copilot_instructions=True,
            instruction_path=".github/copilot-instructions.md",
        ),
        instruction_max_lines=400,
        context_window_tokens=200_000,
        ceremony_mode="full",
        ceremony_weights=CeremonyWeights(),
        nudge_pool_weights=NudgePoolWeights(),  # defaults: 40/30/20/10
        scoring_weights=ScoringDimensionWeights(),
        response_format="json",
        hooks_enabled=True,
        include_agent_teams=True,
        tool_exposure_mode="all",
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
    ),
    "gemini": ClientProfile(
        client_id="gemini",
        display_name="Google Gemini CLI",
        write_targets=WriteTargets(
            claude_md=False,
            agents_md=True,
            gemini_md=True,
            instruction_path="GEMINI.md",
        ),
        instruction_max_lines=500,
        context_window_tokens=1_000_000,
        ceremony_mode="full",
        ceremony_weights=CeremonyWeights(),  # defaults: 25/25/15/10/10/15
        nudge_pool_weights=NudgePoolWeights(),  # defaults: 40/30/20/10
        scoring_weights=ScoringDimensionWeights(),
        response_format="yaml",
        hooks_enabled=True,
        agents_md_enabled=True,
        include_framework_ref=True,
        include_agent_teams=False,  # uses native .gemini/agents/ instead
        include_delegation=True,
        # Surface control (PRD-CORE-125)
        nudge_enabled=True,
        tool_exposure_mode="all",
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
    ),
    "aider": _light_profile("aider", "Aider", ".aider/instructions.md"),
}


def resolve_client_profile(
    client_id: str,
    model_tier: ModelTier | None = None,
) -> ClientProfile:
    """Resolve a built-in profile, optionally adjusted for model tier.

    Unknown client_ids fall back to claude-code with a warning (F04/FR04).
    Model tier adjustments return a NEW profile via model_copy (F06).
    """
    profile = _PROFILES.get(client_id)
    if profile is None:
        if client_id == "cursor":
            logger.warning(
                "unknown_client_id_fallback",
                client_id=client_id,
                fallback="claude-code",
                message=(
                    "The bare 'cursor' profile ID was removed in Sprint 91. "
                    "Use 'cursor-ide' for interactive Cursor IDE sessions or "
                    "'cursor-cli' for headless cursor-agent CI runs. "
                    "Update your target_platforms configuration accordingly."
                ),
            )
        else:
            logger.warning(
                "unknown_client_id_fallback",
                client_id=client_id,
                fallback="claude-code",
            )
        profile = _PROFILES["claude-code"]

    if model_tier is not None and model_tier in _TIER_OVERRIDES:
        overrides = _TIER_OVERRIDES[model_tier]
        profile = profile.model_copy(update=overrides)

    return profile
