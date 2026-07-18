"""Runtime-derived registry for client-profile documentation."""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.models.config import resolve_client_profile
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._defaults import DEFAULT_NUDGE_BUDGET_CHARS

# ``gemini`` and ``aider`` were retired 2026-07-11 (Gemini CLI deprecated by
# Google; aider never had an adapter). They are RETAINED in ``_CLIENT_ORDER``
# because ``uninstall_surfaces()`` is keyed by it — existing ``.gemini/`` and
# ``.aider.conf.yml`` installs must remain removable via ``trw-mcp uninstall``
# forever. Presence here means "has uninstall surfaces", NOT "supported": the
# documentation-facing ``build_client_profile_rows`` iterates
# ``_ACTIVE_CLIENT_ORDER`` (retired ids excluded) so retired clients never
# appear as active/documented profiles.
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

# Retired client identifiers — retained in _CLIENT_ORDER only for uninstall
# surface cleanup; excluded from every "supported/documented" consumer.
_RETIRED_CLIENTS: frozenset[str] = frozenset({"gemini", "aider"})

# Active (installable, documented) client order — retired ids removed.
_ACTIVE_CLIENT_ORDER: tuple[str, ...] = tuple(c for c in _CLIENT_ORDER if c not in _RETIRED_CLIENTS)


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
    tool_resolution_mode: str
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
    if targets.claude_md:
        return "CLAUDE.md"
    if targets.antigravitycli_md:
        return "ANTIGRAVITY.md"
    return "AGENTS.md"


def _format_pool_weights(profile: ClientProfile) -> str:
    weights = profile.nudge_pool_weights
    return f"{weights.workflow} / {weights.learnings} / {weights.ceremony} / {weights.context}"


