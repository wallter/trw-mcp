"""TRW mutation testing gate — PRD-QUAL-025.

Runs mutmut on changed files, calculates mutation score, evaluates
tiered thresholds, and caches results to mutation-status.yaml.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools.build import _find_executable, _run_subprocess, _strip_ansi

logger = structlog.get_logger()


def _get_changed_files(
    project_root: Path,
    source_package_path: str,
) -> list[str]:
    """Get Python files changed relative to HEAD.

    Runs ``git diff --name-only HEAD`` and filters results to ``.py``
    files within the configured source package path.

    Args:
        project_root: Project root directory (git repo root).
        source_package_path: Relative path to source package (e.g. "trw-mcp/src").

    Returns:
        List of relative file paths that are changed Python files
        within the source package.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(project_root),
        )
    except (subprocess.TimeoutExpired, OSError):
        return []

    if result.returncode != 0:
        return []

    changed: list[str] = []
    for line in result.stdout.strip().splitlines():
        stripped = line.strip()
        if stripped.endswith(".py") and stripped.startswith(source_package_path):
            changed.append(stripped)
    logger.debug("mutations_changed_files", count=len(changed))
    return changed


def _classify_threshold_tier(
    file_path: str,
    config: TRWConfig,
) -> tuple[str, float]:
    """Classify a file into a mutation threshold tier.

    Checks the file path against configured critical and experimental
    path prefixes, returning the tier name and its threshold.

    Args:
        file_path: Relative file path to classify.
        config: TRW configuration with tier paths and thresholds.

    Returns:
        Tuple of (tier_name, threshold) where tier_name is one of
        'critical', 'experimental', or 'standard'.
    """
    for critical_prefix in config.mutation_critical_paths:
        if critical_prefix in file_path:
            return ("critical", config.mutation_threshold_critical)

    for experimental_prefix in config.mutation_experimental_paths:
        if experimental_prefix in file_path:
            return ("experimental", config.mutation_threshold_experimental)

    return ("standard", config.mutation_threshold)


def _parse_mutmut_results(json_output: str) -> dict[str, object]:
    """Parse mutmut JSON output into a structured result dict.

    Extracts killed, survived, timeout, and suspicious counts from
    mutmut's JSON results format. Calculates mutation score and
    extracts up to 20 surviving mutant details sorted by line number.

    Args:
        json_output: Raw JSON string from ``mutmut results --json``.

    Returns:
        Dict with keys: killed, survived, timeout, suspicious,
        total_mutants, mutation_score (float or None), and
        surviving_mutants (list of detail dicts, max 20).
    """
    try:
        data = json.loads(json_output)
    except (json.JSONDecodeError, TypeError):
        return {
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "suspicious": 0,
            "total_mutants": 0,
            "mutation_score": None,
            "surviving_mutants": [],
            "parse_error": "invalid JSON from mutmut",
        }

    # mutmut JSON structure varies by version; handle common shapes
    if isinstance(data, dict):
        killed = int(data.get("killed", 0))
        survived = int(data.get("survived", 0))
        timeout = int(data.get("timeout", 0))
        suspicious = int(data.get("suspicious", 0))
        survivors_raw = data.get("survived_mutants", [])
    else:
        killed = 0
        survived = 0
        timeout = 0
        suspicious = 0
        survivors_raw = []

    total = killed + survived
    mutation_score: float | None = None
    if total > 0:
        mutation_score = round(killed / total, 4)

    # Extract surviving mutant details (first 20, sorted by line)
    surviving_mutants: list[dict[str, object]] = []
    if isinstance(survivors_raw, list):
        for mutant in survivors_raw[:20]:
            if isinstance(mutant, dict):
                surviving_mutants.append({
                    "file": str(mutant.get("file", "")),
                    "line": int(mutant.get("line", 0)),
                    "description": str(mutant.get("description", ""))[:200],
                })
        surviving_mutants.sort(key=lambda m: int(str(m.get("line", 0))))

    logger.debug(
        "mutations_results_parsed",
        killed=killed,
        survived=survived,
        mutation_score=mutation_score,
    )
    return {
        "killed": killed,
        "survived": survived,
        "timeout": timeout,
        "suspicious": suspicious,
        "total_mutants": killed + survived + timeout + suspicious,
        "mutation_score": mutation_score,
        "surviving_mutants": surviving_mutants,
    }


def run_mutation_check(
    project_root: Path,
    config: TRWConfig,
) -> dict[str, object]:
    """Run mutation testing on changed files and evaluate thresholds.

    Orchestrates the full mutation testing pipeline: detect changed files,
    run mutmut, parse results, classify tiers, and evaluate pass/fail.

    Args:
        project_root: Project root directory.
        config: TRW configuration with mutation settings.

    Returns:
        Result dict with mutation_passed, mutation_score, tier info,
        and detail fields. Includes mutation_skipped=True when
        skipping (no changed files or mutmut not installed).
    """
    source_path = config.source_package_path or "trw-mcp/src"
    changed_files = _get_changed_files(project_root, source_path)

    if not changed_files:
        return {
            "mutation_skipped": True,
            "mutation_skip_reason": "no_changed_files",
        }

    mutmut_path = _find_executable("mutmut", project_root)
    if mutmut_path is None:
        return {
            "mutation_skipped": True,
            "mutation_skip_reason": "mutmut_not_installed",
        }

    # Run mutmut on changed files
    paths_arg = ",".join(changed_files)
    timeout = config.mutation_timeout_secs

    logger.debug("mutations_subprocess_run", cmd="mutmut run", paths=paths_arg)
    run_result = _run_subprocess(
        [mutmut_path, "run", f"--paths-to-mutate={paths_arg}"],
        project_root,
        timeout,
    )

    if isinstance(run_result, str):
        return {
            "mutation_skipped": True,
            "mutation_skip_reason": run_result,
        }

    # Get JSON results
    logger.debug("mutations_subprocess_run", cmd="mutmut results --json")
    json_result = _run_subprocess(
        [mutmut_path, "results", "--json"],
        project_root,
        30,
    )

    if isinstance(json_result, str):
        return {
            "mutation_skipped": True,
            "mutation_skip_reason": f"results_parse_failed: {json_result}",
        }

    parsed = _parse_mutmut_results(
        _strip_ansi(json_result.stdout),
    )

    # Classify tier based on highest-tier file in the changeset
    highest_tier = "standard"
    highest_threshold = config.mutation_threshold
    for fpath in changed_files:
        tier, threshold = _classify_threshold_tier(fpath, config)
        if tier == "critical":
            highest_tier = "critical"
            highest_threshold = threshold
            break
        if tier == "experimental" and highest_tier == "standard":
            highest_tier = "experimental"
            highest_threshold = threshold

    # Evaluate pass/fail
    score = parsed.get("mutation_score")
    mutation_passed = True
    if score is not None:
        mutation_passed = float(str(score)) >= highest_threshold

    return {
        "mutation_passed": mutation_passed,
        "mutation_score": score,
        "mutation_tier": highest_tier,
        "mutation_threshold": highest_threshold,
        "changed_files": changed_files,
        "changed_file_count": len(changed_files),
        **{k: v for k, v in parsed.items() if k != "mutation_score"},
    }


def cache_mutation_status(
    trw_dir: Path,
    result: dict[str, object],
) -> Path:
    """Write mutation testing results to .trw/context/mutation-status.yaml."""
    from trw_mcp.tools.build import _cache_to_context

    return _cache_to_context(trw_dir, "mutation-status.yaml", result)
