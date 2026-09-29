"""IDE-specific installers — extracted from _init_project.py for module-size compliance.

Belongs to the ``_init_project.py`` facade. Re-exported there for back-compat
with `_client_integrations.py` which imports the per-IDE installers via
the parent.

Per-IDE artifact installers + the shared `_extend_result` helper + `_CopilotInstaller`
Protocol + `_run_copilot_installer` runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import structlog

from trw_mcp.models.typed_dicts import BootstrapFileResult

logger = structlog.get_logger(__name__)


class _CopilotInstaller(Protocol):
    """Callable protocol for Copilot artifact installers."""

    def __call__(
        self,
        target_dir: Path,
        *,
        force: bool = False,
    ) -> BootstrapFileResult | dict[str, list[str]]: ...


def _extend_result(
    result: dict[str, list[str]],
    update: BootstrapFileResult | dict[str, list[str]],
    *,
    include_updated: bool = False,
) -> None:
    """Merge a bootstrap sub-result into the main init payload."""
    result["created"].extend(update.get("created", []))
    if include_updated:
        result["created"].extend(update.get("updated", []))
    result["skipped"].extend(update.get("preserved", []))
    result["errors"].extend(update.get("errors", []))
    # A sub-installer's warnings (e.g. the global antigravity config it refused to replace) must reach the caller.
    # A sub-installer's removals (e.g. a withdrawn CC-03 hook moved to .trw/trash) are reported too.
    for key in ("warnings", "removed", "trashed"):
        items = update.get(key)
        if isinstance(items, list):
            result.setdefault(key, []).extend(items)


def _install_opencode_artifacts(
    target_dir: Path,
    *,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Install OpenCode-specific bootstrap artifacts (including distill channels)."""
    from ._opencode import generate_agents_md, generate_opencode_config

    oc_result = generate_opencode_config(target_dir, force=force)
    _extend_result(result, oc_result, include_updated=True)

    from ._opencode import (
        generate_opencode_instructions,
        install_opencode_commands,
        install_opencode_skills,
    )

    try:
        instructions_result = generate_opencode_instructions(target_dir, force=force)
        _extend_result(result, instructions_result, include_updated=True)
    except Exception as exc:  # justified: fail-open, INSTRUCTIONS.md update is best-effort
        result.setdefault("warnings", []).append(f".opencode/INSTRUCTIONS.md generation skipped: {exc}")

    try:
        agents_result = generate_agents_md(target_dir, force=force, client_id="opencode")
        _extend_result(result, agents_result, include_updated=True)
    except Exception as exc:  # justified: fail-open, AGENTS.md generation is best-effort
        result.setdefault("warnings", []).append(f"AGENTS.md generation skipped: {exc}")

    _extend_result(result, install_opencode_commands(target_dir, force=force))
    _extend_result(result, install_opencode_skills(target_dir, force=force))

    # Distill channel bootstrap (FR41-FR43)
    try:
        from ._opencode_distill_channels import install_opencode_distill_channels

        dc_result = install_opencode_distill_channels(target_dir)
        # install_opencode_distill_channels returns a different shape — absorb errors only
        errors = dc_result.get("errors")
        if isinstance(errors, list):
            result["errors"].extend(errors)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(f"opencode distill channels skipped: {exc}")


