"""Built-in client profile registry and resolution.

Seven profiles (claude-code, opencode, cursor-ide, cursor-cli, codex, copilot,
antigravity-cli) with eval-data-calibrated ceremony and scoring weights.
Unknown client IDs fall back to claude-code with a structured warning.

Migration notes:
- The bare ``cursor`` profile ID was removed in Sprint 91. Use ``cursor-ide``
  for interactive Cursor IDE or ``cursor-cli`` for headless ``cursor-agent``
  CI runs.
- ``aider`` was retired 2026-07-11 (it never had an adapter).
  ``resolve_client_profile`` still accepts it — it resolves to the claude-code
  fallback with a single ``client_profile_retired`` warning so no tool crashes
  on a stale ``target_platforms: [aider]`` config.
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
    writes_shared_agents_md: bool = True,
) -> ClientProfile:
    """Construct a light-mode profile with eval-calibrated defaults.

    ``on_transition`` (PRD-INTENT-002 FR04/FR05b): opencode keeps the safe
    ``require_reconnect`` default (its cache is not invalidated automatically);
    codex uses ``silent`` (phase set at session start, no intra-session change).
    """
    return ClientProfile(
        client_id=client_id,
        display_name=display_name,
        # PRD-CORE-240-FR04, resolved per client by operator decision 2026-07-28.
        #
        # opencode: WITHDRAWN (writes_shared_agents_md=False). It owns
        # .opencode/INSTRUCTIONS.md and, since the FR05 fix, that file is actually
        # referenced from opencode.json's `instructions` array. PRD-CORE-074's
        # mandate predates that fix, when AGENTS.md was opencode's only carrier.
        #
        # codex: RETAINED. Its capability appendix (PRD-CORE-218-FR06) has nowhere
        # else to go. model_instructions_file is single-valued and points at
        # .codex/INSTRUCTIONS.md, which PRD-QUAL-113-FR03 caps at 2,025 bytes (the
        # appendix measures 5,043). project_doc_fallback_filenames is not a third
        # slot: per official Codex docs it lists filenames checked only WHEN
        # AGENTS.md IS ABSENT, so a file registered there is read conditionally at
        # best. Freeing codex's AGENTS.md requires raising the QUAL-113 cap.
        write_targets=WriteTargets(agents_md=writes_shared_agents_md, instruction_path=instruction_path),
        instruction_max_lines=200,
        context_window_tokens=32_000,
        ceremony_mode="light",
        ceremony_weights=_LIGHT_CEREMONY,
        nudge_pool_weights=NudgePoolWeights(workflow=60, learnings=30, ceremony=0, context=10),
        mandatory_phases=_LIGHT_PHASES,
        scoring_weights=_LIGHT_SCORING,
        default_model_tier=default_model_tier,
        hooks_enabled=False,
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
    "opencode": _light_profile(
        "opencode",
        "OpenCode",
        ".opencode/INSTRUCTIONS.md",
        writes_shared_agents_md=False,  # PRD-CORE-240-FR04 (operator decision)
    ),
    "cursor-ide": ClientProfile(
        client_id="cursor-ide",
        display_name="Cursor IDE",
        write_targets=WriteTargets(
            cursor_rules=True,
            # cursor-ide has its own TRW-owned carrier (instruction_path below), so
            # it must not also claim AGENTS.md. 2ca279054f flipped copilot and
            # antigravity-cli to False and missed this one, while asserting "only
            # cursor-cli has TRW text in a file the user owns".
            #
            # Two live consumers read the flag and both did the wrong thing with a
            # True: the AGENTS.md orphan-strip DECLINED, so the migration cleanup
            # could never run in a project listing cursor-ide; and
            # trw_instructions_sync(client="cursor-ide") CREATED a 5.7 KB AGENTS.md
            # in a project that had none. A codex+cursor-ide project had its codex
            # block rewritten rather than removed — the injection PRD-CORE-240-FR04
            # forbids in terms that leave no room.
            #
            # cursor-cli keeps agents_md=True and that is correct: AGENTS.md IS its
            # instruction_path. The distinction is whether the client has somewhere
            # of its own to read from, not whether it is a Cursor surface.
            agents_md=False,
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
        # AGENTS.md is this client's instruction carrier -- see PRD-CORE-242 and
        # PRD-CORE-240-FR03. It was described here as its ONLY carrier, which is
        # false and was TRW's own omission stated as a vendor limitation: Cursor
        # documents that "the CLI agent supports the same rules system as the
        # editor" (`.cursor/rules`) and that "the CLI also reads AGENTS.md and
        # CLAUDE.md at the project root". TRW simply generated the rule file for
        # cursor-ide only. It now generates it for cursor-cli too.
        #
        # `claude_md` stays False deliberately, and it is NOT a claim that the CLI
        # ignores CLAUDE.md -- it does read it. The flag governs whether TRW WRITES
        # there, and a second copy of the protocol in a third file is what this
        # work removes. Do not "correct" it to True on the strength of the reader
        # list alone.
        #
        # `agents_md` stays TRUE, and this is the LAST client for which TRW writes
        # into a user-owned file. codex, copilot, opencode and antigravity-cli were
        # all withdrawn once they had a carrier the vendor documents. cursor-cli is
        # held back by one unverified fact: whether cursor-agent honours
        # `alwaysApply` in `.cursor/rules/*.mdc`. Cursor documents the rules system
        # as shared with the editor and documents `alwaysApply` semantics FOR THE
        # EDITOR, but no primary source states the CLI's metadata handling, and
        # `cursor.com/docs/cli/reference/rules` does not exist. Only blog posts
        # assert it -- the same evidence class that shipped a copilot `@`-include
        # no IDE could resolve. Since AGENTS.md is cursor-cli's only GUARANTEED
        # carrier, a redundant copy is the recoverable error and a missing protocol
        # is not. Withdraw this when a primary source confirms CLI alwaysApply.
        # Source: cursor.com/docs/cli/using
        write_targets=WriteTargets(
            agents_md=True,
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
        # WITHDRAWN (PRD-CORE-240-FR04). codex owns `.codex/INSTRUCTIONS.md`,
        # which `.codex/config.toml` points at via `model_instructions_file`, so
        # it needs nothing from the shared AGENTS.md — a file the USER owns.
        # It was kept only because PRD-QUAL-113-FR03 capped the codex file at
        # 2,025 bytes, which forced the generic workflow and the capability
        # appendix to live in AGENTS.md. That cap was a token budget, not a
        # vendor limit, and its own premise was "AGENTS.md owns generic
        # workflow" — so removing the injection removes the reason for the cap.
        # The full protocol now renders into codex's own file.
        writes_shared_agents_md=False,
        default_model_tier="balanced",
        nudge_enabled=True,
        on_transition="silent",
    ),
    "copilot": ClientProfile(
        client_id="copilot",
        display_name="GitHub Copilot CLI",
        # "none" — and the reason is a SURFACE split, not an absence.
        #
        # This was briefly set to `at_path_repo_relative` on the strength of the
        # Copilot *CLI* docs, which do document `@relpath` includes. But this one
        # profile serves both surfaces: it also writes `.vscode/mcp.json`, and
        # `docs/CLIENT-PROFILES.md` describes it as covering GitHub Copilot
        # generally. GitHub's repository-instructions docs and VS Code's own
        # custom-instructions docs describe NO file-inclusion syntax for
        # `.github/copilot-instructions.md` — only inline Markdown, with links
        # being references a human follows rather than content that is pulled in.
        #
        # So an `@` line there is a dangling literal for every Copilot Chat user:
        # a file that exists, parses, reports success and carries nothing. That
        # is strictly worse than the injection it replaced (P5), which is exactly
        # what PRD-CORE-240 exists to prevent — so the block stays inline in the
        # always-on file, which IS "automatically included in every chat request".
        #
        # The include-free way to externalize this is
        # `.github/instructions/*.instructions.md` with `applyTo: "**"` ("Use `**`
        # to apply to all files"), a TRW-owned file Copilot loads itself — the
        # same config-registered shape opencode and codex use. TRW already writes
        # that directory, so it is a small addition rather than new machinery.
        # Sources: docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions
        #          code.visualstudio.com/docs/copilot/customization/custom-instructions
        #          docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-custom-instructions (CLI only)
        instruction_import_syntax="none",
        # AGENTS.md WITHDRAWN (PRD-CORE-240-FR04). Copilot does read AGENTS.md —
        # VS Code documents it as always-on alongside copilot-instructions.md —
        # but TRW already writes TWO carriers of its own for this client: the
        # always-on `.github/copilot-instructions.md` (which states the deliver
        # gate) and `.github/instructions/trw-ceremony.instructions.md` with
        # `applyTo: "**"` (which carries the full protocol). A third copy in a
        # file the USER owns buys nothing and is what this PRD removes.
        write_targets=WriteTargets(
            claude_md=False,
            agents_md=False,
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
            # WITHDRAWN (PRD-CORE-240-FR04). Antigravity's documented
            # workspace-rule path is `.agents/rules/`, which TRW now writes
            # and which carries the full protocol. AGENTS.md is read too, but
            # it is a file the USER owns and a third copy buys nothing.
            agents_md=False,
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
        include_framework_ref=True,
        include_delegation=True,
        nudge_enabled=True,
        learning_recall_enabled=True,
        mcp_instructions_enabled=True,
        skills_enabled=True,
    ),
}

# Retired client identifiers (2026-07-11): aider never had a TRW adapter. It
# resolves to the claude-code fallback with a single ``client_profile_retired``
# warning so a stale ``target_platforms: [aider]`` config never crashes a tool.
_RETIRED_PROFILES: frozenset[str] = frozenset({"aider"})


def resolve_client_profile(
    client_id: str,
    model_tier: ModelTier | None = None,
) -> ClientProfile:
    """Resolve a built-in profile, optionally adjusted for model tier.

    Unknown client_ids fall back to claude-code with a warning (F04/FR04).
    Retired client_ids (``aider``) fall back to claude-code with a single
    ``client_profile_retired`` warning so no tool crashes on a stale
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
                    "and resolves to the claude-code fallback. Pick a supported "
                    "client profile and update your target_platforms configuration."
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
