"""Project bootstrap — sets up and updates TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.

``trw-mcp update-project`` selectively updates framework files (hooks,
skills, agents, FRAMEWORK.md) while preserving user-customized files
(config.yaml, learnings, CLAUDE.md user sections).
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger()

_DATA_DIR = Path(__file__).parent / "data"

# Directories to scaffold inside the target repo.
_TRW_DIRS = [
    ".trw/frameworks",
    ".trw/context",
    ".trw/templates",
    ".trw/learnings/entries",
    ".trw/scripts",
    ".claude/hooks",
    ".claude/skills",
    ".claude/agents",
]

# Mapping of bundled data files to their destination paths (relative to target).
_DATA_FILE_MAP: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("gitignore.txt", ".trw/.gitignore"),
    ("settings.json", ".claude/settings.json"),
]


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
    result: dict[str, list[str]] = {"created": [], "skipped": [], "errors": []}

    # Validate target is a git repo
    if not (target_dir / ".git").exists():
        result["errors"].append(
            f"{target_dir} is not a git repository (.git/ not found)"
        )
        return result

    # 1. Create directory structure
    for rel_dir in _TRW_DIRS:
        _ensure_dir(target_dir / rel_dir, result)

    # 2. Copy bundled data files
    for data_name, dest_rel in _DATA_FILE_MAP:
        _copy_file(_DATA_DIR / data_name, target_dir / dest_rel, force, result)

    # 3. Write generated config and seed files
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

    # 4. Copy hook scripts
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

    # 5. Copy skills
    skills_source = _DATA_DIR / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                _ensure_dir(dest_skill, result)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        _copy_file(skill_file, dest_skill / skill_file.name, force, result)

    # 6. Copy agents
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

    # 7. Generate root-level files
    _write_if_missing(target_dir / ".mcp.json", _generate_mcp_json(), force, result)
    _write_if_missing(target_dir / "CLAUDE.md", _minimal_claude_md(), force, result)

    logger.info(
        "bootstrap_complete",
        target=str(target_dir),
        created=len(result["created"]),
        skipped=len(result["skipped"]),
        errors=len(result["errors"]),
    )
    return result


# Files that are always overwritten during update (framework-managed).
_ALWAYS_UPDATE: list[tuple[str, str]] = [
    ("framework.md", ".trw/frameworks/FRAMEWORK.md"),
    ("behavioral_protocol.yaml", ".trw/context/behavioral_protocol.yaml"),
    ("messages/messages.yaml", ".trw/context/messages.yaml"),
    ("templates/claude_md.md", ".trw/templates/claude_md.md"),
    ("settings.json", ".claude/settings.json"),
]

# Files that are never overwritten during update (user-customized).
# These are only created if missing.
_NEVER_OVERWRITE = {
    ".trw/config.yaml",
    ".trw/learnings/index.yaml",
    ".mcp.json",
}

# CLAUDE.md markers for the auto-generated section.
_TRW_START_MARKER = "<!-- trw:start -->"
_TRW_END_MARKER = "<!-- trw:end -->"
_TRW_HEADER_MARKER = "<!-- TRW AUTO-GENERATED — do not edit between markers -->"


def update_project(
    target_dir: Path,
    *,
    pip_install: bool = False,
) -> dict[str, list[str]]:
    """Update TRW framework files in *target_dir* while preserving user config.

    Always updates: hooks, skills, agents, FRAMEWORK.md, behavioral_protocol.yaml,
    claude_md template, settings.json.

    Never overwrites: config.yaml, learnings/, .mcp.json.

    Smart update: CLAUDE.md — replaces content between ``trw:start``/``trw:end``
    markers while preserving all user-written sections.

    Args:
        target_dir: Root of the target git repository.
        pip_install: If True, reinstall the trw-mcp package after file updates.

    Returns:
        Dict with ``updated``, ``created``, ``preserved``, ``errors``,
        and ``warnings`` lists.
    """
    result: dict[str, list[str]] = {
        "updated": [],
        "created": [],
        "preserved": [],
        "errors": [],
        "warnings": [],
    }

    # Validate target has TRW installed
    if not (target_dir / ".trw").exists():
        result["errors"].append(
            f"{target_dir} does not have TRW installed (.trw/ not found). "
            "Run `trw-mcp init-project` first."
        )
        return result

    # 1. Ensure directories exist
    for rel_dir in _TRW_DIRS:
        _ensure_dir(target_dir / rel_dir, result)

    # 2. Update framework files (always overwrite)
    for data_name, dest_rel in _ALWAYS_UPDATE:
        src = _DATA_DIR / data_name
        dest = target_dir / dest_rel
        existed = dest.exists()
        try:
            shutil.copy2(src, dest)
            if existed:
                result["updated"].append(str(dest))
            else:
                result["created"].append(str(dest))
        except OSError as exc:
            result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")

    # 3. Create-only files (never overwrite existing)
    for rel_path in _NEVER_OVERWRITE:
        dest = target_dir / rel_path
        if dest.exists():
            result["preserved"].append(str(dest))

    # 4. Update hooks (always overwrite)
    hooks_source = _DATA_DIR / "hooks"
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                dest = target_dir / ".claude" / "hooks" / hook_file.name
                existed = dest.exists()
                try:
                    shutil.copy2(hook_file, dest)
                    if dest.suffix == ".sh":
                        executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                        os.chmod(dest, os.stat(dest).st_mode | executable)
                    if existed:
                        result["updated"].append(str(dest))
                    else:
                        result["created"].append(str(dest))
                except OSError as exc:
                    result["errors"].append(
                        f"Failed to copy {hook_file} -> {dest}: {exc}"
                    )

    # 5. Update skills (always overwrite)
    skills_source = _DATA_DIR / "skills"
    if skills_source.is_dir():
        for skill_dir in sorted(skills_source.iterdir()):
            if skill_dir.is_dir():
                dest_skill = target_dir / ".claude" / "skills" / skill_dir.name
                _ensure_dir(dest_skill, result)
                for skill_file in sorted(skill_dir.iterdir()):
                    if skill_file.is_file():
                        dest = dest_skill / skill_file.name
                        existed = dest.exists()
                        try:
                            shutil.copy2(skill_file, dest)
                            if existed:
                                result["updated"].append(str(dest))
                            else:
                                result["created"].append(str(dest))
                        except OSError as exc:
                            result["errors"].append(
                                f"Failed to copy {skill_file} -> {dest}: {exc}"
                            )

    # 6. Update agents (always overwrite)
    agents_source = _DATA_DIR / "agents"
    if agents_source.is_dir():
        for agent_file in sorted(agents_source.iterdir()):
            if agent_file.suffix == ".md":
                dest = target_dir / ".claude" / "agents" / agent_file.name
                existed = dest.exists()
                try:
                    shutil.copy2(agent_file, dest)
                    if existed:
                        result["updated"].append(str(dest))
                    else:
                        result["created"].append(str(dest))
                except OSError as exc:
                    result["errors"].append(
                        f"Failed to copy {agent_file} -> {dest}: {exc}"
                    )

    # 7. Smart-update CLAUDE.md (preserve user sections, update trw block)
    claude_md_path = target_dir / "CLAUDE.md"
    if claude_md_path.exists():
        _update_claude_md_trw_section(claude_md_path, result)
    else:
        # No CLAUDE.md yet — write the full template
        try:
            claude_md_path.write_text(_minimal_claude_md(), encoding="utf-8")
            result["created"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to write {claude_md_path}: {exc}")

    # 8. Remove stale hooks/skills/agents no longer in bundled data
    _remove_stale_artifacts(target_dir, result)

    # 9. Check installed package version
    _check_package_version(result)

    # 10. Reinstall package if requested
    if pip_install:
        _pip_install_package(target_dir, result)

    # 11. Remind about running sessions
    result["warnings"].append(
        "Running Claude Code sessions use cached hooks/settings. "
        "Restart active sessions (or run /mcp) to pick up updates."
    )

    logger.info(
        "update_complete",
        target=str(target_dir),
        updated=len(result["updated"]),
        created=len(result["created"]),
        preserved=len(result["preserved"]),
        errors=len(result["errors"]),
    )
    return result


def _update_claude_md_trw_section(
    claude_md_path: Path,
    result: dict[str, list[str]],
) -> None:
    """Replace the auto-generated TRW section in CLAUDE.md.

    Preserves all user-written content above and below the markers.
    """
    content = claude_md_path.read_text(encoding="utf-8")
    new_block = _minimal_claude_md_trw_block()

    start_idx = content.find(_TRW_START_MARKER)
    end_idx = content.find(_TRW_END_MARKER)

    if start_idx != -1 and end_idx != -1:
        # Replace the existing auto-generated section
        end_idx += len(_TRW_END_MARKER)
        # Also capture the header marker line if present
        header_idx = content.rfind(_TRW_HEADER_MARKER, 0, start_idx)
        replace_start = header_idx if header_idx != -1 else start_idx
        updated = content[:replace_start] + new_block + content[end_idx:]
        try:
            claude_md_path.write_text(updated, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    elif _TRW_START_MARKER not in content:
        # No TRW section — append it
        if not content.endswith("\n"):
            content += "\n"
        content += "\n" + new_block
        try:
            claude_md_path.write_text(content, encoding="utf-8")
            result["updated"].append(str(claude_md_path))
        except OSError as exc:
            result["errors"].append(f"Failed to update {claude_md_path}: {exc}")
    else:
        result["errors"].append(
            f"CLAUDE.md has malformed TRW markers — found start but not end"
        )


def _minimal_claude_md_trw_block() -> str:
    """Return just the auto-generated TRW section for CLAUDE.md updates."""
    # Extract the TRW block from the full template
    full = _minimal_claude_md()
    start_idx = full.find(_TRW_HEADER_MARKER)
    end_idx = full.find(_TRW_END_MARKER)
    if start_idx != -1 and end_idx != -1:
        return full[start_idx : end_idx + len(_TRW_END_MARKER)] + "\n"
    # Fallback: return entire trw:start..trw:end
    start_idx = full.find(_TRW_START_MARKER)
    if start_idx != -1 and end_idx != -1:
        return full[start_idx : end_idx + len(_TRW_END_MARKER)] + "\n"
    return ""


def _remove_stale_artifacts(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Remove hooks/skills/agents that no longer exist in bundled data.

    This ensures clean updates — if a hook is renamed or a skill is removed
    in a new version, the stale file is cleaned up.
    """
    # Stale hooks
    hooks_source = _DATA_DIR / "hooks"
    if hooks_source.is_dir():
        bundled_hooks = {f.name for f in hooks_source.iterdir() if f.suffix == ".sh"}
        target_hooks = target_dir / ".claude" / "hooks"
        if target_hooks.is_dir():
            for existing in target_hooks.iterdir():
                if existing.suffix == ".sh" and existing.name not in bundled_hooks:
                    try:
                        existing.unlink()
                        result["updated"].append(f"removed:{existing}")
                    except OSError:
                        pass  # Non-critical

    # Stale skills (directories not in bundled data)
    skills_source = _DATA_DIR / "skills"
    if skills_source.is_dir():
        bundled_skills = {d.name for d in skills_source.iterdir() if d.is_dir()}
        target_skills = target_dir / ".claude" / "skills"
        if target_skills.is_dir():
            for existing in target_skills.iterdir():
                if existing.is_dir() and existing.name not in bundled_skills:
                    try:
                        shutil.rmtree(existing)
                        result["updated"].append(f"removed:{existing}")
                    except OSError:
                        pass

    # Stale agents
    agents_source = _DATA_DIR / "agents"
    if agents_source.is_dir():
        bundled_agents = {f.name for f in agents_source.iterdir() if f.suffix == ".md"}
        target_agents = target_dir / ".claude" / "agents"
        if target_agents.is_dir():
            for existing in target_agents.iterdir():
                if existing.suffix == ".md" and existing.name not in bundled_agents:
                    try:
                        existing.unlink()
                        result["updated"].append(f"removed:{existing}")
                    except OSError:
                        pass


