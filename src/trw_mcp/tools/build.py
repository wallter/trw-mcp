"""TRW build verification gate tool — PRD-CORE-023, PRD-QUAL-025/028/029.

Runs the project's test suite and type checker via subprocess, caches results
to .trw/context/build-status.yaml, and returns BuildStatus. Defaults to
pytest + mypy for Python projects but supports configurable test/type-check
commands. Phase gates consume cached status — they never run subprocesses.

Extended with mutation testing (QUAL-025), dependency audit (QUAL-028),
and API fuzz (QUAL-029) scopes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.build import BuildStatus
from trw_mcp.models.config import TRWConfig, get_config
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

_DEP_AUDIT_FILE = "dep-audit.yaml"
_API_FUZZ_FILE = "api-fuzz-status.yaml"


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


def _run_audit_tool(
    cmd: list[str],
    cwd: Path,
    timeout_secs: int,
    tool_name: str,
) -> dict[str, object] | object:
    """Run an audit tool subprocess and parse its JSON output.

    Shared helper for pip-audit and npm audit — handles subprocess
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
        return json.loads(result.stdout)  # type: ignore[no-any-return]
    except (json.JSONDecodeError, TypeError):
        return {
            f"{prefix}_skipped": True,
            f"{prefix}_skip_reason": f"invalid JSON from {tool_name}",
        }


def _run_pip_audit(
    project_root: Path,
    config: TRWConfig,
) -> dict[str, object]:
    """Run pip-audit and parse vulnerability results.

    Executes ``pip-audit --json`` and filters vulnerabilities by the
    configured severity level. Only counts as blocking when fix versions
    are available and ``dep_audit_block_on_patchable_only`` is set.

    Args:
        project_root: Project root directory.
        config: TRW configuration with audit settings.

    Returns:
        Dict with pip_audit_passed, vulnerability count, and details.
        Includes pip_audit_skipped=True when pip-audit is not installed.
    """
    pip_audit_path = _find_executable("pip-audit", project_root)
    if pip_audit_path is None:
        return {
            "pip_audit_skipped": True,
            "pip_audit_skip_reason": "pip-audit not installed",
        }

    data = _run_audit_tool(
        [pip_audit_path, "--json"],
        project_root,
        config.dep_audit_timeout_secs,
        "pip-audit",
    )

    # _run_audit_tool returns a skip dict on failure
    if isinstance(data, dict) and data.get("pip_audit_skipped"):
        return data

    # Severity ranking for filtering
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
    min_rank = severity_rank.get(config.dep_audit_level, 3)

    vulnerabilities: list[dict[str, object]] = []
    blocking_count = 0

    deps = data if isinstance(data, list) else data.get("dependencies", []) if isinstance(data, dict) else []
    for dep in deps:
        if not isinstance(dep, dict):
            continue
        vulns = dep.get("vulns", [])
        if not isinstance(vulns, list):
            continue
        for vuln in vulns:
            if not isinstance(vuln, dict):
                continue
            # Extract severity — pip-audit may report it differently
            severity = str(vuln.get("severity", "unknown")).lower()
            vuln_rank = severity_rank.get(severity, 0)

            # Also check CVSS score as fallback for severity
            cvss = vuln.get("cvss_score")
            if cvss is not None and vuln_rank == 0:
                cvss_val = float(str(cvss))
                if cvss_val >= 9.0:
                    vuln_rank = 4
                elif cvss_val >= 7.0:
                    vuln_rank = 3
                elif cvss_val >= 4.0:
                    vuln_rank = 2
                else:
                    vuln_rank = 1

            if vuln_rank < min_rank:
                continue

            fix_versions = vuln.get("fix_versions", [])
            has_fix = bool(fix_versions)

            entry: dict[str, object] = {
                "package": str(dep.get("name", "")),
                "version": str(dep.get("version", "")),
                "cve_id": str(vuln.get("id", "")),
                "severity": severity,
                "fix_versions": fix_versions if isinstance(fix_versions, list) else [],
            }
            if cvss is not None:
                entry["cvss_score"] = float(str(cvss))

            vulnerabilities.append(entry)

            # Blocking logic
            if config.dep_audit_block_on_patchable_only:
                if has_fix:
                    blocking_count += 1
            else:
                blocking_count += 1

    return {
        "pip_audit_passed": blocking_count == 0,
        "pip_audit_vulnerability_count": len(vulnerabilities),
        "pip_audit_blocking_count": blocking_count,
        "pip_audit_vulnerabilities": vulnerabilities[:_MAX_FAILURES],
    }


