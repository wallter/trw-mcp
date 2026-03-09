"""Pytest and mypy runner implementations.

Handles executing pytest and mypy via subprocess, parsing their output
for test counts, coverage percentages, and failure details.
"""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.models.config import get_config
from trw_mcp.tools.build._subprocess import (
    _extract_failures,
    _find_executable,
    _run_subprocess,
    _strip_ansi,
)

_config = get_config()

# Parse pytest summary line: "X passed, Y failed, Z errors" etc.
_PYTEST_SUMMARY_RE = re.compile(
    r"(\d+)\s+passed"
    r"(?:.*?(\d+)\s+failed)?"
    r"(?:.*?(\d+)\s+error)?"
)

# Parse coverage TOTAL line: "TOTAL    NN    NN    NN%"
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+(?:\.\d+)?)%")

_MAX_FAILURES = 10


def _pytest_error(message: str) -> dict[str, object]:
    """Build a failed pytest result dict with the given error message."""
    return {
        "tests_passed": False,
        "coverage_pct": 0.0,
        "test_count": 0,
        "failure_count": 0,
        "failures": [message],
    }


def _run_pytest(
    project_root: Path,
    timeout_secs: int,
    extra_args: str,
) -> dict[str, object]:
    """Run pytest via subprocess and parse results.

    Args:
        project_root: Project root directory.
        timeout_secs: Maximum seconds before timeout.
        extra_args: Extra CLI arguments from config.

    Returns:
        Dict with tests_passed, coverage_pct, test_count, failure_count, failures.
    """
    # Custom test command takes precedence over auto-resolved pytest
    custom_cmd = _config.build_check_pytest_cmd
    if custom_cmd:
        result = _run_subprocess(
            custom_cmd.split(), project_root, timeout_secs,
        )
        if isinstance(result, str):
            return _pytest_error(result)
        output = _strip_ansi(result.stdout + "\n" + result.stderr)
        return {
            "tests_passed": result.returncode == 0,
            "coverage_pct": 0.0,
            "test_count": 0,
            "failure_count": 0 if result.returncode == 0 else 1,
            "failures": _extract_failures(output, ("FAILED ", "ERROR ")),
        }

    pytest_path = _find_executable("pytest", project_root)
    if pytest_path is None:
        return _pytest_error("pytest not found — install with: pip install pytest")

    # Derive build root and cwd from config (PRD-INFRA-011-FR01)
    source_path = _config.source_package_path or "trw-mcp/src"
    build_root = str(Path(source_path).parent)
    cwd = project_root / build_root

    # Test directory relative to cwd (strip build_root prefix)
    tests_full = _config.tests_relative_path or "trw-mcp/tests"
    test_dir = tests_full.removeprefix(build_root + "/") if build_root != "." else tests_full
    if not test_dir.endswith("/"):
        test_dir += "/"

    cov_target = _config.source_package_name or "trw_mcp"

    cmd = [
        pytest_path,
        test_dir,
        "-v",
        f"--cov={cov_target}",
        "--cov-report=term-missing",
        "--tb=line",
        f"--maxfail={_MAX_FAILURES}",
    ]
    if extra_args:
        cmd.extend(extra_args.split())

    result = _run_subprocess(cmd, cwd, timeout_secs)
    if isinstance(result, str):
        return _pytest_error(result)

    output = _strip_ansi(result.stdout + "\n" + result.stderr)

    # Parse coverage
    coverage_pct = 0.0
    cov_match = _COVERAGE_RE.search(output)
    if cov_match:
        coverage_pct = float(cov_match.group(1))

    # Parse test counts from summary line
    test_count = 0
    failure_count = 0
    summary_match = _PYTEST_SUMMARY_RE.search(output)
    if summary_match:
        passed = int(summary_match.group(1))
        failed = int(summary_match.group(2) or 0)
        errors = int(summary_match.group(3) or 0)
        test_count = passed + failed + errors
        failure_count = failed + errors

    return {
        "tests_passed": result.returncode == 0,
        "coverage_pct": coverage_pct,
        "test_count": test_count,
        "failure_count": failure_count,
        "failures": _extract_failures(output, ("FAILED ", "ERROR ")),
    }


def _run_mypy(
    project_root: Path,
    timeout_secs: int,
    extra_args: str,
) -> dict[str, object]:
    """Run mypy via subprocess and parse results.

    Args:
        project_root: Project root directory.
        timeout_secs: Maximum seconds before timeout.
        extra_args: Extra CLI arguments from config.

    Returns:
        Dict with mypy_clean and failures list.
    """
    mypy_path = _find_executable("mypy", project_root)
    if mypy_path is None:
        return {
            "mypy_clean": False,
            "failures": ["mypy not found — install with: pip install mypy"],
        }

    # Derive build root and cwd from config (PRD-INFRA-011-FR02)
    source_path = _config.source_package_path or "trw-mcp/src"
    build_root = str(Path(source_path).parent)
    cwd = project_root / build_root

    # Source target relative to cwd
    src_rel = source_path.removeprefix(build_root + "/") if build_root != "." else source_path
    pkg_name = _config.source_package_name or "trw_mcp"
    src_target = f"{src_rel}/{pkg_name}/"

    cmd = [mypy_path]
    if extra_args:
        cmd.extend(extra_args.split())
    cmd.append(src_target)

    result = _run_subprocess(cmd, cwd, timeout_secs)
    if isinstance(result, str):
        return {"mypy_clean": False, "failures": [result]}

    output = _strip_ansi(result.stdout + "\n" + result.stderr)
    mypy_clean = result.returncode == 0
    failures = _extract_failures(output, (": error:",)) if not mypy_clean else []
    return {"mypy_clean": mypy_clean, "failures": failures}


def __reload_hook__() -> None:
    """Reset module-level caches on mcp-hmr hot-reload."""
    from trw_mcp.models.config import _reset_config

    global _config
    _reset_config()
    _config = get_config()
