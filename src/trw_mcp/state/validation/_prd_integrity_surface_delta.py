"""PRD-CORE-218-FR08 SurfaceDelta readiness gate.

Belongs to the ``prd_integrity.py`` facade (re-exported there) — extracted as a
sibling per the 350 effective-LOC module gate, matching the existing
``_prd_integrity_duplicates`` / ``_prd_integrity_lanes`` / ``_prd_integrity_paths``
decomposition pattern.
"""

from __future__ import annotations

from trw_mcp.models.requirements import ValidationFailure

# PRD-CORE-218-FR08: a PRD that declares a public-surface change must carry a
# typed SurfaceDelta block. ``additions`` / ``removals`` must be PRESENT lists
# (either may be empty); the scalar fields below must be present and non-blank.
_SURFACE_DELTA_REQUIRED_LISTS: tuple[str, ...] = ("additions", "removals")
_SURFACE_DELTA_REQUIRED_SCALARS: tuple[str, ...] = (
    "default_exposure",
    "migration",
    "owner",
    "measured_benefit",
    "reevaluation",
)


def _check_surface_delta(frontmatter: dict[str, object]) -> list[ValidationFailure]:
    """PRD-CORE-218-FR08: fail-closed SurfaceDelta gate for declared surface change.

    A PRD declares a public-surface change by carrying a ``surface_delta:`` block
    OR a ``public_surface: true`` marker. Such a PRD MUST provide a typed
    SurfaceDelta naming ``additions``, ``removals``, ``default_exposure``,
    ``migration``, ``owner``, ``measured_benefit``, and ``reevaluation``. Net
    growth (``len(additions) > len(removals)``) additionally requires an approved,
    unexpired exception (``exception_owner`` + ``exception_expiry``).

    A PRD that declares NO surface change (no ``surface_delta`` block AND no
    ``public_surface: true`` marker) passes unchanged — this gate is fail-closed
    only for DECLARED changes. Detecting UNdeclared surface changes is the
    instruction-lint parity job (FR06), not this gate.
    """
    from datetime import date as _date
    from datetime import datetime, timezone

    delta = frontmatter.get("surface_delta")
    declared = bool(frontmatter.get("public_surface")) or delta is not None
    if not declared:
        return []

    if delta is None:
        return [
            ValidationFailure(
                field="surface_delta",
                rule="core218_surface_delta_required",
                message=(
                    "PRD marks public_surface: true but has no surface_delta block. "
                    "Declare a typed SurfaceDelta naming additions, removals, "
                    "default_exposure, migration, owner, measured_benefit, and reevaluation."
                ),
                severity="error",
            )
        ]
    if not isinstance(delta, dict):
        return [
            ValidationFailure(
                field="surface_delta",
                rule="core218_surface_delta_required",
                message="surface_delta must be a mapping with the full SurfaceDelta contract.",
                severity="error",
            )
        ]

    missing = [name for name in _SURFACE_DELTA_REQUIRED_SCALARS if _is_blank(delta.get(name))]
    missing += [name for name in _SURFACE_DELTA_REQUIRED_LISTS if not isinstance(delta.get(name), list)]
    if missing:
        return [
            ValidationFailure(
                field="surface_delta",
                rule="core218_surface_delta_required",
                message=(
                    f"SurfaceDelta is missing required fields: {', '.join(sorted(missing))}. "
                    "Every declared public-surface change names additions (list), removals "
                    "(list), default_exposure, migration, owner, measured_benefit, and reevaluation."
                ),
                severity="error",
            )
        ]

    additions = delta.get("additions")
    removals = delta.get("removals")
    if not isinstance(additions, list) or not isinstance(removals, list):
        return []  # unreachable: presence guaranteed above; keeps mypy strict happy

    if len(additions) <= len(removals):
        return []  # net-neutral or net-reduction needs no exception

    # Net growth: require an approved, unexpired expiring exception.
    exception_owner = str(delta.get("exception_owner", "") or "").strip()
    exception_expiry = str(delta.get("exception_expiry", "") or "").strip()
    if not exception_owner or not exception_expiry:
        return [
            ValidationFailure(
                field="surface_delta",
                rule="core218_surface_delta_net_growth",
                message=(
                    f"SurfaceDelta is net growth ({len(additions)} additions > "
                    f"{len(removals)} removals) but lacks an approved expiring exception. "
                    "Net public-surface growth requires exception_owner and exception_expiry."
                ),
                severity="error",
            )
        ]
    try:
        expiry = _date.fromisoformat(exception_expiry)
    except ValueError:
        return [
            ValidationFailure(
                field="surface_delta",
                rule="core218_surface_delta_net_growth",
                message="SurfaceDelta exception_expiry must be an ISO date (YYYY-MM-DD).",
                severity="error",
            )
        ]
    if expiry < datetime.now(timezone.utc).date():
        return [
            ValidationFailure(
                field="surface_delta",
                rule="core218_surface_delta_exception_expired",
                message=(
                    f"SurfaceDelta net-growth exception expired {expiry.isoformat()}: "
                    "expiry is automatic — renew the operator approval or remove the additions."
                ),
                severity="error",
            )
        ]
    return []


def _is_blank(value: object) -> bool:
    """True when a SurfaceDelta field is unset or an empty scalar/collection."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict)):
        return len(value) == 0
    return False
