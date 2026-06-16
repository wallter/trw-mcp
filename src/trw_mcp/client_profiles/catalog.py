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
    "antigravity-cli",
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
    if targets.antigravitycli_md:
        return "ANTIGRAVITY.md"
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


# ---------------------------------------------------------------------------
# Uninstall surface manifest (PRD-SEC-006 FR07)
#
# Single source of truth for the project-scope surfaces ``trw-mcp uninstall``
# removes, derived from the client-profile registry (the same registry
# init-project / bootstrap write through) so a newly-added profile is cleaned
# up automatically instead of needing a parallel hardcoded list.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UninstallSurface:
    """One filesystem surface a profile (or the framework core) installs.

    Attributes:
        relpath: Path relative to the project root.
        managed_block: When True, the path is a SHARED file (e.g. CLAUDE.md,
            settings.json, AGENTS.md) that TRW only partly owns -- uninstall
            removes the TRW-managed marker block and leaves the rest, instead
            of deleting the file wholesale.
        merged_config: When True, the path is a structured client config file
            (JSON/TOML) TRW deep-merges its MCP server entry into -- e.g.
            ``.gemini/settings.json`` (``mcpServers.trw``) or
            ``.codex/config.toml`` (``[mcp_servers.trw]``). Uninstall strips
            ONLY the TRW server entry and writes the rest back; the file is
            never deleted wholesale (sec-006).
    """

    relpath: str
    managed_block: bool = False
    merged_config: bool = False


# Framework-core surfaces created by init-project regardless of client profile.
# Shared-file surfaces (markers removed, file preserved) carry managed_block=True.
_CORE_SURFACES: tuple[UninstallSurface, ...] = (
    UninstallSurface(".trw"),
    UninstallSurface(".mcp.json"),
    UninstallSurface(".claude/skills"),
    UninstallSurface(".claude/agents"),
    UninstallSurface(".claude/hooks"),
    UninstallSurface(".claude/commands"),
)

# Per-profile config-directory surfaces TRW provisions. Standalone instruction
# files written into a SHARED root file (AGENTS.md, GEMINI.md, CLAUDE.md,
# copilot-instructions.md) are handled as managed blocks so user content is
# preserved (PRD-SEC-006 FR07).
_PROFILE_DIR_SURFACES: dict[str, tuple[UninstallSurface, ...]] = {
    "opencode": (
        UninstallSurface(".opencode/commands"),
        UninstallSurface(".opencode/agents"),
        UninstallSurface(".opencode/skills"),
    ),
    "cursor-ide": (
        UninstallSurface(".cursor/rules"),
        UninstallSurface(".cursor/agents"),
        UninstallSurface(".cursor/commands"),
        UninstallSurface(".cursor/skills"),
        UninstallSurface(".cursor/hooks"),
        UninstallSurface(".cursor/hooks.json"),
    ),
    "cursor-cli": (
        UninstallSurface(".cursor/cli.json"),
        UninstallSurface(".cursor/hooks"),
        UninstallSurface(".cursor/hooks.json"),
    ),
    "codex": (
        UninstallSurface(".codex/config.toml", merged_config=True),
        UninstallSurface(".codex/hooks.json"),
        UninstallSurface(".codex/hooks"),
    ),
    "gemini": (UninstallSurface(".gemini/settings.json", merged_config=True),),
    "copilot": (UninstallSurface(".github/agents"),),
    "aider": (UninstallSurface(".aider.conf.yml"),),
    "antigravity-cli": (UninstallSurface(".antigravitycli"),),
}


def _instruction_surface(profile: ClientProfile) -> UninstallSurface | None:
    """Resolve a profile's root instruction file into an uninstall surface.

    Root markdown instruction files (AGENTS.md, GEMINI.md, ANTIGRAVITY.md,
    .github/copilot-instructions.md, .claude/INSTRUCTIONS.md) are shared with
    user content, so they are managed-block surfaces. ``.cursor/rules/*.mdc``
    is a TRW-only file (not shared) and is already covered by the per-profile
    directory surfaces, so it is skipped here.
    """
    path = profile.write_targets.instruction_path
    if not path or path.endswith(".mdc"):
        return None
    return UninstallSurface(path, managed_block=True)


def uninstall_surfaces() -> tuple[UninstallSurface, ...]:
    """Return the de-duplicated set of project-scope uninstall surfaces.

    Driven entirely by the client-profile registry (all profiles in
    ``_CLIENT_ORDER``) plus framework-core surfaces -- the manifest
    init-project writes through. De-duplication is by ``relpath`` (managed-block
    wins over plain removal when both appear) and preserves first-seen order so
    the surface set is deterministic.
    """
    seen: dict[str, UninstallSurface] = {}

    def _add(surface: UninstallSurface) -> None:
        existing = seen.get(surface.relpath)
        if existing is None or (surface.managed_block and not existing.managed_block):
            seen[surface.relpath] = surface

    for surface in _CORE_SURFACES:
        _add(surface)

    for client_id in _CLIENT_ORDER:
        for surface in _PROFILE_DIR_SURFACES.get(client_id, ()):
            _add(surface)
        instr = _instruction_surface(resolve_client_profile(client_id))
        if instr is not None:
            _add(instr)

    return tuple(seen.values())
