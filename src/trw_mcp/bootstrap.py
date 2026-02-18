"""Project bootstrap — sets up TRW framework in a target directory.

PRD-INFRA-006: ``trw-mcp init-project`` CLI command that copies all
required framework files into a target git repository.
"""

from __future__ import annotations

import json
import shutil
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
) -> dict[str, list[str]]:
    """Bootstrap TRW framework in *target_dir*.

    Args:
        target_dir: Root of the target git repository.
        force: If ``True``, overwrite existing files.

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
        target_dir / ".trw" / "config.yaml", _default_config(), force, result
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

    # 5. Generate root-level files
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


def _default_config() -> str:
    """Generate default ``.trw/config.yaml``."""
    return """\
# TRW Framework Configuration
# See trw://config resource for all available fields.
task_root: docs
debug: false
claude_md_max_lines: 300
"""


def _generate_mcp_json() -> str:
    """Generate ``.mcp.json`` pointing to installed trw-mcp."""
    cmd = shutil.which("trw-mcp") or f"{sys.executable} -m trw_mcp.server"
    return json.dumps({"mcpServers": {"trw": {"command": cmd, "args": ["--debug"]}}}, indent=2) + "\n"


def _minimal_claude_md() -> str:
    """Generate minimal ``CLAUDE.md`` with behavioral protocol header."""
    return """\
# CLAUDE.md

Before starting work: execute `trw_session_start()`.
After completing work: execute `trw_deliver()`.

## What This Is

{Describe your project here}

## Build & Test Commands

```bash
# Add your project's build and test commands here
```
"""
