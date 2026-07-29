"""Runtime-derived registry for client-profile documentation."""

from __future__ import annotations

from dataclasses import dataclass

from trw_mcp.models.config import resolve_client_profile
from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._defaults import DEFAULT_NUDGE_BUDGET_CHARS

# ``aider`` was retired 2026-07-11 (it never had a TRW adapter). It is RETAINED
# in ``_CLIENT_ORDER`` because ``uninstall_surfaces()`` is keyed by it —
# existing ``.aider.conf.yml`` installs must remain removable via
# ``trw-mcp uninstall`` forever. Presence here means "has uninstall surfaces",
# NOT "supported": the documentation-facing ``build_client_profile_rows``
# iterates ``_ACTIVE_CLIENT_ORDER`` (retired ids excluded) so retired clients
# never appear as active/documented profiles.
_CLIENT_ORDER: tuple[str, ...] = (
    "claude-code",
    "opencode",
    "cursor-ide",
    "cursor-cli",
    "codex",
    "copilot",
    "antigravity-cli",
    "aider",
)

# Retired client identifiers — retained in _CLIENT_ORDER only for uninstall
# surface cleanup; excluded from every "supported/documented" consumer.
_RETIRED_CLIENTS: frozenset[str] = frozenset({"aider"})

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
            ``.cursor/mcp.json`` (``mcpServers.trw``), ``.codex/config.toml``
            (``[mcp_servers.trw]``), or ``.codex/hooks.json`` (TRW-managed hook
            groups). Uninstall strips ONLY the TRW-owned entries and writes the
            rest back. The file itself is deleted only for the
            ``hook-group-list`` shape, which holds nothing but TRW artifacts;
            server maps and TOML configs belong to the user's client and are
            always preserved, emptied at most (sec-006).
        config_shape: For a ``merged_config`` surface, names the structural
            strategy the uninstall stripper uses. One of ``"mcp-server-map"``
            (JSON ``mcpServers.trw``), ``"codex-toml"`` (TOML
            ``[mcp_servers.trw]``), or ``"hook-group-list"`` (JSON ``hooks`` map
            of event -> groups, TRW groups tagged by ``"TRW managed:"``
            description). Empty for non-merged surfaces; the stripper falls back
            to a suffix heuristic when unset.
    """

    relpath: str
    managed_block: bool = False
    merged_config: bool = False
    config_shape: str = ""


# Instruction-file uninstall surfaces for retired clients (2026-07-11). Resolved
# explicitly here rather than via resolve_client_profile(), which now returns the
# claude-code fallback for retired ids — that fallback would surface claude-code's
# own ``CLAUDE.md`` and LOSE the retired client's instruction file entirely.
# Keeping them here guarantees existing installs stay removable forever.
_RETIRED_INSTRUCTION_SURFACES: dict[str, UninstallSurface] = {
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
    # ``bootstrap/__init__.py::_DATA_FILE_MAP`` installs the bundled
    # ``settings.json`` here and ``_template_updater._merge_settings_json``
    # merges TRW hook entries into an existing one. Every bundled hook command
    # points into ``.claude/hooks/``, which uninstall deletes wholesale — so
    # leaving the entries registered makes every later Claude Code session
    # invoke a script that no longer exists. It is the user's client config
    # (permissions, env, their own hooks), so it is stripped, never deleted.
    UninstallSurface(".claude/settings.json", merged_config=True, config_shape="claude-settings"),
    # PRD-CORE-231: ``bootstrap/_git_hooks.py`` appends a guarded TRW dispatch
    # block to the git ``post-commit`` hook, chaining after any user hook. The
    # block carries its own marker pair (``# >>> trw post-commit ... >>>``),
    # registered in ``_subcommands_uninstall_config._MANAGED_BLOCK_MARKERS``.
    # Note: this covers the default hooks dir only — an install that resolved
    # ``core.hooksPath`` elsewhere writes outside the project-relative manifest.
    UninstallSurface(".git/hooks/post-commit", managed_block=True),
    # Smart-merged MCP-server map, exactly like ``.cursor/mcp.json`` and
    # ``.antigravitycli/settings.json`` below. ``bootstrap/_mcp_json.py::
    # _merge_mcp_json`` merges the ``trw`` key "while preserving all other
    # user-configured servers", so deleting the file wholesale destroys every
    # unrelated server the user configured (github, postgres, ...). Strip only
    # the ``trw`` entry; the file itself is always preserved (a server map is
    # the user's client config), emptied at most.
    UninstallSurface(".mcp.json", merged_config=True, config_shape="mcp-server-map"),
    UninstallSurface(".claude/skills"),
    UninstallSurface(".claude/agents"),
    UninstallSurface(".claude/hooks"),
    UninstallSurface(".claude/commands"),
    # Bundled TRW file copied verbatim by ``_init_project`` (step 7a-1).
    UninstallSurface(".claude/loop.md"),
    # Written by ``_init_project._generate_root_files``. Its own header says
    # "Auto-generated by TRW — manual edits will be overwritten on next
    # trw_deliver()", so TRW owns the whole file, not a block inside it.
    UninstallSurface("REVIEW.md"),
    # ``channels/_gitignore.py`` maintains a ``# TRW:MDC:BEGIN``/``END``
    # section inside the project's own .gitignore (creating the file when
    # absent). Managed block: the user's ignore rules are theirs.
    UninstallSurface(".gitignore", managed_block=True),
)


def _canon_root_surfaces() -> tuple[UninstallSurface, ...]:
    """Canon documents the installer copies to the PROJECT ROOT.

    Derived from the canon registry's install view — the same projection
    ``bootstrap.__init__._DATA_FILE_MAP`` is built from — so adding a
    root-installed canon artifact registers its cleanup automatically.
    Destinations under ``.trw/`` are skipped: the ``.trw`` surface covers them.

    Function-local import: ``canons`` is a heavier subsystem than this registry
    needs at import time, and the call site is already lazy.
    """
    from trw_mcp.canons._views import install_view
    from trw_mcp.canons.registry import load_registry

    return tuple(
        UninstallSurface(destination)
        for _resource, destination in install_view(load_registry())
        if "/" not in destination
    )

# Per-profile config-directory surfaces TRW provisions. Standalone instruction
# files written into a SHARED root file (AGENTS.md, CLAUDE.md, ANTIGRAVITY.md,
# copilot-instructions.md) are handled as managed blocks so user content is
# preserved (PRD-SEC-006 FR07).
_PROFILE_DIR_SURFACES: dict[str, tuple[UninstallSurface, ...]] = {
    "opencode": (
        UninstallSurface(".opencode/commands"),
        UninstallSurface(".opencode/agents"),
        UninstallSurface(".opencode/skills"),
        # ``merge_opencode_json`` adds ``mcp.trw`` while preserving the user's
        # model/agent/instructions keys, so this is a merged config, not a TRW
        # file. The strip also drops the managed ``.opencode/INSTRUCTIONS.md``
        # entry from ``instructions`` — that file is removed above, and a
        # config pointing at a deleted instruction file breaks opencode start-up.
        UninstallSurface("opencode.json", merged_config=True, config_shape="opencode-config"),
    ),
    "cursor-ide": (
        UninstallSurface(".cursor/rules"),
        UninstallSurface(".cursor/agents"),
        UninstallSurface(".cursor/commands"),
        UninstallSurface(".cursor/skills"),
        UninstallSurface(".cursor/hooks"),
        # ``smart_merge_cursor_json`` merges TRW entries into an existing
        # hooks.json and removes only entries whose ``command`` starts with
        # ``.cursor/hooks/trw-`` — "everything else is preserved". As a plain
        # surface this file was deleted wholesale, destroying a Cursor user's
        # own hooks while install had gone out of its way to keep them.
        UninstallSurface(".cursor/hooks.json", merged_config=True, config_shape="cursor-hook-list"),
        # Smart-merged MCP-server map (generate_cursor_mcp_config deep-merges
        # user servers) -- strip only the ``trw`` entry, never delete wholesale.
        UninstallSurface(".cursor/mcp.json", merged_config=True, config_shape="mcp-server-map"),
    ),
    "cursor-cli": (
        UninstallSurface(".cursor/cli.json"),
        UninstallSurface(".cursor/hooks"),
        UninstallSurface(".cursor/hooks.json", merged_config=True, config_shape="cursor-hook-list"),
    ),
    "codex": (
        UninstallSurface(".codex/config.toml", merged_config=True, config_shape="codex-toml"),
        # ``_codex.py`` installs TRW agents here (_CODEX_AGENTS_DIR) and the
        # bundled skill corpus into ``.agents/skills`` (_CODEX_SKILLS_DIR),
        # which ``config.toml``'s ``skills.config`` then points at. Both were
        # unregistered, so a codex project kept the whole TRW corpus after
        # uninstall — the only profile of the four with skills that did.
        UninstallSurface(".codex/agents"),
        UninstallSurface(".agents/skills"),
        # hooks.json merges TRW hook GROUPS alongside user groups
        # (merge_codex_hooks preserves non-TRW groups) -- strip only TRW groups.
        UninstallSurface(".codex/hooks.json", merged_config=True, config_shape="hook-group-list"),
        UninstallSurface(".codex/hooks"),
    ),
    "copilot": (
        UninstallSurface(".github/agents"),
        UninstallSurface(".github/skills"),
        # hooks.json merges TRW hook groups alongside user groups
        # (_merge_copilot_hooks preserves non-TRW groups) -- strip only TRW groups.
        UninstallSurface(".github/hooks/hooks.json", merged_config=True, config_shape="hook-group-list"),
        UninstallSurface(".github/hooks/trw-copilot-adapter.sh"),
        # Distill-channel hook scripts (_copilot_distill_channels.py). Same
        # per-file scoping as the adapter above: .github/hooks may hold hooks
        # the user wrote.
        UninstallSurface(".github/hooks/trw-copilot-distill-hint.sh"),
        UninstallSurface(".github/hooks/lib-copilot-distill-hint.sh"),
        # .github/instructions/ is a SHARED GitHub dir users may own -- register
        # only the specific TRW-written path-scoped files, never the dir.
        UninstallSurface(".github/instructions/python-testing.instructions.md"),
        UninstallSurface(".github/instructions/typescript-react.instructions.md"),
        UninstallSurface(".github/instructions/trw-distill-hotspots.instructions.md"),
        # VS Code MCP config written by the copilot distill channel (C3):
        # ``{"servers": {"trw": ...}}`` -- a different container key from the
        # ``mcpServers`` maps, and .vscode/ is the user's editor config.
        UninstallSurface(".vscode/mcp.json", merged_config=True, config_shape="vscode-server-map"),
    ),
    "aider": (UninstallSurface(".aider.conf.yml"),),
    "antigravity-cli": (
        # settings.json is a smart-merged MCP-server map (preserves user
        # servers) -- strip only the ``trw`` entry, never rmtree the dir.
        UninstallSurface(".antigravitycli/settings.json", merged_config=True, config_shape="mcp-server-map"),
        UninstallSurface(".antigravitycli/agents"),
        # The workspace rule `_antigravity_cli.py` writes (_ANTIGRAVITY_RULES_DIR
        # + _ANTIGRAVITY_RULE_FILENAME). Registered as the FILE, never the
        # directory: `.agents/rules/` is Antigravity's documented workspace-rules
        # folder and belongs to the user, who may keep their own rules beside
        # ours. `.agents/skills` above can be a whole-directory surface because
        # TRW owns that corpus outright; this one it does not.
        UninstallSurface(".agents/rules/trw-ceremony.md"),
        # AG-03 PreToolUse hook cleanup. install_before_edit_hook writes BOTH the
        # hooks.json entry AND the hook script under hooks/, so uninstall must
        # remove both or a live TRW PreToolUse hook is left registered.
        #
        # hooks.json here is a FLAT ``{"<event>": [entry, ...]}`` map (see
        # channels/antigravity/_before_edit_hook.py::_merge_hooks_json), NOT the
        # codex/copilot ``{"hooks": {event: [group]}}`` shape with ``"TRW
        # managed:"`` descriptions, so ``hook-group-list`` matches nothing here
        # and would leave the live hook behind. It was registered as a plain
        # whole-file removal on the premise that TRW is its sole writer —
        # contradicted by ``_merge_hooks_json``, which starts from
        # ``dict(existing)`` and preserves every other event key. The
        # ``antigravity-hook-map`` strategy matches this shape by command path,
        # so the hook is removed without discarding user entries.
        UninstallSurface(".antigravitycli/hooks.json", merged_config=True, config_shape="antigravity-hook-map"),
        UninstallSurface(".antigravitycli/hooks"),
    ),
}


# Shared ROOT instruction files, keyed on the ``WriteTargets`` flag that drives
# the writer rather than on ``instruction_path``. These files hold user content
# alongside a TRW marker block, so they are managed-block surfaces.
#
# ``instruction_path`` is deliberately NOT the key here: it is display//routing
# metadata and does not track what bootstrap writes. claude-code declares
# ``.claude/INSTRUCTIONS.md`` but no writer in the tree ever produces that path
# (``_init_project`` writes ``CLAUDE.md``; the PRD-CORE-203 sidecar is
# ``.trw/INSTRUCTIONS.md``, already covered by the ``.trw`` surface). Deriving
# from the flag registers the file that actually exists and stops uninstall
# claiming ownership of a user-authored ``.claude/INSTRUCTIONS.md``.
_ROOT_INSTRUCTION_SURFACES: tuple[tuple[str, str], ...] = (
    ("claude_md", "CLAUDE.md"),
    ("agents_md", "AGENTS.md"),
    ("copilot_instructions", ".github/copilot-instructions.md"),
    ("antigravitycli_md", "ANTIGRAVITY.md"),
)


def _generated_instruction_relpaths() -> frozenset[str]:
    """Per-client instruction files TRW generates whole (no marker block).

    Imported from the writers themselves so the manifest cannot drift from
    them. Function-local import: ``bootstrap`` imports ``client_profiles``
    (``_file_ops`` -> ``session_identity``), so a module-level import here
    would close a cycle.
    """
    from trw_mcp.bootstrap._opencode_instructions import (
        CODEX_INSTRUCTIONS_REL,
        OPENCODE_INSTRUCTIONS_REL,
    )

    return frozenset({OPENCODE_INSTRUCTIONS_REL.as_posix(), CODEX_INSTRUCTIONS_REL.as_posix()})


def _instruction_surfaces(profile: ClientProfile, generated: frozenset[str]) -> tuple[UninstallSurface, ...]:
    """Resolve a profile's instruction files into uninstall surfaces.

    Two kinds, with different removal contracts:

    * shared root files (CLAUDE.md, AGENTS.md, ANTIGRAVITY.md,
      .github/copilot-instructions.md) -> managed block: strip TRW's markers,
      preserve everything else.
    * TRW-generated per-client files (.opencode/INSTRUCTIONS.md,
      .codex/INSTRUCTIONS.md) -> plain removal. These are rendered whole by
      ``bootstrap/_opencode_instructions.py`` and carry NO markers, so
      classifying them as managed blocks made the strip a silent no-op while
      uninstall still listed them as cleaned surfaces.

    ``.cursor/rules/*.mdc`` is covered by the ``.cursor/rules`` dir surface, and
    any other ``instruction_path`` value has no writer and is not claimed.
    """
    surfaces: list[UninstallSurface] = [
        UninstallSurface(relpath, managed_block=True)
        for flag, relpath in _ROOT_INSTRUCTION_SURFACES
        if getattr(profile.write_targets, flag, False)
    ]
    path = profile.write_targets.instruction_path
    if path in generated:
        surfaces.append(UninstallSurface(path))
    return tuple(surfaces)


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
    for surface in _canon_root_surfaces():
        _add(surface)

    generated = _generated_instruction_relpaths()
    for client_id in _CLIENT_ORDER:
        for surface in _PROFILE_DIR_SURFACES.get(client_id, ()):
            _add(surface)
        if client_id in _RETIRED_CLIENTS:
            # Retired clients no longer resolve to their own profile
            # (resolve_client_profile returns the claude-code fallback), so use
            # the explicit retired-instruction map to preserve their cleanup.
            retired_instr = _RETIRED_INSTRUCTION_SURFACES.get(client_id)
            if retired_instr is not None:
                _add(retired_instr)
            continue
        for instr in _instruction_surfaces(resolve_client_profile(client_id), generated):
            _add(instr)

    return tuple(seen.values())
