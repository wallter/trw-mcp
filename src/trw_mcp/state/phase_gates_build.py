"""Build status, integration, and orphan-module checks for phase gates.

Provides _check_build_status(), _best_effort_build_check(),
_best_effort_integration_check(), _best_effort_orphan_check(),
_best_effort_migration_check() (PRD-INFRA-035),
_best_effort_dry_check() (PRD-QUAL-039), and
_best_effort_semantic_check() (PRD-QUAL-040)
which validate cached build results, tool registration, module
reachability, migration safety, code duplication, and semantic
patterns before allowing phase transitions.
Extracted from phase_gates.py for module focus.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import ValidationFailure

logger = structlog.get_logger()

# Build status staleness threshold (PRD-CORE-023-FR10)
_BUILD_STALENESS_SECS = 1800  # 30 minutes


def _check_build_status(
    trw_dir: Path,
    config: TRWConfig,
    phase_name: str,
) -> list[ValidationFailure]:
    """Check cached build-status.yaml for phase gate decisions.

    Phase gates never run subprocesses -- they read the cached result
    written by ``trw_build_check``.  Severity depends on phase and
    ``build_gate_enforcement`` config.

    PRD-CORE-023-FR06/FR07/FR08: IMPLEMENT=warning, VALIDATE/DELIVER=per config.
    PRD-CORE-023-FR10: Stale results (>30 min) downgraded to warning.
    PRD-CORE-023-FR11: Missing cache = advisory, never blocks.

    Args:
        trw_dir: Path to the .trw directory.
        config: Framework configuration.
        phase_name: Current phase name (implement, validate, deliver).

    Returns:
        List of ValidationFailure entries (may be empty).
    """
    if not config.build_check_enabled or config.build_gate_enforcement == "off":
        return []

    cache_path = trw_dir / "context" / "build-status.yaml"
    if not cache_path.exists():
        return [
            ValidationFailure(
                field="build_status",
                rule="build_cache_exists",
                message=(
                    "No build status cached — run trw_build_check() "
                    "before phase gate"
                ),
                severity="info",
            )
        ]

    from trw_mcp.state.persistence import FileStateReader

    from trw_mcp.exceptions import StateError
    try:
        data = FileStateReader().read_yaml(cache_path)
    except (OSError, StateError):
        return [
            ValidationFailure(
                field="build_status",
                rule="build_cache_readable",
                message="Could not read build-status.yaml",
                severity="warning",
            )
        ]

    failures: list[ValidationFailure] = []

    # FR10: Staleness detection
    is_stale = False
    ts_str = data.get("timestamp", "")
    if ts_str:
        try:
            cached_dt = datetime.fromisoformat(str(ts_str))
            age_secs = time.time() - cached_dt.replace(
                tzinfo=timezone.utc,
            ).timestamp()
            if age_secs > _BUILD_STALENESS_SECS:
                is_stale = True
                failures.append(
                    ValidationFailure(
                        field="build_status",
                        rule="build_staleness",
                        message=(
                            f"Build status is {int(age_secs / 60)}m old "
                            f"(threshold: {_BUILD_STALENESS_SECS // 60}m) — "
                            "re-run trw_build_check()"
                        ),
                        severity="warning",
                    )
                )
        except (ValueError, TypeError, OSError):
            pass  # Can't parse timestamp — treat as fresh

    # Determine severity: IMPLEMENT always warning; VALIDATE/DELIVER per config
    is_strict_gate = (
        phase_name != "implement"
        and not is_stale
        and config.build_gate_enforcement == "strict"
    )
    severity = "error" if is_strict_gate else "warning"

    # Check test results
    if not data.get("tests_passed", False):
        failure_list = data.get("failures", [])
        snippet = ""
        if isinstance(failure_list, list) and failure_list:
            snippet = f" — {failure_list[0]}"
            if len(failure_list) > 1:
                snippet += f" (+{len(failure_list) - 1} more)"
        failures.append(
            ValidationFailure(
                field="build_tests",
                rule="tests_passed",
                message=f"Tests did not pass{snippet}",
                severity=severity,
            )
        )

    # Check mypy results (only if scope includes mypy)
    scope = str(data.get("scope", "full"))
    if scope in ("full", "mypy") and not data.get("mypy_clean", False):
        failures.append(
            ValidationFailure(
                field="build_type_check",
                rule="type_check_clean",
                message="Type checker reported errors — run trw_build_check() for details",
                severity=severity,
            )
        )

    # Check coverage at VALIDATE/DELIVER
    if phase_name in ("validate", "deliver") and scope in ("full", "pytest", "quick"):
        coverage = float(str(data.get("coverage_pct", 0.0)))
        if coverage < config.build_check_coverage_min:
            failures.append(
                ValidationFailure(
                    field="build_coverage",
                    rule="coverage_min",
                    message=(
                        f"Coverage {coverage:.1f}% is below minimum "
                        f"{config.build_check_coverage_min:.1f}%"
                    ),
                    severity=severity,
                )
            )

    return failures


def _best_effort_build_check(
    config: TRWConfig,
    phase_name: str,
    failures: list[ValidationFailure],
) -> None:
    """Append build-status failures (best-effort, never raises).

    Args:
        config: Framework configuration.
        phase_name: Current phase name.
        failures: Mutable list to append failures into.
    """
    try:
        from trw_mcp.state._paths import resolve_trw_dir
        failures.extend(_check_build_status(resolve_trw_dir(), config, phase_name))
    except Exception:  # broad catch: best-effort gate, never blocks
        pass


def _best_effort_integration_check(
    failures: list[ValidationFailure],
    *,
    severity: str = "warning",
) -> None:
    """Append integration-check failures (best-effort, never raises).

    Args:
        failures: Mutable list to append failures into.
        severity: Severity for unregistered-tool findings.
    """
    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state.integration_check import check_integration

        src_dir = resolve_project_root() / "trw-mcp" / "src" / "trw_mcp"
        if not src_dir.is_dir():
            return
        integ = check_integration(src_dir)
        unreg = integ.get("unregistered", [])
        if isinstance(unreg, list):
            for mod in unreg:
                failures.append(ValidationFailure(
                    field=f"integration:tools/{mod}.py",
                    rule="tool_registration",
                    message=f"Tool module 'tools/{mod}.py' has register function but is not wired in server.py",
                    severity=severity,
                ))
        missing = integ.get("missing_tests", [])
        if isinstance(missing, list):
            for test_name in missing:
                failures.append(ValidationFailure(
                    field=f"integration:{test_name}",
                    rule="test_coverage",
                    message=f"Missing test file: {test_name}",
                    severity="warning",
                ))
    except Exception:  # broad catch: best-effort gate, never blocks
        pass


def _best_effort_orphan_check(
    failures: list[ValidationFailure],
    *,
    severity: str = "warning",
) -> None:
    """Append orphan-module findings (best-effort, never raises).

    Detects source modules not imported by any other production module.
    Catches the "extraction without wiring" anti-pattern where a module
    is created but never connected to the production call graph.

    Args:
        failures: Mutable list to append failures into.
        severity: Severity for orphan findings.
    """
    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state.integration_check import check_orphan_modules

        src_dir = resolve_project_root() / "trw-mcp" / "src" / "trw_mcp"
        if not src_dir.is_dir():
            return
        result = check_orphan_modules(src_dir)
        orphans = result.get("orphans", [])
        if isinstance(orphans, list):
            for mod in orphans:
                failures.append(ValidationFailure(
                    field=f"orphan:{mod}",
                    rule="module_reachability",
                    message=(
                        f"Module '{mod}' is not imported by any other "
                        f"production source file — possible extraction "
                        f"without wiring"
                    ),
                    severity=severity,
                ))
    except Exception:  # broad catch: best-effort gate, never blocks
        pass


# ---------------------------------------------------------------------------
# Migration verification gate (PRD-INFRA-035)
# ---------------------------------------------------------------------------


def _get_changed_files(project_root: Path) -> list[str]:
    """Get list of changed files from git diff (staged + unstaged + untracked).

    Returns empty list if git is unavailable.

    Args:
        project_root: Project root directory.

    Returns:
        De-duplicated list of changed file paths (relative to project root).
    """
    import subprocess

    try:
        # Staged + unstaged changes
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(project_root),
        )
        files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]

        # Also check staged files (for new files)
        result2 = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(project_root),
        )
        staged = [f.strip() for f in result2.stdout.strip().split("\n") if f.strip()]

        # Also check untracked files
        result3 = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(project_root),
        )
        untracked = [
            f.strip() for f in result3.stdout.strip().split("\n") if f.strip()
        ]

        all_files = list(set(files + staged + untracked))
        return all_files
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []


def _check_nullable_defaults(
    project_root: Path,
    model_files: list[str],
) -> list[str]:
    """Parse model files for NOT NULL columns without server_default.

    Inspects added lines (``+`` prefix) from ``git diff HEAD`` output for
    patterns like ``Column(..., nullable=False)`` that lack ``server_default``.
    Such columns will fail on existing production rows during migration.

    Args:
        project_root: Project root directory.
        model_files: List of changed model file paths (relative to project root).

    Returns:
        List of warning messages.
    """
    import subprocess

    warnings: list[str] = []

    for model_file in model_files:
        try:
            # Get only added lines from the diff
            result = subprocess.run(
                ["git", "diff", "HEAD", "--", model_file],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(project_root),
            )
            for line in result.stdout.split("\n"):
                if not line.startswith("+") or line.startswith("+++"):
                    continue
                added_line = line[1:]  # Strip the leading +
                if (
                    "Column(" in added_line
                    and "nullable=False" in added_line
                    and "server_default" not in added_line
                ):
                    clean_line = added_line.strip()
                    warnings.append(
                        f"NOT NULL column without server_default in "
                        f"{model_file}: {clean_line}"
                    )
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            continue

    return warnings


def check_migration_gate(project_root: Path) -> list[str]:
    """Check for database model changes without corresponding Alembic migrations.

    PRD-INFRA-035: Detects model-without-migration gaps and NOT NULL columns
    without server_default that would fail on existing production rows.

    Args:
        project_root: Project root directory.

    Returns:
        List of warning messages (empty if no issues found).
    """
    warnings: list[str] = []

    changed = _get_changed_files(project_root)
    if not changed:
        return warnings

    # FR-1: Detect model file changes
    model_files = [
        f for f in changed if "models/database" in f and f.endswith(".py")
    ]

    # FR-2: Check for new migration files
    migration_files = [
        f
        for f in changed
        if "alembic/versions/" in f and f.endswith(".py")
    ]

    if model_files and not migration_files:
        model_names = ", ".join(model_files)
        warnings.append(
            f"database.py modified ({model_names}) but no new Alembic "
            f"migration detected"
        )

    # FR-3: Check NOT NULL without server_default
    if model_files:
        warnings.extend(_check_nullable_defaults(project_root, model_files))

    return warnings


def _best_effort_migration_check(
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Append migration gate warnings (best-effort, never raises).

    PRD-INFRA-035-FR04: Soft gate integration with trw_build_check.

    Args:
        config: Framework configuration.
        failures: Mutable list to append failures into.
    """
    if not config.migration_gate_enabled:
        return

    try:
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root()

        warnings = check_migration_gate(project_root)
        for warning_msg in warnings:
            failures.append(
                ValidationFailure(
                    field="migration_gate",
                    rule="migration_check",
                    message=warning_msg,
                    severity="warning",
                )
            )
    except Exception:  # broad catch: best-effort gate, never blocks
        pass


