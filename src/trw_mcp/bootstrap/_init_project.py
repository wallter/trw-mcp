"""init_project flow — bootstraps TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._utils import (
    _DATA_DIR,
    ProgressCallback,
    _copy_file,
    _default_config,
    _ensure_dir,
    _merge_mcp_json,
    _minimal_claude_md,
    _minimal_review_md,
    _write_if_missing,
    _write_installer_metadata,
    _write_version_yaml,
    resolve_ide_targets,
)

logger = structlog.get_logger(__name__)


def _create_directory_structure(
    target_dir: Path,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Create the TRW directory scaffold inside *target_dir*."""
    from . import _TRW_DIRS

    for rel_dir in _TRW_DIRS:
        _ensure_dir(target_dir / rel_dir, result, on_progress)


def _copy_bundled_data_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy all bundled data files from ``_DATA_FILE_MAP`` to *target_dir*."""
    from . import _DATA_FILE_MAP

    for data_name, dest_rel in _DATA_FILE_MAP:
        _copy_file(_DATA_DIR / data_name, target_dir / dest_rel, force, result, on_progress)


def _write_initial_config(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    *,
    source_package: str = "",
    test_path: str = "",
    runs_root: str = ".trw/runs",
    target_platforms: list[str] | None = None,
    on_progress: ProgressCallback = None,
) -> None:
    """Write generated config.yaml and learnings index seed files."""
    _write_if_missing(
        target_dir / ".trw" / "config.yaml",
        _default_config(
            source_package=source_package,
            test_path=test_path,
            runs_root=runs_root,
            target_platforms=target_platforms,
        ),
        force,
        result,
        on_progress,
    )
    _write_if_missing(
        target_dir / ".trw" / "learnings" / "index.yaml",
        "entries: []\n",
        force,
        result,
        on_progress,
    )


def _install_hooks(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy bundled hook scripts to ``.claude/hooks/``."""
    hooks_source = _DATA_DIR / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                _copy_file(
                    hook_file,
                    target_dir / ".claude" / "hooks" / hook_file.name,
                    force,
                    result,
                    on_progress,
                )


def _validate_skill(skill_dir: Path) -> tuple[bool, str]:
    """Validate a skill directory has a valid SKILL.md.

    Returns ``(is_valid, reason)``.  Required fields in YAML frontmatter:
    ``name`` and ``description``.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, f"Missing SKILL.md in {skill_dir.name}"

    content = skill_md.read_text(encoding="utf-8")

    # Check for YAML frontmatter (--- delimited)
    if not content.startswith("---"):
        return False, f"No YAML frontmatter in {skill_dir.name}/SKILL.md"

    # Parse frontmatter — need at least two --- delimiters
    parts = content.split("---", 2)
    if len(parts) < 3:
        return False, f"Malformed YAML frontmatter in {skill_dir.name}/SKILL.md"

    try:
        from ruamel.yaml import YAML

        yaml = YAML(typ="safe")
        metadata = yaml.load(parts[1])
        if not isinstance(metadata, dict):
            return False, f"Frontmatter is not a dict in {skill_dir.name}/SKILL.md"
        if not metadata.get("name"):
            return False, f"Missing 'name' in {skill_dir.name}/SKILL.md frontmatter"
        if not metadata.get("description"):
            return False, f"Missing 'description' in {skill_dir.name}/SKILL.md frontmatter"
    except Exception as exc:  # justified: boundary — parse errors from user-authored SKILL.md
        return False, f"YAML parse error in {skill_dir.name}/SKILL.md: {exc}"

    return True, ""


def _install_skills(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy bundled skill directories to ``.claude/skills/``.

    Each skill directory is validated via :func:`_validate_skill` before
    installation.  Invalid skills are skipped with a warning.
    """
    # PRD-CORE-125-FR07: Skills gating -- skip skill installation when
    # skills are disabled via config/profile.
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if not config.effective_skills_enabled:
            logger.debug("skills_install_gated", reason="skills_enabled=False")
            return
    except Exception:  # justified: fail-open, config failure installs skills normally
        pass

    skills_source = _DATA_DIR / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                is_valid, reason = _validate_skill(skill_dir)
                if not is_valid:
                    logger.warning(
                        "skill_validation_failed",
                        skill=skill_dir.name,
                        reason=reason,
                    )
                    continue
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                _ensure_dir(dest_skill, result, on_progress)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        _copy_file(skill_file, dest_skill / skill_file.name, force, result, on_progress)


