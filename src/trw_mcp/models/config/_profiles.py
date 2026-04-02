"""Built-in client profile registry and resolution.

Five profiles (claude-code, opencode, cursor, codex, aider) with
eval-data-calibrated ceremony and scoring weights. Unknown client IDs
fall back to claude-code with a structured warning.
"""

from __future__ import annotations

import structlog

from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    ModelTier,
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
        mandatory_phases=_LIGHT_PHASES,
        scoring_weights=_LIGHT_SCORING,
        default_model_tier="local-8b",
        hooks_enabled=False,
        agents_md_enabled=True,
        include_framework_ref=False,
        include_agent_teams=False,
        include_delegation=False,
    )


_PROFILES: dict[str, ClientProfile] = {
    "claude-code": ClientProfile(
        client_id="claude-code",
        display_name="Claude Code",
        write_targets=WriteTargets(claude_md=True, instruction_path=".claude/INSTRUCTIONS.md"),
        ceremony_weights=CeremonyWeights(),  # defaults = claude-code
        scoring_weights=ScoringDimensionWeights(),  # defaults = claude-code
        hooks_enabled=True,
        include_framework_ref=True,
        include_agent_teams=True,
        include_delegation=True,
    ),
    "opencode": _light_profile("opencode", "OpenCode", ".opencode/INSTRUCTIONS.md"),
    "cursor": ClientProfile(
        client_id="cursor",
        display_name="Cursor",
        write_targets=WriteTargets(cursor_rules=True, instruction_path=".cursor/rules/trw-ceremony.mdc"),
        instruction_max_lines=400,
        context_window_tokens=128_000,
        ceremony_weights=CeremonyWeights(),
        scoring_weights=ScoringDimensionWeights(),
        response_format="json",
        hooks_enabled=False,
        include_agent_teams=False,
    ),
    "codex": _light_profile("codex", "Codex CLI", ".codex/INSTRUCTIONS.md"),
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