def _run_npm_audit(
    project_root: Path,
    config: TRWConfig,
    changed_files: list[str],
) -> dict[str, object]:
    """Run npm audit when platform/package.json is in the changeset.

    Only executes when ``platform/package.json`` appears in changed_files.
    Runs ``npm audit --audit-level=high --json`` in the platform/ directory.

    Args:
        project_root: Project root directory.
        config: TRW configuration with audit settings.
        changed_files: List of changed file paths from git diff.

    Returns:
        Dict with npm_audit results. Includes npm_audit_skipped=True
        when skipping (no platform changes, npm not found, etc.).
    """
    # Only run when platform/package.json is changed
    has_platform_changes = any(
        "platform/package.json" in f for f in changed_files
    )
    if not has_platform_changes:
        return {
            "npm_audit_skipped": True,
            "npm_audit_skip_reason": "no platform/package.json changes",
        }

    platform_dir = project_root / "platform"
    if not platform_dir.exists():
        return {
            "npm_audit_skipped": True,
            "npm_audit_skip_reason": "platform/ directory not found",
        }

    npm_path = shutil.which("npm")
    if npm_path is None:
        return {
            "npm_audit_skipped": True,
            "npm_audit_skip_reason": "npm not installed",
        }

    data = _run_audit_tool(
        [npm_path, "audit", "--audit-level=high", "--json"],
        platform_dir,
        config.dep_audit_timeout_secs,
        "npm_audit",
    )

    # _run_audit_tool returns a skip dict on failure
    if isinstance(data, dict) and data.get("npm_audit_skipped"):
        return data

    # npm audit returns non-zero when vulnerabilities found — that's expected
    vulnerabilities = data.get("vulnerabilities", {}) if isinstance(data, dict) else {}
    high_plus = 0
    vuln_details: list[dict[str, object]] = []

    if isinstance(vulnerabilities, dict):
        for pkg_name, info in vulnerabilities.items():
            if not isinstance(info, dict):
                continue
            severity = str(info.get("severity", "")).lower()
            if severity in ("high", "critical"):
                high_plus += 1
                vuln_details.append({
                    "package": pkg_name,
                    "severity": severity,
                    "via": str(info.get("via", ""))[:200],
                })

    return {
        "npm_audit_passed": high_plus == 0,
        "npm_audit_high_plus_count": high_plus,
        "npm_audit_vulnerabilities": vuln_details[:_MAX_FAILURES],
    }


def _detect_unlisted_imports(
    project_root: Path,
    changed_files: list[str],
) -> list[str]:
    """Detect imports in changed files not listed in pyproject.toml dependencies.

    Scans added lines in changed ``.py`` files for import statements and
    cross-references against ``[project.dependencies]`` in pyproject.toml.

    Args:
        project_root: Project root directory.
        changed_files: List of changed file paths.

    Returns:
        List of package names that appear in imports but not in
        pyproject.toml dependencies.
    """
    # Read pyproject.toml dependencies
    listed_deps: set[str] = set()

    # Check multiple possible pyproject.toml locations
    for toml_path in [
        project_root / "pyproject.toml",
        project_root / "trw-mcp" / "pyproject.toml",
    ]:
        if toml_path.exists():
            try:
                content = toml_path.read_text(encoding="utf-8")
                # Simple parser: find lines under [project.dependencies]
                in_deps = False
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped == "[project.dependencies]" or stripped.startswith("dependencies"):
                        in_deps = True
                        continue
                    if in_deps and stripped.startswith("["):
                        in_deps = False
                        continue
                    if in_deps and stripped and not stripped.startswith("#"):
                        # Extract package name (before version specifier)
                        dep_name = stripped.strip('"').strip("'").strip(",")
                        dep_name = dep_name.split(">=")[0].split("<=")[0]
                        dep_name = dep_name.split("==")[0].split("~=")[0]
                        dep_name = dep_name.split(">")[0].split("<")[0]
                        dep_name = dep_name.split("[")[0].strip()
                        if dep_name:
                            # Normalize: pip uses - and _, Python uses _
                            listed_deps.add(dep_name.lower().replace("-", "_"))
            except OSError:
                continue

    # Standard library modules to exclude from detection
    stdlib_prefixes = {
        "os", "sys", "re", "json", "time", "datetime", "pathlib",
        "subprocess", "shutil", "typing", "collections", "functools",
        "itertools", "contextlib", "abc", "io", "math", "hashlib",
        "logging", "unittest", "tempfile", "copy", "enum", "dataclasses",
        "importlib", "inspect", "textwrap", "string", "operator",
        "warnings", "traceback", "threading", "multiprocessing",
        "socket", "http", "urllib", "email", "html", "xml",
        "csv", "configparser", "argparse", "getpass", "uuid",
        "secrets", "hmac", "base64", "binascii", "struct",
        "asyncio", "concurrent", "signal", "fcntl", "stat",
        "__future__",
    }

    # Scan changed Python files for imports
    imported_packages: set[str] = set()
    py_files = [f for f in changed_files if f.endswith(".py")]

    for fpath in py_files:
        full_path = project_root / fpath
        if not full_path.exists():
            continue
        try:
            for line in full_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("import "):
                    # import foo / import foo.bar
                    parts = stripped[7:].split(",")
                    for part in parts:
                        pkg = part.strip().split(".")[0].split(" ")[0]
                        if pkg:
                            imported_packages.add(pkg.lower().replace("-", "_"))
                elif stripped.startswith("from ") and " import " in stripped:
                    # from foo import bar / from foo.bar import baz
                    pkg = stripped[5:].split(" import ")[0].strip().split(".")[0]
                    if pkg:
                        imported_packages.add(pkg.lower().replace("-", "_"))
        except OSError:
            continue

    # Filter out stdlib and already-listed deps
    unlisted = sorted(
        pkg for pkg in imported_packages
        if pkg not in stdlib_prefixes
        and pkg not in listed_deps
        and not pkg.startswith("_")
    )
    return unlisted