def _install_agents(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Copy bundled agent markdown files to ``.claude/agents/``."""
    # PRD-CORE-125-FR08: Agents gating -- skip agent installation when
    # agents are disabled via config/profile.
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if config.agents_enabled is not None and not config.agents_enabled:
            logger.debug("agents_install_gated", reason="agents_enabled=False")
            return
    except Exception:  # justified: fail-open, config failure installs agents normally
        pass

    agents_source = _DATA_DIR / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                _copy_file(
                    agent_file,
                    target_dir / ".claude" / "agents" / agent_file.name,
                    force,
                    result,
                    on_progress,
                )


def _generate_root_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback = None,
) -> None:
    """Generate root-level configuration files (``.mcp.json``, ``CLAUDE.md``, ``REVIEW.md``)."""
    _merge_mcp_json(target_dir, result, on_progress)
    _write_if_missing(target_dir / "CLAUDE.md", _minimal_claude_md(), force, result, on_progress)
    _write_if_missing(target_dir / "REVIEW.md", _minimal_review_md(), force, result, on_progress)


def init_project(
    target_dir: Path,
    *,
    force: bool = False,
    source_package: str = "",
    test_path: str = "",
    runs_root: str = ".trw/runs",
    ide: str | None = None,
    on_progress: ProgressCallback = None,
) -> dict[str, list[str]]:
    """Bootstrap TRW framework in *target_dir*.

    Args:
        target_dir: Root of the target git repository.
        force: If ``True``, overwrite existing files.
        source_package: Pre-populate ``source_package_name`` in config.
        test_path: Pre-populate ``tests_relative_path`` in config.
        ide: Target IDE override ("claude-code", "cursor", "opencode", "all").
            When None, auto-detect from existing IDE config directories.
        on_progress: Optional callback called as ``on_progress(action, path)``
            for each file processed. Enables real-time progress reporting.

    Returns:
        Dict with ``created``, ``skipped``, ``errors`` lists.
    """
    from ._codex import (
        codex_hooks_enabled,
        generate_codex_agents,
        generate_codex_config,
        generate_codex_hooks,
        install_codex_skills,
    )
    from ._opencode import generate_agents_md, generate_opencode_config
    from ._update_project import _extract_trw_section_content, _write_manifest

    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    logger.info("project_init_started", project_root=str(target_dir), ide=ide)

    # Validate target is a git repo
    if not (target_dir / ".git").exists():
        result["errors"].append(f"{target_dir} is not a git repository (.git/ not found)")
        logger.error(
            "project_init_failed",
            project_root=str(target_dir),
            error="not a git repository",
        )
        return result

    # Resolve IDE targets before creating any provider-specific directories.
    # Otherwise new scaffold directories can pollute auto-detection.
    ide_targets = resolve_ide_targets(target_dir, ide_override=ide)

    # 1. Create directory structure
    _create_directory_structure(target_dir, result, on_progress)

    # 2. Copy bundled data files
    _copy_bundled_data_files(target_dir, force, result, on_progress)

    # 3. Write generated config and seed files (includes target_platforms)
    _write_initial_config(
        target_dir,
        force,
        result,
        source_package=source_package,
        test_path=test_path,
        runs_root=runs_root,
        target_platforms=ide_targets,
        on_progress=on_progress,
    )

    # 4. Copy hook scripts
    _install_hooks(target_dir, force, result, on_progress)

    # 5. Copy skills
    _install_skills(target_dir, force, result, on_progress)

    # 6. Copy agents
    _install_agents(target_dir, force, result, on_progress)

    # 7. Generate root-level files (Claude Code: .mcp.json, CLAUDE.md)
    _generate_root_files(target_dir, force, result, on_progress)

    # 7b. OpenCode artifacts (FR15: multi-IDE support)
    if "opencode" in ide_targets:
        oc_result = generate_opencode_config(target_dir, force=force)
        result["created"].extend(oc_result.get("created", []))
        result["created"].extend(oc_result.get("updated", []))
        result["skipped"].extend(oc_result.get("preserved", []))
        result["errors"].extend(oc_result.get("errors", []))

        # 7b-i. Generate INSTRUCTIONS.md with model-family content (FR01)
        from ._opencode import (
            detect_model_family,
            generate_opencode_instructions,
            install_opencode_agents,
            install_opencode_commands,
            install_opencode_skills,
        )

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
            instructions_result = generate_opencode_instructions(target_dir, model_family, force=force)
            result["created"].extend(instructions_result.get("created", []))
            result["created"].extend(instructions_result.get("updated", []))
            result["skipped"].extend(instructions_result.get("preserved", []))
            result["errors"].extend(instructions_result.get("errors", []))
        except Exception as exc:  # justified: fail-open, INSTRUCTIONS.md update is best-effort
            result.setdefault("warnings", []).append(f".opencode/INSTRUCTIONS.md generation skipped: {exc}")

        try:
            from trw_mcp.state.claude_md._static_sections import render_minimal_protocol

            agents_result = generate_agents_md(target_dir, render_minimal_protocol(), force=force)
            result["created"].extend(agents_result.get("created", []))
            result["created"].extend(agents_result.get("updated", []))
            result["skipped"].extend(agents_result.get("preserved", []))
            result["errors"].extend(agents_result.get("errors", []))
        except Exception as exc:  # justified: fail-open, AGENTS.md generation is best-effort
            result.setdefault("warnings", []).append(f"AGENTS.md generation skipped: {exc}")

        commands_result = install_opencode_commands(target_dir, force=force)
        result["created"].extend(commands_result.get("created", []))
        result["skipped"].extend(commands_result.get("preserved", []))
        result["errors"].extend(commands_result.get("errors", []))

        agents_result = install_opencode_agents(target_dir, force=force)
        result["created"].extend(agents_result.get("created", []))
        result["skipped"].extend(agents_result.get("preserved", []))
        result["errors"].extend(agents_result.get("errors", []))

        skills_result = install_opencode_skills(target_dir, force=force)
        result["created"].extend(skills_result.get("created", []))
        result["skipped"].extend(skills_result.get("preserved", []))
        result["errors"].extend(skills_result.get("errors", []))

    # 7c. Cursor artifacts (FR05, FR06, FR07: Cursor IDE support)
    if "cursor" in ide_targets:
        from ._cursor import (
            generate_cursor_hooks,
            generate_cursor_mcp_config,
            generate_cursor_rules,
        )
        from ._update_project import _extract_trw_section_content

        cursor_hooks = generate_cursor_hooks(target_dir, force=force)
        result["created"].extend(cursor_hooks.get("created", []))
        result["skipped"].extend(cursor_hooks.get("preserved", []))

        trw_section = _extract_trw_section_content()
        cursor_rules = generate_cursor_rules(target_dir, trw_section, force=force)
        result["created"].extend(cursor_rules.get("created", []))
        result["skipped"].extend(cursor_rules.get("preserved", []))

        cursor_mcp = generate_cursor_mcp_config(target_dir, force=force)
        result["created"].extend(cursor_mcp.get("created", []))
        result["skipped"].extend(cursor_mcp.get("preserved", []))

    # 7d. Codex artifacts
    if "codex" in ide_targets:

        codex_config = generate_codex_config(target_dir, force=force)
        result["created"].extend(codex_config.get("created", []))
        result["created"].extend(codex_config.get("updated", []))
        result["skipped"].extend(codex_config.get("preserved", []))
        result["errors"].extend(codex_config.get("errors", []))

        if codex_hooks_enabled(target_dir):
            codex_hooks = generate_codex_hooks(target_dir, force=force)
            result["created"].extend(codex_hooks.get("created", []))
            result["created"].extend(codex_hooks.get("updated", []))
            result["skipped"].extend(codex_hooks.get("preserved", []))
            result["errors"].extend(codex_hooks.get("errors", []))

        codex_agents = generate_codex_agents(target_dir, force=force)
        result["created"].extend(codex_agents.get("created", []))
        result["created"].extend(codex_agents.get("updated", []))
        result["skipped"].extend(codex_agents.get("preserved", []))
        result["errors"].extend(codex_agents.get("errors", []))

        codex_skills = install_codex_skills(target_dir, force=force)
        result["created"].extend(codex_skills.get("created", []))
        result["created"].extend(codex_skills.get("updated", []))
        result["skipped"].extend(codex_skills.get("preserved", []))
        result["errors"].extend(codex_skills.get("errors", []))

    # 7d-i. Codex INSTRUCTIONS.md (FR01)
    if "codex" in ide_targets:
        from ._opencode import generate_codex_instructions

        try:
            instructions_result = generate_codex_instructions(target_dir, force=force)
            result["created"].extend(instructions_result.get("created", []))
            result["created"].extend(instructions_result.get("updated", []))
            result["skipped"].extend(instructions_result.get("preserved", []))
            result["errors"].extend(instructions_result.get("errors", []))
        except Exception as exc:  # justified: fail-open, INSTRUCTIONS.md update is best-effort
            result.setdefault("warnings", []).append(f".codex/INSTRUCTIONS.md generation skipped: {exc}")

        try:
            from trw_mcp.state.claude_md._static_sections import render_codex_trw_section

            agents_result = generate_agents_md(target_dir, render_codex_trw_section(), force=force)
            result["created"].extend(agents_result.get("created", []))
            result["created"].extend(agents_result.get("updated", []))
            result["skipped"].extend(agents_result.get("preserved", []))
            result["errors"].extend(agents_result.get("errors", []))
        except Exception as exc:  # justified: fail-open, AGENTS.md generation is best-effort
            result.setdefault("warnings", []).append(f"Codex AGENTS.md generation skipped: {exc}")

    # 7e. Copilot artifacts (PRD-CORE-127)
    if "copilot" in ide_targets:
        from ._copilot import (
            generate_copilot_agents,
            generate_copilot_hooks,
            generate_copilot_instructions,
            generate_copilot_path_instructions,
            install_copilot_skills,
        )

        try:
            instr_result = generate_copilot_instructions(target_dir, force=force)
            result["created"].extend(instr_result.get("created", []))
            result["skipped"].extend(instr_result.get("preserved", []))
            result["errors"].extend(instr_result.get("errors", []))
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f"copilot-instructions.md generation skipped: {exc}")

        try:
            path_result = generate_copilot_path_instructions(target_dir, force=force)
            result["created"].extend(path_result.get("created", []))
            result["skipped"].extend(path_result.get("preserved", []))
            result["errors"].extend(path_result.get("errors", []))
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f"copilot path instructions generation skipped: {exc}")

        try:
            hooks_result = generate_copilot_hooks(target_dir, force=force)
            result["created"].extend(hooks_result.get("created", []))
            result["skipped"].extend(hooks_result.get("preserved", []))
            result["errors"].extend(hooks_result.get("errors", []))
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f"copilot hooks generation skipped: {exc}")

        try:
            agents_result = generate_copilot_agents(target_dir, force=force)
            result["created"].extend(agents_result.get("created", []))
            result["skipped"].extend(agents_result.get("preserved", []))
            result["errors"].extend(agents_result.get("errors", []))
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f"copilot agents generation skipped: {exc}")

        try:
            skills_result = install_copilot_skills(target_dir, force=force)
            result["created"].extend(skills_result.get("created", []))
            result["skipped"].extend(skills_result.get("preserved", []))
            result["errors"].extend(skills_result.get("errors", []))
        except Exception as exc:  # justified: fail-open
            result.setdefault("warnings", []).append(f"copilot skills generation skipped: {exc}")

    # 8. Write managed-artifacts manifest
    _write_manifest(target_dir, result)

    # 9. Write installer metadata + VERSION.yaml
    _write_installer_metadata(target_dir, "init-project", result, on_progress)
    _write_version_yaml(target_dir, result, on_progress)

    if result["errors"]:
        logger.warning(
            "project_init_partial",
            project_root=str(target_dir),
            errors=result["errors"][:3],
        )
    logger.info(
        "project_init_ok",
        project_root=str(target_dir),
        dirs_created=len([p for p in result["created"] if p.endswith("/")]),
        files_created=len(result["created"]),
        skipped=len(result["skipped"]),
        errors=len(result["errors"]),
    )
    return result
