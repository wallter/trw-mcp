"""Shared bootstrap utilities — file operations, config generators, MCP config.

These helpers have NO bootstrap-specific workflow logic; they are pure
utility functions used by both ``_init_project`` and ``_update_project``.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

# Resolve to ``src/trw_mcp/data/``.
# When this file lived at ``src/trw_mcp/bootstrap.py``, the path was
# ``Path(__file__).parent / "data"``.  Now that it lives at
# ``src/trw_mcp/bootstrap/_utils.py``, we need one extra ``.parent``.
_DATA_DIR = Path(__file__).parent.parent / "data"


# ---------------------------------------------------------------------------
# File-level helpers
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


def _files_identical(a: Path, b: Path) -> bool:
    """Compare two files by SHA-256 hash for dry-run diffing."""
    try:
        ha = hashlib.sha256(a.read_bytes()).hexdigest()
        hb = hashlib.sha256(b.read_bytes()).hexdigest()
        return ha == hb
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Config generators
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
        "claude_md_max_lines: 500",
    ]
    if source_package:
        lines.append(f"source_package_name: {source_package}")
    if test_path:
        lines.append(f"tests_relative_path: {test_path}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# MCP server config helpers
# ---------------------------------------------------------------------------


def _trw_mcp_server_entry() -> dict[str, object]:
    """Build the ``trw`` MCP server entry for .mcp.json.

    Always returns a portable command that resolves via $PATH.
    No absolute paths are written — this ensures .mcp.json works
    across machines and venvs (PRD-FIX-037).
    """
    if shutil.which("trw-mcp"):
        return {"command": "trw-mcp", "args": ["--debug"]}
    # Fallback: use bare python -m invocation (no absolute paths)
    return {"command": "python", "args": ["-m", "trw_mcp.server", "--debug"]}


def _merge_mcp_json(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Ensure ``.mcp.json`` has the ``trw`` server entry.

    Reads existing .mcp.json, merges the ``trw`` key into ``mcpServers``
    while preserving all other user-configured servers, and writes back.
    Creates the file from scratch if it doesn't exist.

    Always generates stdio format entries (PRD-CORE-070-FR04). HTTP
    transport is handled internally by the server's auto-start + proxy.
    """
    mcp_path = target_dir / ".mcp.json"
    trw_entry = _trw_mcp_server_entry()

    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        servers = data.get("mcpServers", {})
        if not isinstance(servers, dict):
            servers = {}
        existed = "trw" in servers
        servers["trw"] = trw_entry
        data["mcpServers"] = servers
        try:
            mcp_path.write_text(
                json.dumps(data, indent=2) + "\n", encoding="utf-8",
            )
            key = "updated" if "updated" in result else "created"
            if existed:
                result[key].append(str(mcp_path))
            else:
                result[key].append(f"{mcp_path} (added trw entry)")
        except OSError as exc:
            result["errors"].append(f"Failed to write {mcp_path}: {exc}")
    else:
        content = json.dumps(
            {"mcpServers": {"trw": trw_entry}}, indent=2,
        ) + "\n"
        try:
            mcp_path.write_text(content, encoding="utf-8")
            result["created"].append(str(mcp_path))
        except OSError as exc:
            result["errors"].append(f"Failed to write {mcp_path}: {exc}")


def _generate_mcp_json() -> str:
    """Generate ``.mcp.json`` pointing to installed trw-mcp.

    Legacy helper kept for backward compatibility. New code uses
    ``_merge_mcp_json()`` which does smart merging.
    """
    entry = _trw_mcp_server_entry()
    return json.dumps({"mcpServers": {"trw": entry}}, indent=2) + "\n"


# ---------------------------------------------------------------------------
# Installer metadata & verification
# ---------------------------------------------------------------------------