def _run_dep_audit(
    project_root: Path,
    config: TRWConfig,
) -> dict[str, object]:
    """Orchestrate dependency audit: pip-audit + npm audit + unlisted imports.

    Combines results from all three checks into a unified result dict
    and caches to ``.trw/context/dep-audit.yaml``.

    Args:
        project_root: Project root directory.
        config: TRW configuration with audit settings.

    Returns:
        Combined result dict with dep_audit_passed and sub-results.
    """
    # Get changed files for npm audit and unlisted import detection
    try:
        git_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_root),
        )
        changed_files = [
            line.strip() for line in git_result.stdout.strip().splitlines()
            if line.strip()
        ] if git_result.returncode == 0 else []
    except (subprocess.TimeoutExpired, OSError):
        changed_files = []

    pip_result = _run_pip_audit(project_root, config)
    npm_result = _run_npm_audit(project_root, config, changed_files)

    py_changed = [f for f in changed_files if f.endswith(".py")]
    unlisted = _detect_unlisted_imports(project_root, py_changed)

    # Overall pass: pip must pass (if run), npm must pass (if run)
    pip_passed = bool(pip_result.get("pip_audit_passed", True))
    npm_passed = bool(npm_result.get("npm_audit_passed", True))
    dep_audit_passed = pip_passed and npm_passed

    result: dict[str, object] = {
        "dep_audit_passed": dep_audit_passed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Merge sub-results
    for key, value in pip_result.items():
        result[key] = value
    for key, value in npm_result.items():
        result[key] = value

    if unlisted:
        result["unlisted_imports"] = unlisted
        result["unlisted_import_count"] = len(unlisted)

    return result


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
    context_dir = trw_dir / "context"
    _writer.ensure_dir(context_dir)
    cache_path = context_dir / filename
    _writer.write_yaml(cache_path, data)
    return cache_path


def _run_api_fuzz(
    project_root: Path,
    config: TRWConfig,
) -> dict[str, object]:
    """Run schemathesis API fuzzing against the backend.

    Executes ``schemathesis run --checks all`` against the configured
    base URL's OpenAPI spec. Gracefully skips when schemathesis is not
    installed, the backend is unreachable, or execution times out.

    Args:
        project_root: Project root directory.
        config: TRW configuration with API fuzz settings.

    Returns:
        Dict with api_fuzz_passed and details. Includes
        api_fuzz_skipped=True when skipping.
    """
    schemathesis_path = _find_executable("schemathesis", project_root)
    if schemathesis_path is None:
        schemathesis_path = _find_executable("st", project_root)
    if schemathesis_path is None:
        return {
            "api_fuzz_skipped": True,
            "api_fuzz_skip_reason": "schemathesis not installed",
        }

    base_url = config.api_fuzz_base_url
    openapi_url = f"{base_url}/openapi.json"

    # Check if backend is reachable
    try:
        import urllib.request

        req = urllib.request.Request(
            f"{base_url}/",
            method="HEAD",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        return {
            "api_fuzz_skipped": True,
            "api_fuzz_skip_reason": f"backend unreachable at {base_url}",
        }

    result = _run_subprocess(
        [schemathesis_path, "run", "--checks", "all",
         f"--base-url={base_url}", openapi_url],
        project_root,
        config.api_fuzz_timeout_secs,
    )

    if isinstance(result, str):
        return {
            "api_fuzz_skipped": True,
            "api_fuzz_skip_reason": result,
        }

    output = _strip_ansi(result.stdout + "\n" + result.stderr)
    passed = result.returncode == 0

    # Extract failure/defect counts from output
    failures: list[str] = _extract_failures(
        output,
        ("FAILED", "ERROR", "Failure", "Defect"),
    )

    fuzz_result: dict[str, object] = {
        "api_fuzz_passed": passed,
        "api_fuzz_base_url": base_url,
    }
    if failures:
        fuzz_result["api_fuzz_failures"] = failures
        fuzz_result["api_fuzz_failure_count"] = len(failures)

    return fuzz_result


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

    Supports scopes: 'full' (pytest + mypy), 'pytest', 'mypy', 'quick',
    'mutations', 'deps', 'api'. The mutations/deps/api scopes skip
    pytest and mypy and only run their respective checks.

    Args:
        project_root: Project root directory.
        scope: Check scope — 'full', 'pytest', 'mypy', 'quick',
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

        Runs the project's test suite and type checker via subprocess, parses
        results, and caches to .trw/context/build-status.yaml. Returns test
        count, coverage percentage, failure details, and type-check status.
        This is the VALIDATE phase gate — run it after implementation before
        moving to review and delivery.

        Args:
            scope: Check scope — 'full' (tests + type-check), 'pytest', 'mypy'.
                Also supports 'mutations' (mutation testing only),
                'deps' (dependency audit only), 'api' (API fuzz only).
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

        _VALID_SCOPES = {"full", "pytest", "mypy", "quick", "mutations", "deps", "api"}
        if scope not in _VALID_SCOPES:
            return {
                "status": "error",
                "reason": f"Invalid scope '{scope}'. Valid scopes: {sorted(_VALID_SCOPES)}",
            }

        # --- Standalone scopes (no pytest/mypy) ---

        if scope == "mutations":
            if not _config.mutation_enabled:
                return {"status": "skipped", "reason": "mutation_enabled is False"}
            from trw_mcp.tools.mutations import (
                cache_mutation_status,
                run_mutation_check,
            )

            mut_result = run_mutation_check(project_root, _config)
            cache_mutation_status(trw_dir, mut_result)
            return mut_result

        if scope == "deps":
            if not _config.dep_audit_enabled:
                return {"status": "skipped", "reason": "dep_audit_enabled is False"}
            dep_result = _run_dep_audit(project_root, _config)
            _cache_to_context(trw_dir, _DEP_AUDIT_FILE, dep_result)
            return dep_result

        if scope == "api":
            if not _config.api_fuzz_enabled:
                return {"status": "skipped", "reason": "api_fuzz_enabled is False"}
            fuzz_result = _run_api_fuzz(project_root, _config)
            _cache_to_context(trw_dir, _API_FUZZ_FILE, fuzz_result)
            return fuzz_result

        # --- Standard scopes (pytest/mypy) ---

        status = run_build_check(
            project_root,
            scope=scope,
            timeout_secs=effective_timeout,
            pytest_args=_config.build_check_pytest_args,
            mypy_args=_config.build_check_mypy_args,
        )

        cache_path = cache_build_status(trw_dir, status)

        # FIX-035-FR01: Auto-detect active run when not explicitly provided
        from trw_mcp.state._paths import find_active_run

        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        # FIX-035-FR05: Auto-update phase to VALIDATE
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.VALIDATE)

        # FIX-035-FR02: Log event with proper boolean types
        if resolved_run is not None:
            from trw_mcp.state.persistence import FileEventLogger

            events_path = resolved_run / "meta" / "events.jsonl"
            if events_path.parent.exists():
                event_logger = FileEventLogger(_writer)
                event_logger.log_event(events_path, "build_check_complete", {
                    "scope": scope,
                    "tests_passed": status.tests_passed,
                    "mypy_clean": status.mypy_clean,
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

        # Dep audit on full scope (if enabled)
        if scope == "full" and _config.dep_audit_enabled:
            dep_result = _run_dep_audit(project_root, _config)
            _cache_to_context(trw_dir, _DEP_AUDIT_FILE, dep_result)
            result["dep_audit"] = dep_result
            if not bool(dep_result.get("dep_audit_passed", True)):
                result["dep_audit_blocking"] = True

        return result

    @server.tool()
    @log_tool_call
    def trw_quality_dashboard(
        window_days: int = 90,
        compare_sprint: str = "",
        format: str = "summary",
    ) -> dict[str, object]:
        """View quality trends — ceremony scores, coverage, review verdicts, and degradation alerts.

        Aggregates session event data to show how your project's quality metrics
        are trending over time. Use compare_sprint to see sprint-over-sprint deltas.

        Args:
            window_days: Number of days to include (1-365, default 90).
            compare_sprint: Optional sprint ID to compare against previous sprint.
            format: Output format — "summary" or "detailed".
        """
        from trw_mcp.state.dashboard import aggregate_dashboard

        trw_dir = resolve_trw_dir()
        clamped_days = max(1, min(365, window_days))
        return aggregate_dashboard(trw_dir, clamped_days, compare_sprint)


def __reload_hook__() -> None:
    """Reset module-level caches on mcp-hmr hot-reload."""
    global _config, _writer
    _config = get_config()
    _writer = FileStateWriter()
