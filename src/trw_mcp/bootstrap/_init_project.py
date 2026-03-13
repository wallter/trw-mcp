"""init_project flow — bootstraps TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from ._utils import (
    _DATA_DIR,
    _copy_file,
    _default_config,
    _ensure_dir,
    _merge_mcp_json,
    _minimal_claude_md,
    _write_if_missing,
    _write_installer_metadata,
    _write_version_yaml,
)

logger = structlog.get_logger()


def _create_directory_structure(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Create the TRW directory scaffold inside *target_dir*."""
    from . import _TRW_DIRS

    for rel_dir in _TRW_DIRS:
        _ensure_dir(target_dir / rel_dir, result)


def _copy_bundled_data_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Copy all bundled data files from ``_DATA_FILE_MAP`` to *target_dir*."""
    from . import _DATA_FILE_MAP

    for data_name, dest_rel in _DATA_FILE_MAP:
        _copy_file(_DATA_DIR / data_name, target_dir / dest_rel, force, result)


def _write_initial_config(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
    *,
    source_package: str = "",
    test_path: str = "",
) -> None:
    """Write generated config.yaml and learnings index seed files."""
    _write_if_missing(
        target_dir / ".trw" / "config.yaml",
        _default_config(source_package=source_package, test_path=test_path),
        force,
        result,
    )
    _write_if_missing(
        target_dir / ".trw" / "learnings" / "index.yaml",
        "entries: []\n",
        force,
        result,
    )


def _install_hooks(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
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
                )


def _install_skills(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Copy bundled skill directories to ``.claude/skills/``."""
    skills_source = _DATA_DIR / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                _ensure_dir(dest_skill, result)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        _copy_file(skill_file, dest_skill / skill_file.name, force, result)


def _install_agents(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Copy bundled agent markdown files to ``.claude/agents/``."""
    agents_source = _DATA_DIR / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                _copy_file(
                    agent_file,
                    target_dir / ".claude" / "agents" / agent_file.name,
                    force,
                    result,
                )


def _generate_root_files(
    target_dir: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Generate root-level configuration files (``.mcp.json``, ``CLAUDE.md``)."""
    _merge_mcp_json(target_dir, result)
    _write_if_missing(target_dir / "CLAUDE.md", _minimal_claude_md(), force, result)


def init_project(
    target_dir: Path,
    *,
    force: bool = False,
    source_package: str = "",
    test_path: str = "",
) -> dict[str, list[str]]:
    """Bootstrap TRW framework in *target_dir*.

    Args:
        target_dir: Root of the target git repository.
        force: If ``True``, overwrite existing files.
        source_package: Pre-populate ``source_package_name`` in config.
        test_path: Pre-populate ``tests_relative_path`` in config.

    Returns:
        Dict with ``created``, ``skipped``, ``errors`` lists.
    """
    from ._update_project import _write_manifest

    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    # Validate target is a git repo
    if not (target_dir / ".git").exists():
        result["errors"].append(
            f"{target_dir} is not a git repository (.git/ not found)"
        )
        return result

    # 1. Create directory structure
    _create_directory_structure(target_dir, result)

    # 2. Copy bundled data files
    _copy_bundled_data_files(target_dir, force, result)

    # 3. Write generated config and seed files
    _write_initial_config(
        target_dir, force, result,
        source_package=source_package, test_path=test_path,
    )

    # 4. Copy hook scripts
    _install_hooks(target_dir, force, result)

    # 5. Copy skills
    _install_skills(target_dir, force, result)

    # 6. Copy agents
    _install_agents(target_dir, force, result)

    # 7. Generate root-level files
    _generate_root_files(target_dir, force, result)

    # 8. Write managed-artifacts manifest
    _write_manifest(target_dir, result)

    # 9. Write installer metadata + VERSION.yaml
    _write_installer_metadata(target_dir, "init-project", result)
    _write_version_yaml(target_dir, result)

    logger.info(
        "bootstrap_complete",
        target=str(target_dir),
        created=len(result["created"]),
        skipped=len(result["skipped"]),
        errors=len(result["errors"]),
    )
    return result
