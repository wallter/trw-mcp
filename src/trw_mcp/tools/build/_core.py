"""Core build check orchestration and caching.

Contains the main ``run_build_check`` function that orchestrates pytest
and mypy runs, and ``cache_build_status`` / ``_cache_to_context`` for
persisting results to ``.trw/context/``.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.build import BuildStatus
from trw_mcp.state.persistence import FileStateWriter, model_to_dict
from trw_mcp.tools.build._runners import _run_mypy, _run_pytest
from trw_mcp.tools.build._subprocess import _collect_failures, _MAX_FAILURES


def _cache_to_context(
    trw_dir: Path,
    filename: str,
    data: dict[str, object],
) -> Path:
    """Write a result dict to .trw/context/<filename>.

    Args:
        trw_dir: Path to .trw directory.
        filename: YAML filename within context/.
        data: Dict to serialize.

    Returns:
        Path to the written file.
    """
    writer = FileStateWriter()
    context_dir = trw_dir / "context"
    writer.ensure_dir(context_dir)
    cache_path = context_dir / filename
    writer.write_yaml(cache_path, data)
    return cache_path


def run_build_check(
    project_root: Path,
    scope: str = "full",
    timeout_secs: int = 300,
    pytest_args: str = "",
    mypy_args: str = "--strict",
) -> BuildStatus:
    """Execute build verification and return BuildStatus.

    Supports scopes: 'full' (pytest + mypy), 'pytest', 'mypy', 'quick',
    'mutations', 'deps', 'api'. The mutations/deps/api scopes skip
    pytest and mypy and only run their respective checks.

    Args:
        project_root: Project root directory.
        scope: Check scope -- 'full', 'pytest', 'mypy', 'quick',
            'mutations', 'deps', 'api'.
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
    writer = FileStateWriter()
    context_dir = trw_dir / "context"
    writer.ensure_dir(context_dir)
    cache_path = context_dir / "build-status.yaml"
    writer.write_yaml(cache_path, model_to_dict(status))
    return cache_path