def build_client_profile_rows() -> tuple[ClientProfileDocRow, ...]:
    """Return documentation rows for all active (non-retired) built-in profiles."""
    from trw_mcp.models.config import get_config

    # PRD-CORE-218 FR04: tool exposure is now a single global authority
    # (``tool_resolution_mode``), not a per-profile preset — surface the
    # resolved value uniformly across profile rows.
    tool_resolution_mode = str(getattr(get_config(), "tool_resolution_mode", "standard"))
    rows: list[ClientProfileDocRow] = []
    for client_id in _ACTIVE_CLIENT_ORDER:
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
                tool_resolution_mode=tool_resolution_mode,
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
            (JSON/TOML) TRW deep-merges its own entries into -- e.g.
            ``.gemini/settings.json`` (``mcpServers.trw`` + a managed BeforeTool
            hook), ``.codex/config.toml`` (``[mcp_servers.trw]``), or
            ``.codex/hooks.json`` (TRW-managed hook groups). Uninstall strips
            ONLY the TRW-owned entries and writes the rest back; the file is
            deleted only when nothing user-owned remains (sec-006).
        config_shape: For a ``merged_config`` surface, names the structural
            strategy the uninstall stripper uses. One of ``"mcp-server-map"``
            (JSON ``mcpServers.trw``), ``"codex-toml"`` (TOML
            ``[mcp_servers.trw]``), ``"hook-group-list"`` (JSON ``hooks`` map of
            event -> groups, TRW groups tagged by ``"TRW managed:"``
            description), or ``"gemini-settings"`` (``mcpServers.trw`` plus the
            managed ``hooks.BeforeTool`` block). Empty for non-merged surfaces;
            the stripper falls back to a suffix heuristic when unset.
    """

    relpath: str
    managed_block: bool = False
    merged_config: bool = False
    config_shape: str = ""


# Instruction-file uninstall surfaces for retired clients (2026-07-11). Resolved
# explicitly here rather than via resolve_client_profile(), which now returns the
# claude-code fallback for retired ids — that fallback would surface
# ``.claude/INSTRUCTIONS.md`` and LOSE the GEMINI.md managed-block markers.
# Keeping GEMINI.md here guarantees existing installs stay removable forever.
_RETIRED_INSTRUCTION_SURFACES: dict[str, UninstallSurface] = {
    "gemini": UninstallSurface("GEMINI.md", managed_block=True),
    # aider's pre-retirement _light_profile wrote a managed block into
    # ``.aider/instructions.md`` (retire commit e1466da411 removed the writer but
    # dropped this surface — release-verify 2026-07-17 P1). Existing aider installs
    # must stay strippable forever; managed_block preserves any user content.
    "aider": UninstallSurface(".aider/instructions.md", managed_block=True),
}


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
        # Smart-merged MCP-server map (generate_cursor_mcp_config deep-merges
        # user servers) -- strip only the ``trw`` entry, never delete wholesale.
        UninstallSurface(".cursor/mcp.json", merged_config=True, config_shape="mcp-server-map"),
    ),
    "cursor-cli": (
        UninstallSurface(".cursor/cli.json"),
        UninstallSurface(".cursor/hooks"),
        UninstallSurface(".cursor/hooks.json"),
    ),
    "codex": (
        UninstallSurface(".codex/config.toml", merged_config=True, config_shape="codex-toml"),
        # hooks.json merges TRW hook GROUPS alongside user groups
        # (merge_codex_hooks preserves non-TRW groups) -- strip only TRW groups.
        UninstallSurface(".codex/hooks.json", merged_config=True, config_shape="hook-group-list"),
        UninstallSurface(".codex/hooks"),
    ),
    "gemini": (
        # settings.json carries both mcpServers.trw AND a managed BeforeTool
        # hook block (install_gemini_distill_channels) -- strip both.
        UninstallSurface(".gemini/settings.json", merged_config=True, config_shape="gemini-settings"),
        UninstallSurface(".gemini/agents"),
        UninstallSurface(".gemini/hooks"),
    ),
    "copilot": (
        UninstallSurface(".github/agents"),
        UninstallSurface(".github/skills"),
        # hooks.json merges TRW hook groups alongside user groups
        # (_merge_copilot_hooks preserves non-TRW groups) -- strip only TRW groups.
        UninstallSurface(".github/hooks/hooks.json", merged_config=True, config_shape="hook-group-list"),
        UninstallSurface(".github/hooks/trw-copilot-adapter.sh"),
        # .github/instructions/ is a SHARED GitHub dir users may own -- register
        # only the specific TRW-written path-scoped files, never the dir.
        UninstallSurface(".github/instructions/python-testing.instructions.md"),
        UninstallSurface(".github/instructions/typescript-react.instructions.md"),
    ),
    "aider": (UninstallSurface(".aider.conf.yml"),),
    "antigravity-cli": (
        # settings.json is a smart-merged MCP-server map (preserves user
        # servers) -- strip only the ``trw`` entry, never rmtree the dir.
        UninstallSurface(".antigravitycli/settings.json", merged_config=True, config_shape="mcp-server-map"),
        UninstallSurface(".antigravitycli/agents"),
        # AG-03 PreToolUse hook cleanup. install_before_edit_hook writes BOTH the
        # hooks.json entry AND the hook script under hooks/, so uninstall must
        # remove both or a live TRW PreToolUse hook is left registered.
        #
        # hooks.json here is a FLAT ``{"<event>": [entry, ...]}`` map (see
        # channels/antigravity/_before_edit_hook.py::_merge_hooks_json), NOT the
        # codex/copilot ``{"hooks": {event: [group]}}`` shape with ``"TRW
        # managed:"`` descriptions. The ``hook-group-list`` merged-strip strategy
        # therefore does not match this file and would strip nothing, leaving the
        # hook behind. TRW is the sole writer of .antigravitycli/hooks.json, so
        # whole-file removal (a plain surface, like ``.cursor/hooks.json``) is the
        # correct and only in-module cleanup that guarantees no live hook remains.
        UninstallSurface(".antigravitycli/hooks.json"),
        UninstallSurface(".antigravitycli/hooks"),
    ),
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
        if client_id in _RETIRED_CLIENTS:
            # Retired clients no longer resolve to their own profile
            # (resolve_client_profile returns the claude-code fallback), so use
            # the explicit retired-instruction map to preserve GEMINI.md cleanup.
            retired_instr = _RETIRED_INSTRUCTION_SURFACES.get(client_id)
            if retired_instr is not None:
                _add(retired_instr)
            continue
        instr = _instruction_surface(resolve_client_profile(client_id))
        if instr is not None:
            _add(instr)

    return tuple(seen.values())
