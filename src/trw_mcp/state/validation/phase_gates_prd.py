"""PRD enforcement for phase gates.

Provides _check_prd_enforcement() which verifies governing PRDs
meet the required status before allowing phase transitions.
Extracted from phase_gates.py for module focus.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus, ValidationFailure

logger = structlog.get_logger()

# PRD status ordering for phase gate comparisons (PRD-FIX-008: includes done/merged)
_STATUS_ORDER: dict[str, int] = {
    "draft": 0,
    "review": 1,
    "approved": 2,
    "implemented": 3,
    "done": 4,
    "merged": 4,
    "deprecated": 5,
}


def _check_prd_enforcement(
    run_path: Path,
    config: TRWConfig,
    required_status: PRDStatus,
    phase_name: str,
) -> list[ValidationFailure]:
    """Check PRD readiness for a phase gate.

    Discovers governing PRDs, checks their status against the required
    minimum, and returns failures with severity based on the enforcement level.

    Args:
        run_path: Path to the run directory.
        config: Framework configuration.
        required_status: Minimum PRD status required for this phase.
        phase_name: Phase name for error messages.

    Returns:
        List of ValidationFailure entries (may be empty).
    """
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.prd_utils import discover_governing_prds, parse_frontmatter

    enforcement = config.phase_gate_enforcement

    # Skip if enforcement is off
    if enforcement == "off":
        return []

    # Check run_type — research runs skip PRD enforcement
    run_yaml = run_path / "meta" / "run.yaml"
    if run_yaml.exists():
        try:
            from trw_mcp.state.persistence import FileStateReader
            reader = FileStateReader()
            state = reader.read_yaml(run_yaml)
            if state.get("run_type") == "research":
                return []
        except (StateError, ValueError, TypeError) as exc:
            logger.debug("run_type_read_failed", path=str(run_yaml), error=str(exc))

    severity = "error" if enforcement == "strict" else "warning"
    failures: list[ValidationFailure] = []

    # Discover governing PRDs
    prd_ids = discover_governing_prds(run_path, config)

    if not prd_ids:
        failures.append(
            ValidationFailure(
                field="prd_scope",
                rule="prd_discovery",
                message=(
                    "No governing PRDs associated with this run. "
                    "Consider adding prd_scope to run.yaml."
                ),
                severity="warning",  # Advisory — always warning, never error
            )
        )
        return failures

    required_order = _STATUS_ORDER.get(required_status.value, 0)

    # Check each PRD's status
    project_root = resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)

    for prd_id in prd_ids:
        prd_file = prds_dir / f"{prd_id}.md"
        if not prd_file.exists():
            failures.append(
                ValidationFailure(
                    field=f"prd:{prd_id}",
                    rule="prd_exists",
                    message=f"PRD file not found: {prd_id}",
                    severity=severity,
                )
            )
            continue

        try:
            content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            current_status = str(fm.get("status", "draft")).lower()
            current_order = _STATUS_ORDER.get(current_status, 0)

            if current_order < required_order:
                failures.append(
                    ValidationFailure(
                        field=f"prd:{prd_id}",
                        rule="prd_status",
                        message=(
                            f"{prd_id} status is '{current_status}' but "
                            f"'{required_status.value}' is required for {phase_name} phase"
                        ),
                        severity=severity,
                    )
                )
        except (OSError, StateError, ValueError, TypeError) as exc:
            logger.warning("prd_read_failed", prd_id=prd_id, error=str(exc))
            failures.append(
                ValidationFailure(
                    field=f"prd:{prd_id}",
                    rule="prd_readable",
                    message=f"Could not read/parse PRD: {prd_id}",
                    severity=severity,
                )
            )

    return failures
