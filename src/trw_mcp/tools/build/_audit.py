"""Dependency audit and API fuzz implementations.

Handles pip-audit, npm audit, unlisted import detection,
and schemathesis API fuzzing.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import (
    ApiFuzzResult,
    DepAuditResult,
    NpmAuditResult,
    PipAuditResult,
)
from trw_mcp.tools.build._subprocess import (
    _extract_failures,
    _find_executable,
    _run_audit_tool,
    _run_subprocess,
    _strip_ansi,
    _MAX_FAILURES,
)

_DEP_AUDIT_FILE = "dep-audit.yaml"
_API_FUZZ_FILE = "api-fuzz-status.yaml"


def _run_pip_audit(
    project_root: Path,
    config: TRWConfig,
) -> PipAuditResult:
    """Run pip-audit and parse vulnerability results.

    Executes ``pip-audit --json`` and filters vulnerabilities by the
    configured severity level. Only counts as blocking when fix versions
    are available and ``dep_audit_block_on_patchable_only`` is set.

    Args:
        project_root: Project root directory.
        config: TRW configuration with audit settings.

    Returns:
        PipAuditResult with pip_audit_passed, vulnerability count, and details.
        Includes pip_audit_skipped=True when pip-audit is not installed.
    """
    pip_audit_path = _find_executable("pip-audit", project_root)
    if pip_audit_path is None:
        return PipAuditResult(
            pip_audit_skipped=True,
            pip_audit_skip_reason="pip-audit not installed",
        )

    data = _run_audit_tool(
        [pip_audit_path, "--json"],
        project_root,
        config.dep_audit_timeout_secs,
        "pip-audit",
    )

    # _run_audit_tool returns a skip dict on failure
    if isinstance(data, dict) and data.get("pip_audit_skipped"):
        return PipAuditResult(
            pip_audit_skipped=True,
            pip_audit_skip_reason=str(data.get("pip_audit_skip_reason", "")),
        )

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
            # Extract severity -- pip-audit may report it differently
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

    return PipAuditResult(
        pip_audit_passed=blocking_count == 0,
        pip_audit_vulnerability_count=len(vulnerabilities),
        pip_audit_blocking_count=blocking_count,
        pip_audit_vulnerabilities=vulnerabilities[:_MAX_FAILURES],
    )


def _run_npm_audit(
    project_root: Path,
    config: TRWConfig,
    changed_files: list[str],
) -> NpmAuditResult:
    """Run npm audit when platform/package.json is in the changeset.

    Only executes when ``platform/package.json`` appears in changed_files.
    Runs ``npm audit --audit-level=high --json`` in the platform/ directory.

    Args:
        project_root: Project root directory.
        config: TRW configuration with audit settings.
        changed_files: List of changed file paths from git diff.

    Returns:
        NpmAuditResult with npm_audit results. Includes npm_audit_skipped=True
        when skipping (no platform changes, npm not found, etc.).
    """
    # Only run when platform/package.json is changed
    has_platform_changes = any(
        "platform/package.json" in f for f in changed_files
    )
    if not has_platform_changes:
        return NpmAuditResult(
            npm_audit_skipped=True,
            npm_audit_skip_reason="no platform/package.json changes",
        )

    platform_dir = project_root / "platform"
    if not platform_dir.exists():
        return NpmAuditResult(
            npm_audit_skipped=True,
            npm_audit_skip_reason="platform/ directory not found",
        )

    npm_path = shutil.which("npm")
    if npm_path is None:
        return NpmAuditResult(
            npm_audit_skipped=True,
            npm_audit_skip_reason="npm not installed",
        )

    data = _run_audit_tool(
        [npm_path, "audit", "--audit-level=high", "--json"],
        platform_dir,
        config.dep_audit_timeout_secs,
        "npm_audit",
    )

    # _run_audit_tool returns a skip dict on failure
    if isinstance(data, dict) and data.get("npm_audit_skipped"):
        return NpmAuditResult(
            npm_audit_skipped=True,
            npm_audit_skip_reason=str(data.get("npm_audit_skip_reason", "")),
        )

    # npm audit returns non-zero when vulnerabilities found -- that's expected
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

    return NpmAuditResult(
        npm_audit_passed=high_plus == 0,
        npm_audit_high_plus_count=high_plus,
        npm_audit_vulnerabilities=vuln_details[:_MAX_FAILURES],
    )


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
) -> DepAuditResult:
    """Orchestrate dependency audit: pip-audit + npm audit + unlisted imports.

    Combines results from all three checks into a unified result dict
    and caches to ``.trw/context/dep-audit.yaml``.

    Args:
        project_root: Project root directory.
        config: TRW configuration with audit settings.

    Returns:
        DepAuditResult with dep_audit_passed and merged sub-results.
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

    pip_result: PipAuditResult = _run_pip_audit(project_root, config)
    npm_result: NpmAuditResult = _run_npm_audit(project_root, config, changed_files)

    py_changed = [f for f in changed_files if f.endswith(".py")]
    unlisted = _detect_unlisted_imports(project_root, py_changed)

    # Overall pass: pip must pass (if run), npm must pass (if run)
    pip_passed = bool(pip_result.get("pip_audit_passed", True))
    npm_passed = bool(npm_result.get("npm_audit_passed", True))
    dep_audit_passed = pip_passed and npm_passed

    result: DepAuditResult = DepAuditResult(
        dep_audit_passed=dep_audit_passed,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Merge pip-audit sub-result keys
    if "pip_audit_passed" in pip_result:
        result["pip_audit_passed"] = pip_result["pip_audit_passed"]
    if "pip_audit_vulnerability_count" in pip_result:
        result["pip_audit_vulnerability_count"] = pip_result["pip_audit_vulnerability_count"]
    if "pip_audit_blocking_count" in pip_result:
        result["pip_audit_blocking_count"] = pip_result["pip_audit_blocking_count"]
    if "pip_audit_vulnerabilities" in pip_result:
        result["pip_audit_vulnerabilities"] = pip_result["pip_audit_vulnerabilities"]
    if "pip_audit_skipped" in pip_result:
        result["pip_audit_skipped"] = pip_result["pip_audit_skipped"]
    if "pip_audit_skip_reason" in pip_result:
        result["pip_audit_skip_reason"] = pip_result["pip_audit_skip_reason"]
    # Merge npm-audit sub-result keys
    if "npm_audit_passed" in npm_result:
        result["npm_audit_passed"] = npm_result["npm_audit_passed"]
    if "npm_audit_high_plus_count" in npm_result:
        result["npm_audit_high_plus_count"] = npm_result["npm_audit_high_plus_count"]
    if "npm_audit_vulnerabilities" in npm_result:
        result["npm_audit_vulnerabilities"] = npm_result["npm_audit_vulnerabilities"]
    if "npm_audit_skipped" in npm_result:
        result["npm_audit_skipped"] = npm_result["npm_audit_skipped"]
    if "npm_audit_skip_reason" in npm_result:
        result["npm_audit_skip_reason"] = npm_result["npm_audit_skip_reason"]

    if unlisted:
        result["unlisted_imports"] = unlisted
        result["unlisted_import_count"] = len(unlisted)

    return result


def _run_api_fuzz(
    project_root: Path,
    config: TRWConfig,
) -> ApiFuzzResult:
    """Run schemathesis API fuzzing against the backend.

    Executes ``schemathesis run --checks all`` against the configured
    base URL's OpenAPI spec. Gracefully skips when schemathesis is not
    installed, the backend is unreachable, or execution times out.

    Args:
        project_root: Project root directory.
        config: TRW configuration with API fuzz settings.

    Returns:
        ApiFuzzResult with api_fuzz_passed and details. Includes
        api_fuzz_skipped=True when skipping.
    """
    schemathesis_path = _find_executable("schemathesis", project_root)
    if schemathesis_path is None:
        schemathesis_path = _find_executable("st", project_root)
    if schemathesis_path is None:
        return ApiFuzzResult(
            api_fuzz_skipped=True,
            api_fuzz_skip_reason="schemathesis not installed",
        )

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
    except Exception:  # justified: boundary, backend reachability check may fail for many reasons
        return ApiFuzzResult(
            api_fuzz_skipped=True,
            api_fuzz_skip_reason=f"backend unreachable at {base_url}",
        )

    result = _run_subprocess(
        [schemathesis_path, "run", "--checks", "all",
         f"--base-url={base_url}", openapi_url],
        project_root,
        config.api_fuzz_timeout_secs,
    )

    if isinstance(result, str):
        return ApiFuzzResult(
            api_fuzz_skipped=True,
            api_fuzz_skip_reason=result,
        )

    output = _strip_ansi(result.stdout + "\n" + result.stderr)
    passed = result.returncode == 0

    # Extract failure/defect counts from output
    failures: list[str] = _extract_failures(
        output,
        ("FAILED", "ERROR", "Failure", "Defect"),
    )

    fuzz_result: ApiFuzzResult = ApiFuzzResult(
        api_fuzz_passed=passed,
        api_fuzz_base_url=base_url,
    )
    if failures:
        fuzz_result["api_fuzz_failures"] = failures
        fuzz_result["api_fuzz_failure_count"] = len(failures)

    return fuzz_result