def _best_effort_dry_check(
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Append DRY check warnings for changed files (best-effort, never raises).

    PRD-QUAL-039-FR03: Soft gate integration with trw_build_check.

    Args:
        config: Framework configuration.
        failures: Mutable list to append failures into.
    """
    if not config.dry_check_enabled:
        return

    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state.dry_check import find_duplicated_blocks

        project_root = resolve_project_root()

        # Reuse shared helper instead of duplicating subprocess call
        changed = _get_changed_files(project_root)
        py_files = [
            str(project_root / f) for f in changed
            if f.endswith(".py") and "/tests/" not in f
        ]

        if not py_files:
            return

        blocks = find_duplicated_blocks(
            py_files, min_block_size=config.dry_check_min_block_size,
        )

        for block in blocks[:5]:  # Cap at 5 warnings
            loc_summary = ", ".join(
                f"{loc.file_path}:{loc.start_line}"
                for loc in block.locations[:3]
            )
            failures.append(ValidationFailure(
                field="dry_check",
                rule="duplication_detected",
                message=(
                    f"Duplicated block ({len(block.locations)} occurrences, "
                    f"{block.block_hash}): {loc_summary}"
                ),
                severity="warning",
            ))
    except Exception:  # broad catch: best-effort gate, never blocks
        pass


def _best_effort_semantic_check(
    config: TRWConfig,
    failures: list[ValidationFailure],
) -> None:
    """Append semantic check warnings for changed files (best-effort, never raises).

    PRD-QUAL-040-FR04: Adds semantic warnings section to build gate.

    Args:
        config: Framework configuration.
        failures: Mutable list to append failures into.
    """
    if not config.semantic_checks_enabled:
        return

    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state.semantic_checks import run_semantic_checks

        project_root = resolve_project_root()

        # Reuse shared helper instead of duplicating subprocess call
        changed = _get_changed_files(project_root)
        scannable = [
            str(project_root / f)
            for f in changed
            if f.endswith((".py", ".ts", ".tsx", ".js"))
        ]

        if not scannable:
            return

        check_result = run_semantic_checks(scannable)

        # Only report warnings and errors (skip info-level)
        for finding in check_result.findings[:10]:
            if finding.severity in ("warning", "error"):
                failures.append(
                    ValidationFailure(
                        field="semantic_check",
                        rule=finding.check_id,
                        message=(
                            f"[{finding.severity}] {finding.description} "
                            f"at {finding.file_path}:{finding.line_number}"
                        ),
                        severity="warning",  # Always soft gate
                    )
                )
    except Exception:  # broad catch: best-effort gate, never blocks
        pass