def _install_cursor_artifacts(
    target_dir: Path,
    *,
    force: bool,
    result: dict[str, list[str]],
    ide_targets: list[str] | None = None,
) -> None:
    """Install Cursor-specific bootstrap artifacts (cursor-ide and/or cursor-cli).

    Shared steps (hooks.json legacy, rules.mdc, mcp.json) run once for either
    surface.  CLI-specific generators (cli.json, AGENTS.md, 5-event hook subset)
    are gated on "cursor-cli" in *ide_targets*.

    PRD-CORE-137-FR07: dispatcher wiring.
    """
    from ._cursor import generate_cursor_mcp_config, generate_cursor_rules_mdc
    from ._update_project import _extract_trw_section_content

    resolved_targets = ide_targets or []

    # Shared: .cursor/mcp.json (run once for either surface)
    _extend_result(result, generate_cursor_mcp_config(target_dir, force=force))

    # IDE-specific artifacts (PRD-CORE-136-FR03, FR04, FR05, FR06, FR08)
    if "cursor-ide" in resolved_targets:
        from ._cursor_ide import (
            generate_cursor_ide_commands,
            generate_cursor_ide_hooks,
            generate_cursor_ide_skills,
        )

        # FR06: .cursor/rules/trw-ceremony.mdc (IDE primary write target)
        try:
            _extend_result(
                result,
                generate_cursor_rules_mdc(
                    target_dir,
                    _extract_trw_section_content(),
                    client_id="cursor-ide",
                    force=force,
                ),
                include_updated=True,
            )
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/rules/trw-ceremony.mdc generation skipped: {exc}")

        # FR05: .cursor/commands/trw-*.md
        try:
            _extend_result(result, generate_cursor_ide_commands(target_dir), include_updated=True)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/commands/ generation skipped: {exc}")

        # FR04: .cursor/skills/<name>/
        try:
            _extend_result(result, generate_cursor_ide_skills(target_dir, force=force), include_updated=True)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/skills/ generation skipped: {exc}")

        # FR08: 8-event hook set + .cursor/hooks/trw-*.sh
        try:
            _extend_result(result, generate_cursor_ide_hooks(target_dir, force=force), include_updated=True)
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f".cursor/hooks/ generation skipped: {exc}")

    # CLI-specific artifacts (PRD-CORE-137-FR03, FR04, FR05, FR08a)
    if "cursor-cli" in resolved_targets:
        _install_cursor_cli_artifacts(target_dir, force=force, result=result, ide_targets=resolved_targets)

    # Distill channel bootstrap (FR41-FR43)
    try:
        from ._cursor_distill_channels import install_cursor_distill_channels

        dc_result = install_cursor_distill_channels(target_dir, force=force)
        _extend_result(result, dc_result)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(f"cursor distill channels skipped: {exc}")


def _install_cursor_cli_artifacts(
    target_dir: Path,
    *,
    force: bool,
    result: dict[str, list[str]],
    ide_targets: list[str] | None = None,
) -> None:
    """Install cursor-cli-only artifacts (PRD-CORE-137-FR03, FR04, FR05, FR08a).

    Called from ``_install_cursor_artifacts`` when cursor-cli is in ide_targets.
    Fail-open: each generator is wrapped in try/except so one failure doesn't
    abort the others.

    *ide_targets* is the resolved selection, needed because ``.cursor/rules/
    trw-ceremony.mdc`` is a SHARED file both cursor surfaces read.
    """

    from ._cursor_cli import (
        generate_cursor_cli_agents_md,
        generate_cursor_cli_config,
        generate_cursor_cli_hooks,
    )

    # FR03: .cursor/cli.json permissions (also emits TTY reminder via FR08a)
    try:
        cli_result = generate_cursor_cli_config(target_dir, force=force)
        _extend_result(result, cli_result, include_updated=True)
    except Exception as exc:  # justified: fail-open, cli.json update is best-effort
        result.setdefault("warnings", []).append(f".cursor/cli.json generation skipped: {exc}")

    # PRD-CORE-301-FR14: cursor-cli auto-loads both .cursor/rules and AGENTS.md, so it
    # writes the protocol to AGENTS.md only (below). The rule file is cursor-ide's; an
    # older cursor-cli copy is reported by update and doctor, never deleted
    # (_retired_artifacts, the PRD-INFRA-200 FR05 precedent).
    # FR04: AGENTS.md with TRW sentinel block
    try:
        # AGENTS.md is cursor-cli's one carrier (PRD-CORE-301-FR14): it links the shared
        # .trw/INSTRUCTIONS.md (PRD-CORE-341), whose body comes from the one renderer
        # every writer uses, so this installer and `instructions sync` never rewrite each other.
        agents_result = generate_cursor_cli_agents_md(target_dir, force=force)
        _extend_result(result, agents_result, include_updated=True)
    except Exception as exc:  # justified: fail-open, AGENTS.md update is best-effort
        result.setdefault("warnings", []).append(f"AGENTS.md (cursor-cli) generation skipped: {exc}")

    # FR05: 5-event CLI hook subset (composes shared helpers; idempotent with IDE pass)
    try:
        hooks_result = generate_cursor_cli_hooks(target_dir, force=force)
        _extend_result(result, hooks_result, include_updated=True)
    except Exception as exc:  # justified: fail-open, hooks.json update is best-effort
        result.setdefault("warnings", []).append(f".cursor/hooks.json (cursor-cli) generation skipped: {exc}")