def _write_installer_metadata(
    target_dir: Path,
    action: str,
    result: dict[str, list[str]],
) -> None:
    """Write ``.trw/installer-meta.yaml`` with deployment metadata.

    Tracks framework version, package version, timestamp, and artifact
    counts so audits can detect stale deployments.
    """
    from trw_mcp import __version__ as pkg_version
    from trw_mcp.models.config import get_config

    config = get_config()

    # Count deployed artifacts
    hooks_dir = target_dir / ".claude" / "hooks"
    skills_dir = target_dir / ".claude" / "skills"
    agents_dir = target_dir / ".claude" / "agents"
    hooks_count = len(list(hooks_dir.glob("*.sh"))) if hooks_dir.is_dir() else 0
    skills_count = len([d for d in skills_dir.iterdir() if d.is_dir()]) if skills_dir.is_dir() else 0
    agents_count = len(list(agents_dir.glob("*.md"))) if agents_dir.is_dir() else 0

    meta = {
        "framework_version": config.framework_version,
        "package_version": pkg_version,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "installed_by": f"trw-mcp {action}",
        "hooks_count": hooks_count,
        "skills_count": skills_count,
        "agents_count": agents_count,
    }
    meta_path = target_dir / ".trw" / "installer-meta.yaml"
    try:
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        writer.write_yaml(meta_path, meta)
        # init_project uses "created", update_project uses "updated"
        key = "updated" if "updated" in result else "created"
        result[key].append(str(meta_path))
    except OSError as exc:
        result["errors"].append(f"Failed to write {meta_path}: {exc}")


def _verify_installation(
    target_dir: Path,
    result: dict[str, list[str]],
) -> None:
    """Run lightweight post-update health checks.

    Verifies hooks are executable, .mcp.json has trw entry, and
    CLAUDE.md has TRW markers.  Adds warnings for any failures.
    """
    # Check hooks are executable
    hooks_dir = target_dir / ".claude" / "hooks"
    if hooks_dir.is_dir():
        for hook in hooks_dir.glob("*.sh"):
            if not os.access(hook, os.X_OK):
                result["warnings"].append(f"Hook not executable: {hook.name}")

    # Check .mcp.json has trw entry
    mcp_path = target_dir / ".mcp.json"
    if mcp_path.exists():
        try:
            data = json.loads(mcp_path.read_text(encoding="utf-8"))
            if "trw" not in data.get("mcpServers", {}):
                result["warnings"].append(
                    ".mcp.json missing 'trw' server entry"
                )
        except (json.JSONDecodeError, OSError):
            result["warnings"].append(".mcp.json is not valid JSON")
    else:
        result["warnings"].append(".mcp.json not found")

    # Check CLAUDE.md has TRW markers
    from trw_mcp.bootstrap._update_project import _TRW_END_MARKER, _TRW_START_MARKER

    claude_md = target_dir / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text(encoding="utf-8")
        if _TRW_START_MARKER not in content or _TRW_END_MARKER not in content:
            result["warnings"].append(
                "CLAUDE.md missing TRW auto-generated markers"
            )


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
    # Look up _DATA_DIR through the package module so that
    # patch("trw_mcp.bootstrap._DATA_DIR", ...) in tests works.
    _data_dir = sys.modules["trw_mcp.bootstrap"]._DATA_DIR

    # The package source is the parent of the data directory
    # _data_dir = .../trw-mcp/src/trw_mcp/data -> .parent x3 = trw-mcp/
    package_dir = _data_dir.parent.parent.parent
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
            result["updated"].append("pip install trw-mcp (reinstalled)")
        else:
            result["errors"].append(
                f"pip install failed (exit {proc.returncode}): {proc.stderr[:200]}"
            )
    except (subprocess.TimeoutExpired, OSError) as exc:
        result["errors"].append(f"pip install failed: {exc}")


# ---------------------------------------------------------------------------
# CLAUDE.md content generators
# ---------------------------------------------------------------------------


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
| VALIDATE | `trw_build_check` | Before delivery — runs tests + type-check |
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