def _check_package_version(result: dict[str, list[str]]) -> None:
    """Compare installed trw-mcp version against source version.

    Warns if the installed package is outdated, which means server-side
    fixes (log filtering, LLM client, tool logic) won't be active.
    """
    from trw_mcp import __version__ as source_version

    try:
        installed_version = importlib.metadata.version("trw-mcp")
    except importlib.metadata.PackageNotFoundError:
        result["warnings"].append(
            "trw-mcp package not found in Python environment. "
            "Install with: pip install -e trw-mcp[dev]"
        )
        return

    if installed_version != source_version:
        result["warnings"].append(
            f"Installed trw-mcp ({installed_version}) differs from source "
            f"({source_version}). Server-side fixes require reinstall: "
            f"pip install -e trw-mcp[dev]"
        )
    else:
        result["preserved"].append(
            f"trw-mcp package v{installed_version} (up to date)"
        )


def _pip_install_package(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Reinstall trw-mcp package from the source tree.

    Uses the trw-mcp directory that contains the bundled data, ensuring
    the installed package matches the source version.
    """
    # The package source is the parent of the data directory
    package_dir = _DATA_DIR.parent.parent.parent  # trw-mcp/src -> trw-mcp/
    if not (package_dir / "pyproject.toml").exists():
        # Fall back: try to find trw-mcp relative to data dir
        package_dir = _DATA_DIR.parent.parent.parent
        if not (package_dir / "pyproject.toml").exists():
            result["errors"].append(
                "Cannot find trw-mcp pyproject.toml for pip install. "
                "Manually run: pip install -e /path/to/trw-mcp[dev]"
            )
            return

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", f"{package_dir}[dev]"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode == 0:
            result["updated"].append(f"pip install trw-mcp (reinstalled)")
        else:
            result["errors"].append(
                f"pip install failed (exit {proc.returncode}): {proc.stderr[:200]}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        result["errors"].append(f"pip install failed: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_dir(path: Path, result: dict[str, list[str]]) -> None:
    """Create directory if it doesn't exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        result["created"].append(str(path) + "/")
    # Already existing dirs are silently fine -- not worth reporting as "skipped".


def _copy_file(
    src: Path,
    dest: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Copy *src* to *dest* with idempotency."""
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        return
    try:
        shutil.copy2(src, dest)
        # Ensure shell scripts are executable (pip install may strip permissions)
        if dest.suffix == ".sh":
            executable = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            os.chmod(dest, os.stat(dest).st_mode | executable)
        result["created"].append(str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to copy {src} -> {dest}: {exc}")


def _write_if_missing(
    dest: Path,
    content: str,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Write *content* to *dest* if it doesn't exist (or *force* is True)."""
    if dest.exists() and not force:
        result["skipped"].append(str(dest))
        return
    try:
        dest.write_text(content, encoding="utf-8")
        result["created"].append(str(dest))
    except OSError as exc:
        result["errors"].append(f"Failed to write {dest}: {exc}")


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def _default_config(
    *,
    source_package: str = "",
    test_path: str = "",
) -> str:
    """Generate default ``.trw/config.yaml``.

    Args:
        source_package: If set, adds ``source_package_name`` field.
        test_path: If set, adds ``tests_relative_path`` field.
    """
    lines = [
        "# TRW Framework Configuration",
        "# See trw://config resource for all available fields.",
        "task_root: docs",
        "debug: false",
        "claude_md_max_lines: 300",
    ]
    if source_package:
        lines.append(f"source_package_name: {source_package}")
    if test_path:
        lines.append(f"tests_relative_path: {test_path}")
    return "\n".join(lines) + "\n"


def _generate_mcp_json() -> str:
    """Generate ``.mcp.json`` pointing to installed trw-mcp."""
    cmd = shutil.which("trw-mcp") or f"{sys.executable} -m trw_mcp.server"
    return json.dumps({"mcpServers": {"trw": {"command": cmd, "args": ["--debug"]}}}, indent=2) + "\n"


def _minimal_claude_md() -> str:
    """Generate ``CLAUDE.md`` with behavioral protocol and tool reference."""
    return """\
# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What This Is

{Describe your project here}

## Build & Test Commands

```bash
# Add your project's build and test commands here
```

## Project Conventions

{Add project-specific conventions here}

<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

TRW tools help you build effectively and preserve your work across sessions:
- **Start**: call `trw_session_start()` to load prior learnings and recover any active run
- **Finish**: call `trw_deliver()` to persist your learnings for future sessions

## TRW Behavioral Protocol (Auto-Generated)

- `trw_session_start()` loads your prior learnings and recovers any active run — call it first so you have full context before writing code
- `trw_status()` shows your current phase, completed work, and next steps — call it when resuming so you pick up where you left off instead of redoing work
- `trw_init(task_name)` creates your run directory and event log — call it for new tasks so checkpoints and progress tracking work
- `trw_checkpoint(message)` saves your implementation progress — call it after each milestone so you can resume here if context compacts, instead of re-implementing from scratch
- `trw_learn(summary, detail)` records discoveries for all future sessions — call it when you hit errors or find gotchas so no agent repeats your mistakes
- `trw_claude_md_sync()` promotes your best learnings into CLAUDE.md — call it at delivery so the next session starts with your insights built in
- For quick tasks without a run: `trw_recall()` gives you relevant prior learnings at the start, `trw_learn()` saves new ones for next time

## TRW Ceremony Tools (Auto-Generated)

### Execution Phases

```
RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
```

### Tool Lifecycle

| Phase | Tool | When to Use |
|-------|------|-------------|
| Start | `trw_session_start` | At session start — loads learnings + run state |
| Start | `trw_recall` | Quick tasks — retrieves relevant prior learnings |
| Start | `trw_status` | When resuming — shows phase, progress, next steps |
| RESEARCH | `trw_init` | New tasks — creates run directory for tracking |
| Any | `trw_learn` | On errors/discoveries — saves for future sessions |
| Any | `trw_checkpoint` | After milestones — preserves progress across compactions |
| VALIDATE | `trw_build_check` | Before delivery — runs pytest + mypy |
| DELIVER | `trw_claude_md_sync` | At delivery — promotes learnings to CLAUDE.md |
| DELIVER | `trw_deliver` | At task completion — persists everything in one call |

### Example Flows

**Quick Task** (no run needed):
```
trw_session_start -> work -> trw_learn (if discovery) -> trw_deliver()
```

**Full Run**:
```
trw_session_start -> trw_init(task_name, prd_scope)
  -> work + trw_checkpoint (periodic) + trw_learn (discoveries)
  -> trw_build_check(scope='full')
  -> trw_deliver()
```

<!-- trw:end -->
"""
