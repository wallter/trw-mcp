"""Project bootstrap — sets up TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
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

CRITICAL — YOU MUST EXECUTE THESE TOOLS:
- **BEFORE ANY WORK**: ALWAYS call `trw_session_start()` (or `trw_recall('*', min_impact=0.7)` for quick tasks). NEVER skip this step.
- **AFTER COMPLETING WORK**: ALWAYS call `trw_deliver()` (or `trw_claude_md_sync` for quick tasks). NEVER skip this step.

## TRW Behavioral Protocol (Auto-Generated)

- ALWAYS execute `trw_session_start()` FIRST — server enforces this with warnings on every response if skipped
- ALWAYS execute `trw_status()` when resuming a run — without this, you WILL overwrite in-progress work
- ALWAYS execute `trw_init` to bootstrap run directory for new tasks
- ALWAYS execute `trw_checkpoint` during implementation — without these, progress is lost on failure
- After errors or >2 retries: ALWAYS execute `trw_learn` to record the discovery — unrecorded mistakes WILL recur
- ALWAYS execute `trw_claude_md_sync` at delivery to persist learnings — without this, next session starts blind
- Quick tasks (no run): ALWAYS use `trw_recall` at start, `trw_learn` for discoveries

## TRW Ceremony Tools (Auto-Generated)

### Execution Phases

```
RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
```

### Tool Lifecycle

| Phase | Tool | When to Use |
|-------|------|-------------|
| Start | `trw_session_start` | ALWAYS at session start |
| Start | `trw_recall` | ALWAYS for quick tasks (no run) |
| Start | `trw_status` | ALWAYS when resuming a run |
| RESEARCH | `trw_init` | ALWAYS for new tasks |
| Any | `trw_learn` | ALWAYS on errors/discoveries |
| Any | `trw_checkpoint` | Every milestone / ~10min |
| VALIDATE | `trw_build_check` | ALWAYS before delivery |
| DELIVER | `trw_claude_md_sync` | ALWAYS at delivery |
| DELIVER | `trw_deliver` | ALWAYS at task completion |

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
