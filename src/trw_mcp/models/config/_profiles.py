"""Built-in client profile registry and resolution.

Seven profiles (claude-code, opencode, cursor-ide, cursor-cli, codex, copilot,
antigravity-cli) with eval-data-calibrated ceremony and scoring weights.
Unknown client IDs fall back to claude-code with a structured warning.

Migration notes:
- The bare ``cursor`` profile ID was removed in Sprint 91. Use ``cursor-ide``
  for interactive Cursor IDE or ``cursor-cli`` for headless ``cursor-agent``
  CI runs.
- ``gemini`` and ``aider`` were retired 2026-07-11 (Google deprecated the
  Gemini CLI; aider never had an adapter). ``resolve_client_profile`` still
  accepts both — they resolve to the claude-code fallback with a single
  ``client_profile_retired`` warning so no tool crashes on a stale
  ``target_platforms: [gemini]`` config.
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from trw_mcp.models.config._capability import CapabilityTier, ModelTier, normalize_capability_tier
from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    NudgePoolWeights,
    ScoringDimensionWeights,
    WriteTargets,
)
from trw_mcp.models.config._defaults import (
    CAPABILITY_PACKS,
    HIGH_RISK_PACKS,
    KERNEL_TOOLS,
    KEYWORD_PACK_HINTS,
    STANDARD_TASK_PACKS,
)

logger = structlog.get_logger(__name__)

# Shared constants for light-mode profiles (opencode, codex) — DRY (P1-B)
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

# Capability tier adjustments for resolve_client_profile (F06 -- model_copy, not mutate).
# Legacy names remain aliases so existing configs do not break during the v25 transition.
_TIER_OVERRIDES: dict[CapabilityTier, dict[str, object]] = {
    "frontier": {"context_window_tokens": 200_000, "instruction_max_lines": 500},
    "balanced": {"context_window_tokens": 200_000, "instruction_max_lines": 500},
    "local-large": {"context_window_tokens": 128_000, "instruction_max_lines": 350},
    "local-small": {"context_window_tokens": 32_000, "instruction_max_lines": 200},
}


def _light_profile(
    client_id: str,
    display_name: str,
    instruction_path: str,
    *,
    default_model_tier: ModelTier = "local-small",
    nudge_enabled: bool = False,
    on_transition: str = "require_reconnect",
) -> ClientProfile:
    """Construct a light-mode profile with eval-calibrated defaults.

    ``on_transition`` (PRD-INTENT-002 FR04/FR05b): opencode keeps the safe
    ``require_reconnect`` default (its cache is not invalidated automatically);
    codex uses ``silent`` (phase set at session start, no intra-session change).
    """
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
        default_model_tier=default_model_tier,
        hooks_enabled=False,
        agents_md_enabled=True,
        include_framework_ref=False,
        include_delegation=False,
        # Surface control (PRD-CORE-125)
        nudge_enabled=nudge_enabled,
        learning_recall_enabled=True,
        mcp_instructions_enabled=False,
        skills_enabled=False,
        on_transition=on_transition,  # type: ignore[arg-type]
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
        include_delegation=True,
        # Surface control (PRD-CORE-125)
        nudge_enabled=True,
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
        # PRD-FIX-078: claude-code exposes MCP tools under mcp__{server}__{tool}
        tool_namespace_prefix="mcp__trw__",
        # PRD-INTENT-002 FR04: claude-code supports tools.listChanged.
        on_transition="notify",
        # PRD-CORE-203 FR01: this client (claude-code) supports `@<path>`
        # in-file imports, so the TRW block can be externalized to
        # `.trw/INSTRUCTIONS.md`.
        instruction_import_syntax="at_path",
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
        include_delegation=True,
        nudge_enabled=True,
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
        on_transition="silent",  # PRD-INTENT-002 FR04
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
        default_model_tier="balanced",
        response_format="json",
        hooks_enabled=True,
        agents_md_enabled=True,
        include_framework_ref=False,
        include_delegation=False,
        nudge_enabled=True,
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
        on_transition="silent",  # PRD-INTENT-002 FR04
    ),
    "codex": _light_profile(
        "codex",
        "Codex CLI",
        ".codex/INSTRUCTIONS.md",
        default_model_tier="balanced",
        nudge_enabled=True,
        on_transition="silent",
    ),
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
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
        on_transition="silent",  # PRD-INTENT-002 FR04
    ),
    "antigravity-cli": ClientProfile(
        client_id="antigravity-cli",
        display_name="Antigravity CLI",
        write_targets=WriteTargets(
            claude_md=False,
            agents_md=True,
            antigravitycli_md=True,
            instruction_path="ANTIGRAVITY.md",
        ),
        instruction_max_lines=500,
        context_window_tokens=1_000_000,
        ceremony_mode="full",
        ceremony_weights=CeremonyWeights(),
        nudge_pool_weights=NudgePoolWeights(),
        scoring_weights=ScoringDimensionWeights(),
        response_format="yaml",
        hooks_enabled=True,  # AG-03 confirmed 2026-05-28: hooks.json PreToolUse schema verified
        agents_md_enabled=True,
        include_framework_ref=True,
        include_delegation=True,
        nudge_enabled=True,
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
    ),
}

# Retired client identifiers (2026-07-11): Google deprecated the Gemini CLI in
# favor of Antigravity CLI; aider never had a TRW adapter. They resolve to the
# claude-code fallback with a single ``client_profile_retired`` warning so a
# stale ``target_platforms: [gemini]`` config never crashes a tool.
_RETIRED_PROFILES: frozenset[str] = frozenset({"gemini", "aider"})


def resolve_client_profile(
    client_id: str,
    model_tier: ModelTier | None = None,
) -> ClientProfile:
    """Resolve a built-in profile, optionally adjusted for model tier.

    Unknown client_ids fall back to claude-code with a warning (F04/FR04).
    Retired client_ids (``gemini``, ``aider``) fall back to claude-code with a
    single ``client_profile_retired`` warning so no tool crashes on a stale
    ``target_platforms`` entry. Model tier adjustments return a NEW profile via
    model_copy (F06).
    """
    profile = _PROFILES.get(client_id)
    if profile is None:
        if client_id in _RETIRED_PROFILES:
            logger.warning(
                "client_profile_retired",
                client_id=client_id,
                fallback="claude-code",
                message=(
                    f"The '{client_id}' client profile was retired 2026-07-11 "
                    "and resolves to the claude-code fallback. "
                    + (
                        "Configure 'antigravity-cli' as the Gemini CLI successor "
                        if client_id == "gemini"
                        else "Pick a supported client profile "
                    )
                    + "and update your target_platforms configuration."
                ),
            )
        elif client_id == "cursor":
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

    if model_tier is not None:
        normalized_tier = normalize_capability_tier(model_tier)
        if normalized_tier in _TIER_OVERRIDES:
            profile = profile.model_copy(
                update={**_TIER_OVERRIDES[normalized_tier], "default_model_tier": normalized_tier}
            )

    return profile


# ---------------------------------------------------------------------------
# PRD-CORE-218-FR03: task-selected capability packs.
#
# Resolution builds a bounded, explainable tool surface from a stable kernel
# plus capability packs selected by (1) the standard task->pack fixture,
# (2) explicit phase rules, and (3) operator grants. Provider identity and
# vague keywords can NEVER grant a high-risk pack (security monotonicity).
# Pack membership is the versioned manifest fixture from PRD-CORE-218 §4,
# owned by ``_defaults`` (single source of truth) and imported here.
# ---------------------------------------------------------------------------

PackGrantSource = Literal["kernel", "task_type", "phase_rule", "operator_grant", "keyword", "denied"]


class PackGrant(BaseModel):
    """One pack decision with the layer that produced it and a reason.

    ``source == "denied"`` records a refused grant (e.g. a high-risk pack a
    vague keyword or provider identity failed to grant).
    """

    model_config = ConfigDict(frozen=True)

    pack: str
    source: PackGrantSource
    reason: str


class CapabilityResolution(BaseModel):
    """Resolved kernel+pack tool surface with per-capability explanations."""

    model_config = ConfigDict(frozen=True)

    task: str
    packs: tuple[str, ...]
    tools: tuple[str, ...]
    tool_count: int
    grants: tuple[PackGrant, ...]
    #: Every resolved tool -> the reason it is present (FR03 "explanation for
    #: every capability"). Keys are exactly ``tools``.
    explanations: dict[str, str]


def resolve_capability_packs(
    task: str,
    *,
    phase_pack_grants: tuple[str, ...] = (),
    operator_pack_grants: tuple[str, ...] = (),
    keyword_hints: tuple[str, ...] = (),
    provider_identity: str | None = None,
) -> CapabilityResolution:
    """Resolve the bounded capability-pack surface for ``task``.

    Layers, highest authority last: standard task fixture, explicit phase rule,
    operator grant, then vague keyword hints. Provider identity never grants a
    pack and vague keywords never grant a high-risk pack; both refusals are
    recorded as ``denied`` grants (FR03 guard).
    """
    grants: list[PackGrant] = [PackGrant(pack="kernel", source="kernel", reason="universal minimal kernel")]
    ordered_packs: list[str] = []

    def _grant(pack: str, source: PackGrantSource, reason: str) -> None:
        if pack not in CAPABILITY_PACKS:
            grants.append(PackGrant(pack=pack, source="denied", reason=f"unknown pack '{pack}'"))
            return
        if pack not in ordered_packs:
            ordered_packs.append(pack)
            grants.append(PackGrant(pack=pack, source=source, reason=reason))

    if task not in STANDARD_TASK_PACKS:
        grants.append(PackGrant(pack="*", source="denied", reason=f"task '{task}' unmapped; kernel only"))
    for pack in STANDARD_TASK_PACKS.get(task, ()):
        _grant(pack, "task_type", f"standard mapping for task '{task}'")

    for pack in phase_pack_grants:
        _grant(pack, "phase_rule", f"explicit phase rule granted '{pack}'")
    for pack in operator_pack_grants:
        _grant(pack, "operator_grant", f"operator granted '{pack}'")

    for keyword in keyword_hints:
        hinted = KEYWORD_PACK_HINTS.get(keyword.strip().lower())
        if hinted is None:
            continue
        if hinted in HIGH_RISK_PACKS:
            grants.append(
                PackGrant(
                    pack=hinted,
                    source="denied",
                    reason=f"vague keyword '{keyword}' cannot grant high-risk pack '{hinted}'",
                )
            )
            continue
        _grant(hinted, "keyword", f"keyword '{keyword}' hinted low-risk pack '{hinted}'")

    if provider_identity is not None:
        grants.append(
            PackGrant(
                pack="*",
                source="denied",
                reason=f"provider identity '{provider_identity}' cannot grant any pack",
            )
        )

    tools: list[str] = list(KERNEL_TOOLS)
    explanations: dict[str, str] = dict.fromkeys(KERNEL_TOOLS, "kernel: always present")
    for pack in ordered_packs:
        for tool in CAPABILITY_PACKS[pack]:
            if tool not in explanations:
                tools.append(tool)
                explanations[tool] = f"pack '{pack}': present via task/phase/operator selection"

    return CapabilityResolution(
        task=task,
        packs=("kernel", *ordered_packs),
        tools=tuple(tools),
        tool_count=len(tools),
        grants=tuple(grants),
        explanations=explanations,
    )
