"""Subprocess utilities for build verification.

Low-level helpers for running subprocesses, stripping ANSI codes,
finding executables, and extracting failure lines from output.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config

logger = structlog.get_logger(__name__)

# Strip ANSI escape codes from subprocess output (PRD-CORE-023 RISK-009)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_MAX_FAILURES = 10


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def _find_executable(name: str, project_root: Path) -> str | None:
    """Locate a tool in the project venv first, then fallback to PATH.

    Venv-first resolution prevents using a system-level binary that
    lacks project dependencies (e.g., system pytest without trw_mcp).

    Resolution order:
    1. {source_package_path}/../.venv/bin/{name} (monorepo package venv)
    2. {project_root}/.venv/bin/{name}
    3. {project_root}/venv/bin/{name}
    4. PATH lookup (shutil.which) — last resort

    Cross-platform: on Windows, also checks Scripts/{name}.exe.

    Args:
        name: Executable name (e.g. "pytest", "mypy").
        project_root: Project root directory.

    Returns:
        Resolved path string, or None if not found.
    """
    import sys

    config = get_config()
    source_path = config.source_package_path or "trw-mcp/src"

    # 1. Monorepo package venv (e.g., trw-mcp/.venv/bin/pytest)
    pkg_dir = project_root / Path(source_path).parent
    for bin_dir in ("bin", "Scripts"):
        venv_path = pkg_dir / ".venv" / bin_dir / name
        if venv_path.exists():
            return str(venv_path)
        # Windows .exe variant
        if sys.platform == "win32":
            exe_path = pkg_dir / ".venv" / bin_dir / f"{name}.exe"
            if exe_path.exists():
                return str(exe_path)

    # 2-3. Project root venv
    for venv_name in (".venv", "venv"):
        for bin_dir in ("bin", "Scripts"):
            candidate = project_root / venv_name / bin_dir / name
            if candidate.exists():
                return str(candidate)
            if sys.platform == "win32":
                exe_candidate = project_root / venv_name / bin_dir / f"{name}.exe"
                if exe_candidate.exists():
                    return str(exe_candidate)

    # 4. PATH fallback (may find system-level binary)
    path = shutil.which(name)
    if path is not None:
        return path

    return None


def _run_subprocess(
    cmd: list[str],
    cwd: Path,
    timeout_secs: int,
) -> subprocess.CompletedProcess[str] | str:
    """Run a subprocess, returning the result or an error message string.

    Args:
        cmd: Command and arguments.
        cwd: Working directory.
        timeout_secs: Maximum seconds before timeout.

    Returns:
        CompletedProcess on success, or an error message string on failure.
    """
    try:
        return subprocess.run(  # noqa: S603 — shell=False (default); cmd is a fully-resolved build tool path from _find_executable() with validated args
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        return f"{cmd[0]} timed out after {timeout_secs}s"
    except OSError:
        return f"{cmd[0]} executable not found"


def _extract_failures(
    output: str,
    markers: tuple[str, ...],
) -> list[str]:
    """Extract failure lines from subprocess output.

    Args:
        output: Combined stdout+stderr text (ANSI-stripped).
        markers: Substrings that identify failure lines (matched with ``in``).

    Returns:
        Up to _MAX_FAILURES matching lines, each truncated to 200 chars.
    """
    failures: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if any(m in stripped for m in markers):
            failures.append(stripped[:200])
            if len(failures) >= _MAX_FAILURES:
                break
    return failures


def _collect_failures(result: dict[str, object]) -> list[str]:
    """Safely extract the failures list from a subprocess result dict.

    Handles the build result dict by checking isinstance
    before extending, as required by mypy --strict.
    """
    raw = result.get("failures", [])
    if isinstance(raw, list):
        return [str(f) for f in raw]
    return []


def _run_audit_tool(
    cmd: list[str],
    cwd: Path,
    timeout_secs: int,
    tool_name: str,
) -> object:
    """Run an audit tool subprocess and parse its JSON output.

    Shared helper for pip-audit and npm audit -- handles subprocess
    execution, error handling, and JSON parsing. Returns parsed JSON
    data on success, or a skip dict on failure.

    Args:
        cmd: Command and arguments (e.g. ``["pip-audit", "--json"]``).
        cwd: Working directory for the subprocess.
        timeout_secs: Maximum seconds before timeout.
        tool_name: Human-readable tool name for skip reasons
            (e.g. ``"pip-audit"``, ``"npm audit"``).

    Returns:
        Parsed JSON data (any type) on success, or a dict with
        ``{tool_name}_skipped=True`` and ``{tool_name}_skip_reason``
        on failure. Callers distinguish success from failure by
        checking for the ``_skipped`` key.
    """
    # Derive the key prefix from tool_name (e.g. "pip-audit" -> "pip_audit")
    prefix = tool_name.replace("-", "_").replace(" ", "_")

    result = _run_subprocess(cmd, cwd, timeout_secs)

    if isinstance(result, str):
        return {
            f"{prefix}_skipped": True,
            f"{prefix}_skip_reason": result,
        }

    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {
            f"{prefix}_skipped": True,
            f"{prefix}_skip_reason": f"invalid JSON from {tool_name}",
        }
