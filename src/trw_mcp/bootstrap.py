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


def _get_data_path(filename: str) -> Path:
    """Get path to a bundled data file."""
    return _DATA_DIR / filename


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

    # 1. Create .trw/ structure
    _ensure_dir(target_dir / ".trw" / "frameworks", result)
    _ensure_dir(target_dir / ".trw" / "context", result)
    _ensure_dir(target_dir / ".trw" / "templates", result)
    _ensure_dir(target_dir / ".trw" / "learnings" / "entries", result)
    _ensure_dir(target_dir / ".trw" / "scripts", result)

    # 2. Copy framework files
    _copy_data_file(
        "framework.md",
        target_dir / ".trw" / "frameworks" / "FRAMEWORK.md",
        force,
        result,
    )
    _copy_data_file(
        "behavioral_protocol.yaml",
        target_dir / ".trw" / "context" / "behavioral_protocol.yaml",
        force,
        result,
    )
    _copy_data_file(
        "templates/claude_md.md",
        target_dir / ".trw" / "templates" / "claude_md.md",
        force,
        result,
    )

    # 3. Create .trw/config.yaml with defaults
    _write_if_missing(
        target_dir / ".trw" / "config.yaml", _default_config(), force, result
    )

    # 4. Create .trw/learnings/index.yaml
    _write_if_missing(
        target_dir / ".trw" / "learnings" / "index.yaml",
        "entries: []\n",
        force,
        result,
    )

    # 4b. Create .trw/.gitignore from bundled template
    _copy_file(
        _get_data_path("gitignore.txt"),
        target_dir / ".trw" / ".gitignore",
        force,
        result,
    )

    # 5. Copy .claude/ hooks and settings
    _ensure_dir(target_dir / ".claude" / "hooks", result)
    _copy_data_file(
        "settings.json",
        target_dir / ".claude" / "settings.json",
        force,
        result,
    )

    # Copy all hook scripts
    hooks_source = _get_data_path("hooks")
    if hooks_source.is_dir():
        for hook_file in sorted(hooks_source.iterdir()):
            if hook_file.suffix == ".sh":
                dest = target_dir / ".claude" / "hooks" / hook_file.name
                _copy_file(hook_file, dest, force, result)

    # 6. Generate .mcp.json
    _write_if_missing(target_dir / ".mcp.json", _generate_mcp_json(), force, result)

    # 7. Generate minimal CLAUDE.md
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
    # Already existing dirs are silently fine — not worth reporting as "skipped".


def _copy_data_file(
    data_name: str,
    dest: Path,
    force: bool,
    result: dict[str, list[str]],
) -> None:
    """Copy a bundled data file to *dest*."""
    src = _get_data_path(data_name)
    _copy_file(src, dest, force, result)


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
    return (
        "# TRW Framework Configuration\n"
        "# See trw://config resource for all available fields.\n"
        "task_root: docs\n"
        "debug: false\n"
        "claude_md_max_lines: 300\n"
    )


def _generate_mcp_json() -> str:
    """Generate ``.mcp.json`` pointing to installed trw-mcp."""
    trw_binary = shutil.which("trw-mcp")
    if trw_binary:
        cmd = trw_binary
    else:
        cmd = f"{sys.executable} -m trw_mcp.server"

    config = {"mcpServers": {"trw": {"command": cmd, "args": ["--debug"]}}}
    return json.dumps(config, indent=2) + "\n"


def _minimal_claude_md() -> str:
    """Generate minimal ``CLAUDE.md`` with behavioral protocol header."""
    return (
        "# CLAUDE.md\n\n"
        "Before starting work: execute `trw_session_start()`.\n"
        "After completing work: execute `trw_deliver()`.\n\n"
        "## What This Is\n\n"
        "{Describe your project here}\n\n"
        "## Build & Test Commands\n\n"
        "```bash\n"
        "# Add your project's build and test commands here\n"
        "```\n"
    )
