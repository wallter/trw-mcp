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
from trw_mcp.models.config import get_config
from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
from trw_mcp.state.persistence import FileStateWriter, model_to_dict
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger()

_config = get_config()
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


def _find_executable(name: str, project_root: Path) -> str | None:
    """Locate a tool on PATH or in the project venv.

    Resolution order:
    1. PATH lookup (shutil.which)
    2. {project_root}/.venv/bin/{name}
    3. {project_root}/venv/bin/{name}
    4. {source_package_path}/../.venv/bin/{name} (legacy)

    Args:
        name: Executable name (e.g. "pytest", "mypy").
        project_root: Project root directory.

    Returns:
        Resolved path string, or None if not found.
    """
    path = shutil.which(name)
    if path is not None:
        return path

    # Check common venv locations in project root
    for venv_name in (".venv", "venv"):
        candidate = project_root / venv_name / "bin" / name
        if candidate.exists():
            return str(candidate)

    # Legacy: check venv in build root (parent of source_package_path)
    source_path = _config.source_package_path or "trw-mcp/src"
    pkg_dir = project_root / Path(source_path).parent
    venv_path = pkg_dir / ".venv" / "bin" / name
    if venv_path.exists():
        return str(venv_path)
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
        return subprocess.run(
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


def _pytest_error(message: str) -> dict[str, object]:
    """Build a failed pytest result dict with the given error message."""
    return {
        "tests_passed": False,
        "coverage_pct": 0.0,
        "test_count": 0,
        "failure_count": 0,
        "failures": [message],
    }


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


def _collect_failures(result: dict[str, object]) -> list[str]:
    """Safely extract the failures list from a subprocess result dict.

    Handles the dict[str, object] return type by checking isinstance
    before extending, as required by mypy --strict.
    """
    raw = result.get("failures", [])
    if isinstance(raw, list):
        return [str(f) for f in raw]
    return []


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
        all_failures.extend(_collect_failures(pytest_result))

    if scope in ("full", "mypy"):
        mypy_result = _run_mypy(project_root, timeout_secs, mypy_args)
        mypy_clean = bool(mypy_result["mypy_clean"])
        all_failures.extend(_collect_failures(mypy_result))

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
    @log_tool_call
    def trw_build_check(
        scope: str = "full",
        run_path: str | None = None,
        timeout_secs: int | None = None,
        min_coverage: float | None = None,
    ) -> dict[str, object]:
        """Verify your code passes tests and type checking — the gate between implementation and delivery.

        Runs pytest and/or mypy via subprocess, parses results, and caches to
        .trw/context/build-status.yaml. Returns test count, coverage percentage,
        failure details, and mypy status. This is the VALIDATE phase gate — run
        it after implementation before moving to review and delivery.

        Args:
            scope: Check scope — 'full' (pytest + mypy), 'pytest', 'mypy'.
            run_path: Optional run directory for event logging.
            timeout_secs: Override timeout (default: config value, max 600).
            min_coverage: Optional minimum coverage percentage. If set and
                coverage falls below this threshold, tests_passed is set to
                False and a coverage_threshold_failed flag is added to the result.
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

        # Q-learning: reward recalled learnings based on build outcome
        try:
            from trw_mcp.scoring import process_outcome_for_event
            event_type = "build_passed" if status.tests_passed and status.mypy_clean else "build_failed"
            process_outcome_for_event(event_type)
        except Exception:
            pass  # Q-learning is best-effort, never block build check

        logger.info(
            "build_check_complete",
            scope=scope,
            tests_passed=status.tests_passed,
            mypy_clean=status.mypy_clean,
            coverage_pct=status.coverage_pct,
            duration_secs=status.duration_secs,
        )

        result: dict[str, object] = {
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

        # Coverage threshold enforcement (sprint-finish anti-regression)
        if min_coverage is not None and status.coverage_pct < min_coverage:
            result["tests_passed"] = False
            result["coverage_threshold_failed"] = True
            result["coverage_threshold"] = min_coverage
            result["coverage_threshold_message"] = (
                f"Coverage {status.coverage_pct:.1f}% is below "
                f"required threshold {min_coverage:.1f}%"
            )

        return result
