"""Artifact name discovery — bundled vs. user-created artifact enumeration.

Belongs to the ``_template_updater.py`` facade. Re-exported there for
backward compatibility with callers/tests that import
``_template_updater._get_bundled_names`` / ``_get_custom_names``
(``_version_migration.py``, ``bootstrap/__init__.py``, ``_update_project.py``).

Extracted from ``_template_updater.py`` (350-eLOC gate) — pure filesystem
enumeration with no copy/side-effect responsibility.
"""

from __future__ import annotations

from pathlib import Path

from ._utils import _DATA_DIR


def _get_bundled_names(data_dir: Path | None = None) -> dict[str, list[str]]:
    """Return sorted lists of bundled artifact names by category."""
    effective = data_dir or _DATA_DIR
    skills_source = effective / "skills"
    agents_source = effective / "agents"
    hooks_source = effective / "hooks"
    opencode_root = effective / "opencode"
    opencode_commands = opencode_root / "commands"
    opencode_agents = opencode_root / "agents"
    opencode_skills = opencode_root / "skills"
    return {
        "skills": sorted(d.name for d in skills_source.iterdir() if d.is_dir()) if skills_source.is_dir() else [],
        "agents": sorted(f.name for f in agents_source.iterdir() if f.suffix == ".md")
        if agents_source.is_dir()
        else [],
        "hooks": sorted(f.name for f in hooks_source.iterdir() if f.suffix == ".sh") if hooks_source.is_dir() else [],
        "opencode_commands": sorted(f.name for f in opencode_commands.iterdir() if f.suffix == ".md")
        if opencode_commands.is_dir()
        else [],
        "opencode_agents": sorted(f.name for f in opencode_agents.iterdir() if f.suffix == ".md")
        if opencode_agents.is_dir()
        else [],
        "opencode_skills": sorted(d.name for d in opencode_skills.iterdir() if d.is_dir())
        if opencode_skills.is_dir()
        else [],
    }


def _get_custom_names(target_dir: Path, data_dir: Path | None = None) -> dict[str, list[str]]:
    """Return sorted lists of user-created artifact names not in bundled data."""
    bundled = _get_bundled_names(data_dir)
    bundled_skills = set(bundled["skills"])
    bundled_agents = set(bundled["agents"])
    bundled_hooks = set(bundled["hooks"])
    bundled_opencode_commands = set(bundled.get("opencode_commands", []))
    bundled_opencode_agents = set(bundled.get("opencode_agents", []))
    bundled_opencode_skills = set(bundled.get("opencode_skills", []))
    result: dict[str, list[str]] = {
        "skills": [],
        "agents": [],
        "hooks": [],
        "opencode_commands": [],
        "opencode_agents": [],
        "opencode_skills": [],
    }

    skills_dir = target_dir / ".claude" / "skills"
    if skills_dir.is_dir():
        result["skills"] = sorted(d.name for d in skills_dir.iterdir() if d.is_dir() and d.name not in bundled_skills)

    agents_dir = target_dir / ".claude" / "agents"
    if agents_dir.is_dir():
        result["agents"] = sorted(
            f.name for f in agents_dir.iterdir() if f.suffix == ".md" and f.name not in bundled_agents
        )

    hooks_dir = target_dir / ".claude" / "hooks"
    if hooks_dir.is_dir():
        result["hooks"] = sorted(
            f.name for f in hooks_dir.iterdir() if f.suffix == ".sh" and f.name not in bundled_hooks
        )

    opencode_commands_dir = target_dir / ".opencode" / "commands"
    if opencode_commands_dir.is_dir():
        result["opencode_commands"] = sorted(
            f.name
            for f in opencode_commands_dir.iterdir()
            if f.suffix == ".md" and f.name not in bundled_opencode_commands
        )

    opencode_agents_dir = target_dir / ".opencode" / "agents"
    if opencode_agents_dir.is_dir():
        result["opencode_agents"] = sorted(
            f.name for f in opencode_agents_dir.iterdir() if f.suffix == ".md" and f.name not in bundled_opencode_agents
        )

    opencode_skills_dir = target_dir / ".opencode" / "skills"
    if opencode_skills_dir.is_dir():
        result["opencode_skills"] = sorted(
            d.name for d in opencode_skills_dir.iterdir() if d.is_dir() and d.name not in bundled_opencode_skills
        )

    return result
