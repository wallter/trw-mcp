"""Skill + agent installation helpers — extracted from _init_project.py for module-size compliance.

Belongs to the ``_init_project.py`` facade. Re-exported there for back-compat
with external callers (`bootstrap/__init__.py` exports + `_copilot.py` and
`_codex.py` which import `_validate_skill` directly).

Three helpers:
- ``_validate_skill`` — verify SKILL.md has required frontmatter fields
- ``_install_skills`` — copy bundled skills to .claude/skills/
- ``_install_agents`` — materialize every bundled agent for each selected
  client and write it to that client's own destination: tool-placeholder
  rendering, capability-tier ``model:`` resolution (PRD-INFRA-104), and
  per-client frontmatter translation (PRD-CORE-252).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import structlog

from trw_mcp.agents.tier_resolver import materialize_agent
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
    *,
    clients: Sequence[str] = ("claude-code",),
    explicit: bool = False,
) -> None:
    """Copy bundled skill directories to ``.claude/skills/``.

    Each skill directory is validated via :func:`_validate_skill` before
    installation.  Invalid skills are skipped with a warning.

    *clients* is the resolved install selection (PRD-CORE-262-FR05). A
    codex-only install used to end up with 29 duplicate skill files under a
    directory codex never reads -- codex gets its skills from its own
    installer (``install_codex_skills``). Every other selection (default,
    claude-code, cursor-ide, a mixed set, ...) still gets ``.claude/skills``
    exactly as HEAD did; only an EXPLICIT codex-only selection drops it
    (CORE262-13: *explicit* distinguishes a user ``--ide codex`` from
    ``detect_ide`` resolving to ``["codex"]`` off a pre-existing ``.codex/``
    marker on a bare install).
    """
    from . import _wants_claude_scaffold

    if not _wants_claude_scaffold(clients, explicit=explicit):
        logger.debug("skills_install_skipped_for_clients", clients=list(clients))
        return

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
    clients: Sequence[str] = ("claude-code",),
) -> None:
    """Install every bundled agent into each selected client's own destination.

    Bundled agents are client-neutral: they declare a capability tier
    (``frontier|balanced|local-large|local-small``) in ``model:``, reference TRW
    tools through ``{tool:trw_x}`` placeholders, and are authored in Claude
    Code's frontmatter dialect. Each file is materialized per client via
    :func:`trw_mcp.agents.tier_resolver.materialize_agent` — tier resolution,
    placeholder rendering, and the per-client frontmatter translation — and
    written to the directory that client's
    :class:`~trw_mcp.agents.agent_formats.AgentFormat` declares
    (PRD-CORE-252-FR03).

    Before this, the destination was hardcoded to ``.claude/agents`` whatever
    the client argument said, and the sole production call site never passed a
    client at all, so six of the seven supported harnesses received none of the
    bundled specialists.

    A client with no agent surface produces exactly ONE record in
    ``result['info']`` naming it and its reason — eleven identical records
    would be noise — and creates no directory.

    Args:
        target_dir: Root of the target git repository.
        force: When ``True`` overwrite existing destination files.
        result: Bootstrap accumulator dict (``created``/``skipped``/``errors``).
        on_progress: Optional progress callback.
        clients: Selected client-profile identifiers. Duplicates collapse;
            order is preserved for deterministic reporting.
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

    for client in dict.fromkeys(clients):
        try:
            _install_agents_for_client(
                target_dir,
                agents_source,
                client,
                force=force,
                result=result,
                on_progress=on_progress,
            )
        except Exception as exc:  # justified: NFR02, one failing client must not stop the rest
            logger.warning("agent_install_client_failed", client=client, exc_info=True)
            result["errors"].append(f"Failed to install agents for {client}: {exc}")


def _install_agents_for_client(
    target_dir: Path,
    agents_source: Path,
    client: str,
    *,
    force: bool,
    result: dict[str, list[str]],
    on_progress: ProgressCallback,
) -> None:
    """Install the whole bundle for one client, or record why it cannot be."""
    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.exceptions import AgentFormatError

    try:
        fmt = agent_format_for(client)
    except AgentFormatError as exc:
        result.setdefault("info", []).append(f"agents: {client} — {exc}")
        return
    if not fmt.supports_agents:
        result.setdefault("info", []).append(f"agents: {client} — {fmt.unsupported_reason}")
        logger.info("agent_install_client_unsupported", client=client, reason=fmt.unsupported_reason)
        return

    for agent_file in sorted(agents_source.iterdir()):
        if agent_file.suffix != ".md":
            continue
        try:
            rel = fmt.destination_for(agent_file.stem)
        except AgentFormatError as exc:
            # NFR03: a name that would escape the destination never becomes a
            # path component. Recorded and skipped, never written.
            logger.warning("agent_install_name_rejected", agent=agent_file.name, client=client, error=str(exc))
            result["errors"].append(f"Rejected agent name {agent_file.stem!r} for {client}: {exc}")
            continue
        _install_one_agent(
            agent_file,
            target_dir / rel,
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
    """Install a single bundled agent, materialized for *client*.

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

    from trw_mcp.agents.agent_formats import agent_format_for
    from trw_mcp.exceptions import AgentFormatError

    try:
        cap = agent_format_for(client).max_agent_bytes
        if src.stat().st_size > cap:
            # NFR03: bound the read. A pathological file in a forked bundle
            # must not be pulled into memory before anything inspects it.
            raise AgentFormatError(f"agent {src.name} is larger than the {cap}-byte cap")
        bundled = src.read_text(encoding="utf-8")
    except (OSError, AgentFormatError) as exc:
        result["errors"].append(f"Failed to read {src}: {exc}")
        if on_progress:
            on_progress("Error", str(dest))
        return

    try:
        rewritten = materialize_agent(bundled, client=client)
    except (ValueError, AgentFormatError) as exc:
        # Unknown tier, unparseable frontmatter, or an undecided frontmatter
        # key -- surface clearly, skip this agent only (NFR02).
        logger.warning(
            "agent_install_materialize_failed",
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
