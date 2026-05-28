# Parent facade: bootstrap/_template_updater.py
"""IDE-specific artifact update logic.

Extracted from ``_template_updater.py`` to keep the facade focused on
CLAUDE.md marker management and framework file copying.  All public
names are re-exported from ``_template_updater.py`` so existing import
paths are preserved.

Covers:
- OpenCode artifact updates (FR15)
- Cursor artifact updates (FR05, FR06, FR07)
- Config target_platforms patching
- CLAUDE.md sync (learnings promotion during update)
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import structlog

from ._ide_targets_finalize import (
    _LEGACY_PROFILE_RENAMES as _LEGACY_PROFILE_RENAMES,
)
from ._ide_targets_finalize import (
    _run_claude_md_sync as _run_claude_md_sync,
)
from ._ide_targets_finalize import (
    _update_config_target_platforms as _update_config_target_platforms,
)
from ._utils import _minimal_claude_md, resolve_ide_targets

logger = structlog.get_logger(__name__)

# CLAUDE.md markers — needed by _extract_trw_section_content and _run_claude_md_sync
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"

_SUB_RESULT_KEYS = ("created", "updated", "preserved", "errors")

_WRITE_TARGET_RENDERERS: dict[str, tuple[str, ...]] = {
    "claude_md": ("_run_claude_md_sync",),
    "agents_md": (
        "_update_opencode_artifacts",
        "_update_codex_artifacts",
        "_update_cursor_cli_artifacts",
        "_update_copilot_artifacts",
        "_update_gemini_artifacts",
        "_update_antigravity_artifacts",
    ),
    "agents_md_primary": ("_update_cursor_cli_artifacts",),
    "cli_config": ("_update_cursor_cli_artifacts",),
    "cursor_rules": ("_update_cursor_artifacts",),
    "copilot_instructions": ("_update_copilot_artifacts",),
    "gemini_md": ("_update_gemini_artifacts",),
    "antigravitycli_md": ("_update_antigravity_artifacts",),
}
_UNSUPPORTED_WRITE_TARGETS: dict[str, str] = {}


def write_target_renderer_coverage() -> dict[str, tuple[str, ...] | str]:
    """Return renderer/update coverage for every supported WriteTargets boolean."""
    return {
        **_WRITE_TARGET_RENDERERS,
        **{flag: f"unsupported:{reason}" for flag, reason in _UNSUPPORTED_WRITE_TARGETS.items()},
    }


def _absorb_sub_result(
    parent: dict[str, list[str]],
    child: Mapping[str, object],
) -> None:
    """Merge a sub-result payload into *parent* for all standard keys.

    Accepts both ``dict[str, list[str]]`` (from copilot/gemini generators)
    and ``BootstrapFileResult`` TypedDicts (from cursor-ide/opencode generators).
    The ``Mapping[str, object]`` signature satisfies mypy for both caller types
    via contravariance; the ``isinstance`` guard ensures runtime correctness.

    Only keys in ``_SUB_RESULT_KEYS`` are merged; unknown keys are ignored.
    """
    for key in _SUB_RESULT_KEYS:
        items = child.get(key)
        if not isinstance(items, list):
            continue
        parent.setdefault(key, []).extend(items)


# ---------------------------------------------------------------------------
# OpenCode update helper (FR15)
# ---------------------------------------------------------------------------


def _update_opencode_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update opencode artifacts when opencode is detected (FR15).

    Checks IDE targets and, when opencode is included, calls
    ``generate_opencode_config()`` to smart-merge ``opencode.json`` and
    ``generate_agents_md()`` to sync ``AGENTS.md``.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._opencode import (
        detect_model_family,
        generate_agents_md,
        generate_opencode_config,
        generate_opencode_instructions,
        install_opencode_agents,
        install_opencode_commands,
        install_opencode_skills,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "opencode" not in ide_targets:
        return

    # Update opencode.json (smart merge)
    try:
        oc_result = generate_opencode_config(target_dir)
        result["created"].extend(oc_result.get("created", []))
        result["updated"].extend(oc_result.get("updated", []))
        result["errors"].extend(oc_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, opencode update is best-effort
        result.setdefault("warnings", []).append(f"opencode.json update skipped: {exc}")
        return

    # Update AGENTS.md with platform-generic TRW section
    try:
        from trw_mcp.models.config import get_config
        from trw_mcp.state.claude_md._static_sections import (
            render_agents_trw_section,
            render_minimal_protocol,
        )

        _cfg = get_config()
        if _cfg.effective_ceremony_mode == "light":
            agents_section = render_minimal_protocol()
        else:
            agents_section = render_agents_trw_section()
        agents_result = generate_agents_md(target_dir, agents_section)
        result["created"].extend(agents_result.get("created", []))
        result["updated"].extend(agents_result.get("updated", []))
        result["errors"].extend(agents_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, AGENTS.md update is best-effort
        result.setdefault("warnings", []).append(f"AGENTS.md update skipped: {exc}")

    # Update .opencode/INSTRUCTIONS.md with model-specific content (FR01)
    try:
        opencode_path = target_dir / "opencode.json"
        model_family = "generic"
        if opencode_path.exists():
            import json

            try:
                opencode_data = json.loads(opencode_path.read_text(encoding="utf-8"))
                model_family = detect_model_family(opencode_data)
            except (json.JSONDecodeError, OSError):
                pass

        instructions_result = generate_opencode_instructions(
            target_dir,
            model_family,
            manifest_hashes=manifest_hashes,
        )
        result["created"].extend(instructions_result.get("created", []))
        result["updated"].extend(instructions_result.get("updated", []))
        result["preserved"].extend(instructions_result.get("preserved", []))
        result["errors"].extend(instructions_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, INSTRUCTIONS.md update is best-effort
        result.setdefault("warnings", []).append(f".opencode/INSTRUCTIONS.md update skipped: {exc}")

    try:
        commands_result = install_opencode_commands(target_dir, manifest_hashes=manifest_hashes)
        result["created"].extend(commands_result.get("created", []))
        result["updated"].extend(commands_result.get("updated", []))
        result["preserved"].extend(commands_result.get("preserved", []))
        result["errors"].extend(commands_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, command update is best-effort
        result.setdefault("warnings", []).append(f".opencode/commands update skipped: {exc}")

    try:
        agents_result = install_opencode_agents(target_dir, manifest_hashes=manifest_hashes)
        result["created"].extend(agents_result.get("created", []))
        result["updated"].extend(agents_result.get("updated", []))
        result["preserved"].extend(agents_result.get("preserved", []))
        result["errors"].extend(agents_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, agent update is best-effort
        result.setdefault("warnings", []).append(f".opencode/agents update skipped: {exc}")

    try:
        skills_result = install_opencode_skills(target_dir, manifest_hashes=manifest_hashes)
        result["created"].extend(skills_result.get("created", []))
        result["updated"].extend(skills_result.get("updated", []))
        result["preserved"].extend(skills_result.get("preserved", []))
        result["errors"].extend(skills_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, skill update is best-effort
        result.setdefault("warnings", []).append(f".opencode/skills update skipped: {exc}")

    # Distill channel bootstrap (FR41-FR43) — extracted to _ide_targets_distill
    from ._ide_targets_distill import _update_opencode_distill_channels
    _update_opencode_distill_channels(target_dir, result)


def _update_codex_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update Codex artifacts when Codex is detected."""
    from ._codex import (
        codex_hooks_enabled,
        codex_hooks_review_warning,
        generate_codex_agents,
        generate_codex_config,
        generate_codex_hooks,
        install_codex_skills,
    )
    from ._opencode import (
        generate_agents_md,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "codex" not in ide_targets:
        return

    try:
        codex_result = generate_codex_config(target_dir)
        result["created"].extend(codex_result.get("created", []))
        result["updated"].extend(codex_result.get("updated", []))
        result["errors"].extend(codex_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, codex update is best-effort
        result.setdefault("warnings", []).append(f".codex/config.toml update skipped: {exc}")

    if codex_hooks_enabled(target_dir):
        try:
            hooks_result = generate_codex_hooks(target_dir)
            result["created"].extend(hooks_result.get("created", []))
            result["updated"].extend(hooks_result.get("updated", []))
            result["preserved"].extend(hooks_result.get("preserved", []))
            result["errors"].extend(hooks_result.get("errors", []))
            if hooks_result.get("created") or hooks_result.get("updated"):
                result.setdefault("warnings", []).append(codex_hooks_review_warning())
        except Exception as exc:  # justified: fail-open, codex update is best-effort
            result.setdefault("warnings", []).append(f".codex/hooks.json update skipped: {exc}")

    try:
        agents_result = generate_codex_agents(target_dir)
        result["created"].extend(agents_result.get("created", []))
        result["updated"].extend(agents_result.get("updated", []))
        result["preserved"].extend(agents_result.get("preserved", []))
        result["errors"].extend(agents_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, codex update is best-effort
        result.setdefault("warnings", []).append(f".codex/agents update skipped: {exc}")

    try:
        skills_result = install_codex_skills(target_dir)
        result["created"].extend(skills_result.get("created", []))
        result["updated"].extend(skills_result.get("updated", []))
        result["preserved"].extend(skills_result.get("preserved", []))
        result["errors"].extend(skills_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, codex update is best-effort
        result.setdefault("warnings", []).append(f".agents/skills update skipped: {exc}")

    try:
        from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

        agents_md_result = generate_agents_md(target_dir, render_codex_trw_section())
        result["created"].extend(agents_md_result.get("created", []))
        result["updated"].extend(agents_md_result.get("updated", []))
        result["errors"].extend(agents_md_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, AGENTS update is best-effort
        result.setdefault("warnings", []).append(f"Codex AGENTS.md update skipped: {exc}")

    try:
        from ._opencode import generate_codex_instructions

        codex_instructions_result = generate_codex_instructions(
            target_dir,
            manifest_hashes=manifest_hashes,
        )
        result["created"].extend(codex_instructions_result.get("created", []))
        result["updated"].extend(codex_instructions_result.get("updated", []))
        result["preserved"].extend(codex_instructions_result.get("preserved", []))
        result["errors"].extend(codex_instructions_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, INSTRUCTIONS.md update is best-effort
        result.setdefault("warnings", []).append(f".codex/INSTRUCTIONS.md update skipped: {exc}")

    # Distill channel bootstrap (FR41-FR43) — extracted to _ide_targets_distill
    from ._ide_targets_distill import _update_codex_distill_channels
    _update_codex_distill_channels(target_dir, result)


# ---------------------------------------------------------------------------
# Copilot update helper (PRD-CORE-127)
# ---------------------------------------------------------------------------


def _update_copilot_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update GitHub Copilot artifacts when Copilot is detected.

    Generates ``copilot-instructions.md``, path-scoped instructions,
    ``hooks.json``, agents, and skills under ``.github/``.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._copilot import (
        generate_copilot_agents,
        generate_copilot_hooks,
        generate_copilot_instructions,
        generate_copilot_path_instructions,
        install_copilot_skills,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "copilot" not in ide_targets:
        return

    try:
        instr_result = generate_copilot_instructions(target_dir)
        _absorb_sub_result(result, instr_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"copilot-instructions.md update skipped: {exc}")

    try:
        path_result = generate_copilot_path_instructions(target_dir)
        _absorb_sub_result(result, path_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"copilot path instructions update skipped: {exc}")

    try:
        hooks_result = generate_copilot_hooks(target_dir)
        _absorb_sub_result(result, hooks_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"copilot hooks.json update skipped: {exc}")

    try:
        agents_result = generate_copilot_agents(target_dir)
        _absorb_sub_result(result, agents_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"copilot agents update skipped: {exc}")

    try:
        skills_result = install_copilot_skills(target_dir)
        _absorb_sub_result(result, skills_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"copilot skills update skipped: {exc}")

    # Distill channel bootstrap (FR41-FR43) — extracted to _ide_targets_distill
    from ._ide_targets_distill import _update_copilot_distill_channels
    _update_copilot_distill_channels(target_dir, result)


# ---------------------------------------------------------------------------
# Gemini CLI update helper
# ---------------------------------------------------------------------------


def _update_gemini_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update Gemini CLI artifacts when Gemini is detected.

    Generates ``GEMINI.md``, ``.gemini/settings.json`` MCP config,
    and ``.gemini/agents/trw-*.md`` subagent definitions.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._gemini import (
        generate_gemini_agents,
        generate_gemini_instructions,
        generate_gemini_mcp_config,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "gemini" not in ide_targets:
        return

    try:
        instr_result = generate_gemini_instructions(target_dir)
        _absorb_sub_result(result, instr_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"GEMINI.md update skipped: {exc}")

    try:
        mcp_result = generate_gemini_mcp_config(target_dir)
        _absorb_sub_result(result, mcp_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"gemini MCP config update skipped: {exc}")

    try:
        agents_result = generate_gemini_agents(target_dir)
        _absorb_sub_result(result, agents_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"gemini agents update skipped: {exc}")


# ---------------------------------------------------------------------------
# Antigravity CLI update helper
# ---------------------------------------------------------------------------


def _update_antigravity_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
    manifest_hashes: dict[str, str] | None = None,
) -> None:
    """Update Antigravity CLI artifacts when Antigravity is detected.

    Generates ``ANTIGRAVITY.md``, ``.antigravitycli/settings.json`` MCP config,
    and ``.antigravitycli/agents/trw-*.md`` subagent definitions.

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._antigravity_cli import (
        generate_antigravity_agents,
        generate_antigravity_instructions,
        generate_antigravity_mcp_config,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "antigravity-cli" not in ide_targets:
        return

    try:
        instr_result = generate_antigravity_instructions(target_dir)
        _absorb_sub_result(result, instr_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"ANTIGRAVITY.md update skipped: {exc}")

    try:
        mcp_result = generate_antigravity_mcp_config(target_dir)
        _absorb_sub_result(result, mcp_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"antigravity MCP config update skipped: {exc}")

    try:
        agents_result = generate_antigravity_agents(target_dir)
        _absorb_sub_result(result, agents_result)
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"antigravity agents update skipped: {exc}")

    # Distill channel bootstrap (FR41-FR43) — extracted to _ide_targets_distill
    from ._ide_targets_distill import _update_antigravity_distill_channels
    _update_antigravity_distill_channels(target_dir, result)


def _extract_trw_section_content() -> str:
    """Extract the content between trw:start and trw:end from _minimal_claude_md."""
    full = _minimal_claude_md()
    start_idx, end_idx = full.find(_TRW_START_MARKER), full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        return full[start_idx + len(_TRW_START_MARKER) : end_idx].strip()
    return ""


# ---------------------------------------------------------------------------
# Cursor update helper (FR05, FR06, FR07)
# ---------------------------------------------------------------------------


def _update_cursor_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
    ide_override: str | None = None,
) -> None:
    """Update Cursor artifacts for cursor-ide and/or cursor-cli surfaces.

    Shared steps (run once regardless of active surfaces):
      - generate_cursor_mcp_config (FR07): .cursor/mcp.json

    cursor-ide specific steps (PRD-CORE-136 FR03-FR06, FR08):
      - generate_cursor_rules_mdc        (FR06): .cursor/rules/trw-ceremony.mdc
      - generate_cursor_ide_subagents    (FR03): .cursor/agents/trw-*.md
      - generate_cursor_ide_commands     (FR05): .cursor/commands/trw-*.md
      - generate_cursor_ide_skills       (FR04): .cursor/skills/<name>/
      - generate_cursor_ide_hooks        (FR08): .cursor/hooks/trw-*.sh + hooks.json

    cursor-cli specific steps (wired via ``_update_cursor_cli_artifacts``):
      - generate_cursor_cli_config       (137-FR03): .cursor/cli.json permissions
      - generate_cursor_cli_agents_md    (137-FR04): AGENTS.md with sentinel block
      - generate_cursor_cli_hooks        (137-FR05): CLI-safe 5-event hook subset
      - TTY/tmux bootstrap reminder      (137-FR08a): appended to result["info"]

    Fail-open: errors are captured in ``result["warnings"]`` so they never
    break the overall update flow.
    """
    from ._cursor import (
        generate_cursor_mcp_config,
        generate_cursor_rules_mdc,
    )

    ide_targets = resolve_ide_targets(target_dir, ide_override=ide_override)
    if "cursor-ide" not in ide_targets and "cursor-cli" not in ide_targets:
        return

    # ------------------------------------------------------------------
    # Shared step: .cursor/mcp.json (FR07)
    # ------------------------------------------------------------------
    try:
        mcp_result = generate_cursor_mcp_config(target_dir)
        _absorb_sub_result(result, mcp_result)
    except Exception as exc:  # justified: fail-open, cursor mcp update is best-effort
        result.setdefault("warnings", []).append(f".cursor/mcp.json update skipped: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # cursor-ide specific steps
    # ------------------------------------------------------------------
    if "cursor-ide" in ide_targets:
        from ._cursor_ide import (
            generate_cursor_ide_commands,
            generate_cursor_ide_hooks,
            generate_cursor_ide_skills,
            generate_cursor_ide_subagents,
        )

        # FR06: .cursor/rules/trw-ceremony.mdc (IDE primary write target)
        try:
            trw_section = _extract_trw_section_content()
            rules_result = generate_cursor_rules_mdc(target_dir, trw_section, client_id="cursor-ide")
            _absorb_sub_result(result, rules_result)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(
                f".cursor/rules/trw-ceremony.mdc update skipped: {type(exc).__name__}: {exc}"
            )

        # FR03: .cursor/agents/trw-*.md
        try:
            sub_result = generate_cursor_ide_subagents(target_dir)
            _absorb_sub_result(result, sub_result)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/agents/ update skipped: {type(exc).__name__}: {exc}")

        # FR05: .cursor/commands/trw-*.md
        try:
            cmd_result = generate_cursor_ide_commands(target_dir)
            _absorb_sub_result(result, cmd_result)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/commands/ update skipped: {type(exc).__name__}: {exc}")

        # FR04: .cursor/skills/<name>/
        try:
            skills_result = generate_cursor_ide_skills(target_dir)
            _absorb_sub_result(result, skills_result)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/skills/ update skipped: {type(exc).__name__}: {exc}")

        # FR08: .cursor/hooks/ (8-event set) + hooks.json
        try:
            hooks_result = generate_cursor_ide_hooks(target_dir)
            _absorb_sub_result(result, hooks_result)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/hooks/ update skipped: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # cursor-ide advisory: tool-ceiling awareness (PRD-CORE-136 NFR)
    # ------------------------------------------------------------------
    if "cursor-ide" in ide_targets:
        # TRW exposes 24 MCP tools. Cursor IDE has a ~40-tool ceiling when
        # combining all MCP servers. If the user has additional MCP servers
        # configured alongside TRW, they may approach or exceed this limit.
        result.setdefault("info", []).append(
            "cursor-ide: TRW exposes 24 MCP tools (tool_exposure_mode=all). "
            "Cursor has a ~40-tool ceiling across all MCP servers — adding "
            "other servers alongside TRW may exhaust the per-turn budget. "
            "See docs/CLIENT-PROFILES.md for mitigation options."
        )

    # ------------------------------------------------------------------
    # cursor-cli specific steps — dispatched to existing helper
    # ------------------------------------------------------------------
    if "cursor-cli" in ide_targets:
        _update_cursor_cli_artifacts(target_dir, result)

    # Distill channel bootstrap (FR41-FR43) — extracted to _ide_targets_distill
    from ._ide_targets_distill import _update_cursor_distill_channels
    _update_cursor_distill_channels(target_dir, result)


def _update_cursor_cli_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Update cursor-cli-specific artifacts (PRD-CORE-137-FR07).

    Called from ``_update_cursor_artifacts`` when cursor-cli is in targets.
    Fail-open: each generator is wrapped in try/except.
    """
    from trw_mcp.state.claude_md._static_sections import render_agents_trw_section

    from ._cursor_cli import (
        generate_cursor_cli_agents_md,
        generate_cursor_cli_config,
        generate_cursor_cli_hooks,
    )

    # FR03: .cursor/cli.json permissions (also emits TTY reminder via FR08a)
    try:
        cli_result = generate_cursor_cli_config(target_dir)
        _absorb_sub_result(result, cli_result)
    except Exception as exc:  # justified: fail-open, cli.json update is best-effort
        result.setdefault("warnings", []).append(f".cursor/cli.json update skipped: {type(exc).__name__}: {exc}")

    # FR04: AGENTS.md with TRW sentinel block
    try:
        trw_section = render_agents_trw_section()
        agents_result = generate_cursor_cli_agents_md(target_dir, trw_section)
        _absorb_sub_result(result, agents_result)
    except Exception as exc:  # justified: fail-open, AGENTS.md update is best-effort
        result.setdefault("warnings", []).append(f"AGENTS.md (cursor-cli) update skipped: {type(exc).__name__}: {exc}")

    # FR05: 5-event CLI hook subset (composes shared helpers; idempotent with IDE pass)
    try:
        hooks_result = generate_cursor_cli_hooks(target_dir)
        _absorb_sub_result(result, hooks_result)
    except Exception as exc:  # justified: fail-open, hooks.json update is best-effort
        result.setdefault("warnings", []).append(
            f".cursor/hooks.json (cursor-cli) update skipped: {type(exc).__name__}: {exc}"
        )
