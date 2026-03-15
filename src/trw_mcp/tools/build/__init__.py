"""TRW build verification gate tool — PRD-CORE-023, PRD-QUAL-025/028/029.

Runs the project's test suite and type checker via subprocess, caches results
to .trw/context/build-status.yaml, and returns BuildStatus. Defaults to
pytest + mypy for Python projects but supports configurable test/type-check
commands. Phase gates consume cached status — they never run subprocesses.

Extended with mutation testing (QUAL-025), dependency audit (QUAL-028),
and API fuzz (QUAL-029) scopes.

This package re-exports all public names for backward compatibility.
Internal modules:
  _subprocess  — ANSI stripping, executable finder, subprocess runner
  _runners     — pytest and mypy execution and output parsing
  _audit       — dependency audit (pip/npm) and API fuzz
  _core        — run_build_check orchestration and caching
  _registration — MCP tool registration
"""

from __future__ import annotations

from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

# Re-export public API for backward compatibility
from trw_mcp.tools.build._audit import (
    _API_FUZZ_FILE,
    _DEP_AUDIT_FILE,
    _detect_unlisted_imports,
    _run_api_fuzz,
    _run_dep_audit,
    _run_npm_audit,
    _run_pip_audit,
)
from trw_mcp.tools.build._core import (
    _cache_to_context,
    cache_build_status,
    run_build_check,
)
from trw_mcp.tools.build._registration import register_build_tools
from trw_mcp.tools.build._runners import (
    _COVERAGE_RE,
    _PYTEST_SUMMARY_RE,
    _pytest_error,
    _run_mypy,
    _run_pytest,
)
from trw_mcp.tools.build._subprocess import (
    _ANSI_RE,
    _MAX_FAILURES,
    _collect_failures,
    _extract_failures,
    _find_executable,
    _run_audit_tool,
    _run_subprocess,
    _strip_ansi,
)

__all__ = [
    "_ANSI_RE",
    "_API_FUZZ_FILE",
    "_COVERAGE_RE",
    "_DEP_AUDIT_FILE",
    "_MAX_FAILURES",
    "_PYTEST_SUMMARY_RE",
    "_cache_to_context",
    "_collect_failures",
    "_detect_unlisted_imports",
    "_extract_failures",
    "_find_executable",
    "_pytest_error",
    "_run_api_fuzz",
    "_run_audit_tool",
    "_run_dep_audit",
    "_run_mypy",
    "_run_npm_audit",
    "_run_pip_audit",
    "_run_pytest",
    "_run_subprocess",
    "_strip_ansi",
    "cache_build_status",
    "register_build_tools",
    "resolve_project_root",
    "resolve_trw_dir",
    "run_build_check",
]
