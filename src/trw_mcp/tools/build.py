"""TRW build verification gate tool — PRD-CORE-023.

Runs pytest and mypy via subprocess, caches results to
.trw/context/build-status.yaml, and returns BuildStatus.
Phase gates consume cached status — they never run subprocesses.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import TRWConfig
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import FileStateWriter, model_to_dict

logger = structlog.get_logger()

_config = TRWConfig()
_writer = FileStateWriter()

# Strip ANSI escape codes from subprocess output (PRD-CORE-023 RISK-009)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Parse pytest summary line: "X passed, Y failed, Z errors" etc.
_PYTEST_SUMMARY_RE = re.compile(
    r"(\d+)\s+passed"
    r"(?:.*?(\d+)\s+failed)?"
    r"(?:.*?(\d+)\s+error)?"
)

# Parse coverage TOTAL line: "TOTAL    NN    NN    NN%"
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+(?:\.\d+)?)%")

_MAX_FAILURES = 10


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


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
    pytest_path = shutil.which("pytest")
    if pytest_path is None:
        # Try project venv
        venv_pytest = project_root / "trw-mcp" / ".venv" / "bin" / "pytest"
        if venv_pytest.exists():
            pytest_path = str(venv_pytest)
        else:
            return {
                "tests_passed": False,
                "coverage_pct": 0.0,
                "test_count": 0,
                "failure_count": 0,
                "failures": ["pytest not found — install with: pip install pytest"],
            }

    cmd = [
        pytest_path,
        "tests/",
        "-v",
        "--cov=trw_mcp",
        "--cov-report=term-missing",
        "--tb=line",
        f"--maxfail={_MAX_FAILURES}",
    ]

    if extra_args:
        cmd.extend(extra_args.split())

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            cwd=str(project_root / "trw-mcp"),
        )
    except subprocess.TimeoutExpired:
        return {
            "tests_passed": False,
            "coverage_pct": 0.0,
            "test_count": 0,
            "failure_count": 0,
            "failures": [f"pytest timed out after {timeout_secs}s"],
        }
    except FileNotFoundError:
        return {
            "tests_passed": False,
            "coverage_pct": 0.0,
            "test_count": 0,
            "failure_count": 0,
            "failures": ["pytest executable not found"],
        }

    output = _strip_ansi(result.stdout + "\n" + result.stderr)
    tests_passed = result.returncode == 0

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

    # Extract failure lines
    failures: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAILED ") or stripped.startswith("ERROR "):
            failures.append(stripped[:200])
            if len(failures) >= _MAX_FAILURES:
                break

    return {
        "tests_passed": tests_passed,
        "coverage_pct": coverage_pct,
        "test_count": test_count,
        "failure_count": failure_count,
        "failures": failures,
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
    mypy_path = shutil.which("mypy")
    if mypy_path is None:
        venv_mypy = project_root / "trw-mcp" / ".venv" / "bin" / "mypy"
        if venv_mypy.exists():
            mypy_path = str(venv_mypy)
        else:
            return {
                "mypy_clean": False,
                "failures": ["mypy not found — install with: pip install mypy"],
            }

    cmd = [mypy_path]
    if extra_args:
        cmd.extend(extra_args.split())
    cmd.append("src/trw_mcp/")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
            cwd=str(project_root / "trw-mcp"),
        )
    except subprocess.TimeoutExpired:
        return {
            "mypy_clean": False,
            "failures": [f"mypy timed out after {timeout_secs}s"],
        }
    except FileNotFoundError:
        return {
            "mypy_clean": False,
            "failures": ["mypy executable not found"],
        }

    output = _strip_ansi(result.stdout + "\n" + result.stderr)
    mypy_clean = result.returncode == 0

    failures: list[str] = []
    if not mypy_clean:
        for line in output.splitlines():
            stripped = line.strip()
            if ": error:" in stripped:
                failures.append(stripped[:200])
                if len(failures) >= _MAX_FAILURES:
                    break

    return {"mypy_clean": mypy_clean, "failures": failures}


def run_build_check(
    project_root: Path,
    scope: str = "full",
    timeout_secs: int = 300,
    pytest_args: str = "",
    mypy_args: str = "--strict",
) -> BuildStatus:
    """Execute build verification and return BuildStatus.

    Args:
        project_root: Project root directory.
        scope: Check scope — 'full', 'pytest', 'mypy'.
        timeout_secs: Maximum seconds per subprocess.
        pytest_args: Extra pytest CLI arguments.
        mypy_args: Extra mypy CLI arguments.

    Returns:
        Populated BuildStatus with all results.
    """
    start = time.monotonic()
    tests_passed = True
    mypy_clean = True
    coverage_pct = 0.0
    test_count = 0
    failure_count = 0
    all_failures: list[str] = []

    if scope in ("full", "pytest", "quick"):
        pytest_result = _run_pytest(project_root, timeout_secs, pytest_args)
        tests_passed = bool(pytest_result["tests_passed"])
        coverage_pct = float(str(pytest_result["coverage_pct"]))
        test_count = int(str(pytest_result["test_count"]))
        failure_count = int(str(pytest_result["failure_count"]))
        pytest_failures = pytest_result.get("failures", [])
        if isinstance(pytest_failures, list):
            all_failures.extend(str(f) for f in pytest_failures)

    if scope in ("full", "mypy"):
        mypy_result = _run_mypy(project_root, timeout_secs, mypy_args)
        mypy_clean = bool(mypy_result["mypy_clean"])
        mypy_failures = mypy_result.get("failures", [])
        if isinstance(mypy_failures, list):
            all_failures.extend(str(f) for f in mypy_failures)

    duration = time.monotonic() - start

    return BuildStatus(
        tests_passed=tests_passed,
        mypy_clean=mypy_clean,
        coverage_pct=coverage_pct,
        test_count=test_count,
        failure_count=failure_count,
        failures=all_failures[:_MAX_FAILURES],
        timestamp=datetime.now(timezone.utc).isoformat(),
        scope=scope,
        duration_secs=round(duration, 2),
    )


def cache_build_status(trw_dir: Path, status: BuildStatus) -> Path:
    """Write BuildStatus to .trw/context/build-status.yaml.

    Args:
        trw_dir: Path to .trw directory.
        status: BuildStatus to cache.

    Returns:
        Path to the cached file.
    """
    context_dir = trw_dir / "context"
    _writer.ensure_dir(context_dir)
    cache_path = context_dir / "build-status.yaml"
    _writer.write_yaml(cache_path, model_to_dict(status))
    return cache_path


def register_build_tools(server: FastMCP) -> None:
    """Register build verification tools on the MCP server."""

    @server.tool()
    def trw_build_check(
        scope: str = "full",
        run_path: str | None = None,
        timeout_secs: int | None = None,
    ) -> dict[str, object]:
        """Run build verification (pytest + mypy) and cache results.

        Executes pytest and/or mypy via subprocess, parses results,
        caches to .trw/context/build-status.yaml, and returns BuildStatus.
        Phase gates read the cached status — call this before trw_phase_check.

        Args:
            scope: Check scope — 'full' (pytest + mypy), 'pytest', 'mypy'.
            run_path: Optional run directory for event logging.
            timeout_secs: Override timeout (default: config value, max 600).
        """
        if not _config.build_check_enabled:
            return {
                "status": "skipped",
                "reason": "build_check_enabled is False",
            }

        trw_dir = resolve_trw_dir()
        project_root = resolve_project_root()
        effective_timeout = min(
            timeout_secs or _config.build_check_timeout_secs,
            600,
        )

        status = run_build_check(
            project_root,
            scope=scope,
            timeout_secs=effective_timeout,
            pytest_args=_config.build_check_pytest_args,
            mypy_args=_config.build_check_mypy_args,
        )

        cache_path = cache_build_status(trw_dir, status)

        # Log event if run_path provided
        if run_path:
            from trw_mcp.state.persistence import FileEventLogger

            events_path = Path(run_path).resolve() / "meta" / "events.jsonl"
            if events_path.parent.exists():
                event_logger = FileEventLogger(_writer)
                event_logger.log_event(events_path, "build_check_complete", {
                    "scope": scope,
                    "tests_passed": str(status.tests_passed),
                    "mypy_clean": str(status.mypy_clean),
                    "coverage_pct": str(status.coverage_pct),
                    "duration_secs": str(status.duration_secs),
                })

        logger.info(
            "build_check_complete",
            scope=scope,
            tests_passed=status.tests_passed,
            mypy_clean=status.mypy_clean,
            coverage_pct=status.coverage_pct,
            duration_secs=status.duration_secs,
        )

        return {
            "tests_passed": status.tests_passed,
            "mypy_clean": status.mypy_clean,
            "coverage_pct": status.coverage_pct,
            "test_count": status.test_count,
            "failure_count": status.failure_count,
            "failures": status.failures,
            "scope": status.scope,
            "duration_secs": status.duration_secs,
            "cache_path": str(cache_path),
        }