def _install_codex_artifacts(target_dir: Path, *, force: bool, result: dict[str, list[str]]) -> None:
    """Install Codex-specific bootstrap artifacts."""
    from ._codex import (
        codex_hooks_enabled,
        codex_hooks_review_warning,
        generate_codex_config,
        generate_codex_hooks,
        install_codex_skills,
    )
    from ._opencode import generate_codex_instructions

    _extend_result(result, generate_codex_config(target_dir, force=force), include_updated=True)

    if codex_hooks_enabled(target_dir):
        hooks_result = generate_codex_hooks(target_dir, force=force)
        _extend_result(result, hooks_result, include_updated=True)
        if hooks_result.get("created") or hooks_result.get("updated"):
            result.setdefault("warnings", []).append(codex_hooks_review_warning())

    _extend_result(result, install_codex_skills(target_dir, force=force), include_updated=True)

    try:
        instructions_result = generate_codex_instructions(target_dir, force=force)
        _extend_result(result, instructions_result, include_updated=True)
    except Exception as exc:  # justified: fail-open, INSTRUCTIONS.md update is best-effort
        result.setdefault("warnings", []).append(f".codex/INSTRUCTIONS.md generation skipped: {exc}")

    # Distill channel bootstrap (FR41-FR43)
    try:
        from ._codex_distill_channels import install_codex_distill_channels

        dc_result = install_codex_distill_channels(target_dir, force=force)
        _extend_result(result, dc_result)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(f"codex distill channels skipped: {exc}")


def _run_copilot_installer(
    result: dict[str, list[str]],
    label: str,
    installer: _CopilotInstaller,
    target_dir: Path,
    *,
    force: bool,
) -> None:
    """Run a single Copilot installer with best-effort warning capture."""
    try:
        _extend_result(result, installer(target_dir, force=force))
    except Exception as exc:  # justified: fail-open
        result.setdefault("warnings", []).append(f"{label} generation skipped: {exc}")


def _install_copilot_artifacts(target_dir: Path, *, force: bool, result: dict[str, list[str]]) -> None:
    """Install Copilot-specific bootstrap artifacts."""
    from ._copilot import (
        generate_copilot_hooks,
        generate_copilot_instructions,
        generate_copilot_path_instructions,
        install_copilot_skills,
    )

    installers = (
        ("copilot-instructions.md", generate_copilot_instructions),
        ("copilot path instructions", generate_copilot_path_instructions),
        ("copilot hooks", generate_copilot_hooks),
        ("copilot skills", install_copilot_skills),
    )
    for label, installer in installers:
        _run_copilot_installer(result, label, installer, target_dir, force=force)

    # Distill channel bootstrap (FR41-FR43)
    try:
        from ._copilot_distill_channels import install_copilot_distill_channels

        dc_result = install_copilot_distill_channels(target_dir, force=force)
        _extend_result(result, dc_result)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(f"copilot distill channels skipped: {exc}")


def _install_antigravity_artifacts(target_dir: Path, *, force: bool, result: dict[str, list[str]]) -> None:
    """Install Antigravity CLI-specific bootstrap artifacts."""
    from ._antigravity_cli import (
        generate_antigravity_instructions,
        generate_antigravity_mcp_config,
    )

    installers = (
        ("ANTIGRAVITY.md", generate_antigravity_instructions),
        ("antigravity MCP config", generate_antigravity_mcp_config),
    )
    for label, installer in installers:
        _run_copilot_installer(result, label, installer, target_dir, force=force)

    # Distill channel bootstrap (FR41-FR43)
    try:
        from ._antigravity_distill_channels import install_antigravity_distill_channels

        dc_result = install_antigravity_distill_channels(target_dir, force=force)
        _extend_result(result, dc_result)
    except Exception as exc:  # justified: fail-open, distill channels are additive
        result.setdefault("warnings", []).append(f"antigravity distill channels skipped: {exc}")
