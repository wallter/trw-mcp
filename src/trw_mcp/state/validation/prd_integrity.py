"""Integrity checks layered on top of PRD quality validation."""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation._prd_integrity_duplicates import _check_duplicate_candidates
from trw_mcp.state.validation._prd_integrity_paths import (
    _check_repo_path_references,
    _extract_repo_path_refs,
    _normalize_repo_path,
    _path_exists,
    _resolve_bare_filename,
)

logger = structlog.get_logger(__name__)

_REEXPORTED_HELPERS = (
    _check_repo_path_references,
    _extract_repo_path_refs,
    _normalize_repo_path,
    _path_exists,
    _resolve_bare_filename,
)

BUILTIN_PRD_CATEGORIES: frozenset[str] = frozenset({"CORE", "QUAL", "INFRA", "FIX", "LOCAL", "EXPLR", "RESEARCH"})
"""Framework-generic PRD categories shipped with trw-mcp.

Projects MAY extend this set via `.trw/config.yaml` field
``extra_prd_categories: [CATEGORY, ...]``. The union of built-in + configured
categories is available via :func:`allowed_prd_categories` for validation.
"""


def allowed_prd_categories() -> frozenset[str]:
    """Return built-in + config-extended PRD categories."""
    extras = set(getattr(get_config(), "extra_prd_categories", ()) or ())

    # The MCP server can keep a TRWConfig singleton alive across project/session
    # transitions.  Re-read the lightweight project config here so category
    # validation honors repo-local extensions even when the singleton was built
    # before the current project root/config was available.
    try:
        from trw_mcp.state._paths import resolve_project_root
        from trw_mcp.state.persistence import FileStateReader

        config_path = resolve_project_root() / ".trw" / "config.yaml"
        if config_path.exists():
            config_data = FileStateReader().read_yaml(config_path)
            file_extras = config_data.get("extra_prd_categories", [])
            if isinstance(file_extras, list):
                extras.update(str(category) for category in file_extras)
    except Exception:  # justified: fail-open; config singleton extras still apply
        logger.debug("extra_prd_categories_file_read_failed", exc_info=True)

    return BUILTIN_PRD_CATEGORIES | frozenset(str(c).upper() for c in extras)


# Backward-compatible alias for existing callers; prefer allowed_prd_categories().
ALLOWED_PRD_CATEGORIES: frozenset[str] = BUILTIN_PRD_CATEGORIES

_VALID_FUNCTIONALITY_LEVELS: frozenset[str] = frozenset({"stub", "partial", "live"})


def _check_functionality_level_matches_status(
    frontmatter: dict[str, object],
) -> list[ValidationFailure]:
    """Enforce FPI #7 (2026-04-18): status=implemented requires functionality_level=live."""
    status = str(frontmatter.get("status", "")).strip().lower()
    level = str(frontmatter.get("functionality_level", "")).strip().lower()

    if status not in {"implemented", "partial", "stub"}:
        return []

    failures: list[ValidationFailure] = []
    if not level:
        failures.append(
            ValidationFailure(
                field="functionality_level",
                rule="aaref_functionality_level_required",
                message=(
                    "PRD status is past-implementation (implemented/partial/stub) but "
                    "functionality_level is unset. Per FPI #7 (2026-04-18), declare "
                    "functionality_level: stub | partial | live + stubs[]. See "
                    "DISTILLERY-DEFECT-LEDGER-2026-04-18.md §FPI #7."
                ),
                severity="error",
            )
        )
        return failures

    if level not in _VALID_FUNCTIONALITY_LEVELS:
        allowed = ", ".join(sorted(_VALID_FUNCTIONALITY_LEVELS))
        failures.append(
            ValidationFailure(
                field="functionality_level",
                rule="aaref_functionality_level_valid_value",
                message=f"Unsupported functionality_level {level!r}. Allowed: {allowed}.",
                severity="error",
            )
        )
        return failures

    if status == "implemented" and level != "live":
        failures.append(
            ValidationFailure(
                field="status",
                rule="aaref_implemented_requires_live",
                message=(
                    f"PRD at status=implemented but functionality_level={level!r}. "
                    "Per FPI #7: status=implemented requires functionality_level=live "
                    "AND stubs[]==[]. Downgrade status to `partial` (or `stub`) OR "
                    "land the remaining stubs. See DISTILLERY-DEFECT-LEDGER-2026-04-18.md "
                    "§FPI #7 for precedent."
                ),
                severity="error",
            )
        )

    stubs = frontmatter.get("stubs", [])
    if level == "live" and stubs:
        failures.append(
            ValidationFailure(
                field="stubs",
                rule="aaref_live_implies_empty_stubs",
                message=(
                    "functionality_level=live but stubs[] is non-empty. A live PRD "
                    "MUST have an empty stubs list (every path is real). Either downgrade "
                    "to `partial` or close the remaining stubs."
                ),
                severity="error",
            )
        )

    if level in {"stub", "partial"} and not stubs:
        failures.append(
            ValidationFailure(
                field="stubs",
                rule="aaref_non_live_requires_enumerated_stubs",
                message=(
                    f"functionality_level={level!r} but stubs[] is empty. Enumerate "
                    "the non-live paths with id/location/activation_gate/upgraded_by "
                    "so reviewers can audit what's left. See DISTILLERY-DEFECT-LEDGER-2026-04-18.md "
                    "§Deferred-Scope Items for the exemplar."
                ),
                severity="error",
            )
        )

    return failures


def _check_allowed_category(frontmatter: dict[str, object]) -> list[ValidationFailure]:
    category = str(frontmatter.get("category", "")).upper().strip()
    allowed_set = allowed_prd_categories()
    if not category or category in allowed_set:
        return []

    allowed = ", ".join(sorted(allowed_set))
    return [
        ValidationFailure(
            field="category",
            rule="aaref_category_allowlist",
            message=f"Unsupported PRD category {category!r}. Allowed categories: {allowed}.",
            severity="error",
        )
    ]


def run_prd_integrity_checks(
    content: str,
    frontmatter: dict[str, object],
    *,
    project_root: Path,
    prds_relative_path: str,
) -> tuple[list[ValidationFailure], list[str]]:
    """Return integrity failures and warnings for a PRD document."""
    failures: list[ValidationFailure] = []
    warnings: list[str] = []

    failures.extend(_check_allowed_category(frontmatter))
    failures.extend(_check_repo_path_references(content, project_root))
    failures.extend(_check_functionality_level_matches_status(frontmatter))
    warnings.extend(_check_duplicate_candidates(content, frontmatter, project_root, prds_relative_path))

    return failures, warnings
