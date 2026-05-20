"""Skill + agent installation helpers — extracted from _init_project.py for module-size compliance.

Belongs to the ``_init_project.py`` facade. Re-exported there for back-compat
with external callers (`bootstrap/__init__.py` exports + `_copilot.py` and
`_codex.py` which import `_validate_skill` directly).

Three helpers:
- ``_validate_skill`` — verify SKILL.md has required frontmatter fields
- ``_install_skills`` — copy bundled skills to .claude/skills/
- ``_install_agents`` — copy bundled agent .md files to .claude/agents/,
  rewriting the ``model:`` field via the per-client tier resolver
  (PRD-INFRA-104).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.agents.tier_resolver import rewrite_model_line
from trw_mcp.models.skill_manifest import validate_skill_markdown

from ._utils import (
    ProgressCallback,
    _copy_file,
    _ensure_dir,
)

logger = structlog.get_logger(__name__)


def _data_dir() -> Path:
    """Look up ``_DATA_DIR`` via the parent ``_init_project`` module.

    Indirection lets test code patch ``trw_mcp.bootstrap._init_project._DATA_DIR``
    and have the patch flow through to this module's ``_install_skills`` /
    ``_install_agents`` calls (PRD-DIST-243 batch 21b).
    """
    from trw_mcp.bootstrap._init_project import _DATA_DIR  # type: ignore[attr-defined]

    return _DATA_DIR


def _validate_skill(skill_dir: Path) -> tuple[bool, str]:
    """Validate a skill directory has a valid SKILL.md.

    Returns ``(is_valid, reason)``.  Required fields in YAML frontmatter:
    ``name`` and ``description``.
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, f"Missing SKILL.md in {skill_dir.name}"

    content = skill_md.read_text(encoding="utf-8")
    result = validate_skill_markdown(content, path=skill_md, mode="compat")
    if result.ok and result.manifest is not None:
        for warning in result.warnings:
            logger.debug(
                "skill_manifest_compat_warning",
                skill=skill_dir.name,
                field=warning.field,
                reason=warning.reason,
            )
        return True, ""

    if result.errors:
        reason = result.errors[0].reason
        field = result.errors[0].field
        if field == "frontmatter" and "missing" in reason:
            return False, f"No YAML frontmatter in {skill_dir.name}/SKILL.md"
        if field == "frontmatter" and "unterminated" in reason:
            return False, f"Malformed YAML frontmatter in {skill_dir.name}/SKILL.md"
        if field == "frontmatter" and "must be a mapping" in reason:
            return False, f"Frontmatter is not a dict in {skill_dir.name}/SKILL.md"
        if field == "name":
            return False, f"Missing 'name' in {skill_dir.name}/SKILL.md frontmatter"
        if field == "description":
            return False, f"Missing 'description' in {skill_dir.name}/SKILL.md frontmatter"
        return False, f"YAML parse error in {skill_dir.name}/SKILL.md: {reason}"

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
        logger.debug("skills_install_gate_unavailable", exc_info=True)

    skills_source = _data_dir() / "skills"
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
    *,
    client: str = "claude-code",
) -> None:
    """Copy bundled agent markdown files to ``.claude/agents/``.

    PRD-INFRA-104: bundled agents declare a capability-tier vocabulary
    (``frontier|balanced|local-large|local-small``) in their ``model:``
    frontmatter. Each client harness accepts a different concrete model
    vocabulary, so this function applies
    :func:`trw_mcp.agents.tier_resolver.rewrite_model_line` per agent
    before writing the destination file. For the default ``claude-code``
    client that means ``model: frontier`` → ``model: opus``, etc.
    Unknown tiers are logged and the file is skipped (FR-11).

    Args:
        target_dir: Root of the target git repository.
        force: When ``True`` overwrite existing destination files.
        result: Bootstrap accumulator dict (``created``/``skipped``/``errors``).
        on_progress: Optional progress callback.
        client: Client-profile identifier passed through to the tier
            resolver. Defaults to ``"claude-code"``; cursor-ide installs
            its own agents via :mod:`trw_mcp.bootstrap._cursor_ide` so the
            default is correct for the only call-site.
    """
    # PRD-CORE-125-FR08: Agents gating -- skip agent installation when
    # agents are disabled via config/profile.
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
        if config.agents_enabled is not None and not config.agents_enabled:
            logger.debug("agents_install_gated", reason="agents_enabled=False")
            return
    except Exception:  # justified: fail-open, config failure installs agents normally
        logger.debug("agents_install_gate_unavailable", exc_info=True)

    agents_source = _data_dir() / "agents"
    if not agents_source.is_dir():
        return

    dest_root = target_dir / ".claude" / "agents"
    for agent_file in sorted(agents_source.iterdir()):
        if agent_file.suffix != ".md":
            continue
        dest = dest_root / agent_file.name
        _install_one_agent(
            agent_file,
            dest,
            force=force,
            result=result,
            on_progress=on_progress,
            client=client,
        )


def _install_one_agent(
    src: Path,
    dest: Path,
    *,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
    client: str,
) -> None:
    """Install a single bundled agent, rewriting its ``model:`` field.

    Idempotent: if *dest* already exists and *force* is False, the file
    is skipped (matching :func:`trw_mcp.bootstrap._utils._copy_file`
    semantics). Tier resolution failures are logged and the file is
    appended to ``result['errors']`` — the rest of the install
    continues (PRD-INFRA-104 FR-11).
    """
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        if on_progress:
            on_progress("Skipped", str(dest))
        return

    try:
        bundled = src.read_text(encoding="utf-8")
    except OSError as exc:
        result["errors"].append(f"Failed to read {src}: {exc}")
        if on_progress:
            on_progress("Error", str(dest))
        return

    try:
        rewritten = rewrite_model_line(bundled, client=client)
    except ValueError as exc:
        # Unknown tier -- surface clearly, skip this agent only.
        logger.warning(
            "agent_install_tier_unknown",
            agent=src.name,
            client=client,
            error=str(exc),
        )
        result["errors"].append(str(src))
        if on_progress:
            on_progress("Error", str(dest))
        return

    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rewritten, encoding="utf-8")
    except OSError as exc:
        result["errors"].append(f"Failed to write {dest}: {exc}")
        if on_progress:
            on_progress("Error", str(dest))
        return

    result["created"].append(str(dest))
    if on_progress:
        on_progress("Created", str(dest))
