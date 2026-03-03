"""Build status and integration checks for phase gates.

Provides _check_build_status(), _best_effort_build_check(), and
_best_effort_integration_check() which validate cached build results
and tool registration before allowing phase transitions.
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

    try:
        data = FileStateReader().read_yaml(cache_path)
    except Exception:
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
                field="build_mypy",
                rule="mypy_clean",
                message="mypy reported errors — run trw_build_check() for details",
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
    except Exception:
        pass  # Best-effort


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
    except Exception:
        pass  # Best-effort — scanner errors never block
