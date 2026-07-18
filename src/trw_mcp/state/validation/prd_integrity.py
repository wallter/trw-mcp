"""Integrity checks layered on top of PRD quality validation."""

from __future__ import annotations

import time
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation._prd_integrity_contracts import (
    _check_compatibility_exceptions as _check_compatibility_exceptions,
)
from trw_mcp.state.validation._prd_integrity_contracts import (
    _check_frontmatter_parses as _check_frontmatter_parses,
)
from trw_mcp.state.validation._prd_integrity_duplicates import _check_duplicate_candidates
from trw_mcp.state.validation._prd_integrity_lanes import (
    ValidationLaneResult,
    evaluate_changed_scope,
    evaluate_full_corpus,
)
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
    # PRD-QUAL-121-FR05 typed validation lanes (facade re-export)
    ValidationLaneResult,
    evaluate_changed_scope,
    evaluate_full_corpus,
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

# PRD-QUAL-097-FR01: canonical status vocabulary + alias map.
CANONICAL_STATUSES: frozenset[str] = frozenset({"draft", "review", "approved", "implemented", "deprecated"})
"""The five template-sanctioned PRD statuses. Single source for the truthfulness gates."""

_STATUS_ALIASES: dict[str, str] = {
    # implemented-family
    "done": "implemented",
    "delivered": "implemented",
    "complete": "implemented",
    # in-flight -> draft
    "in-progress": "draft",
    "in_progress": "draft",
    "wip": "draft",
    # review-ready
    "ready": "review",
}
"""Common non-canonical variants mapped to their canonical equivalent (FR01)."""


def normalize_status(status: str) -> tuple[str, bool]:
    """Normalize a PRD ``status`` to its canonical value (PRD-QUAL-097-FR01).

    Returns ``(canonical_value, is_canonical)``:
    - a canonical status maps to itself with ``True``;
    - a known alias maps to its canonical target with ``True``;
    - an unknown / empty status echoes back lowercased with ``False``.
    """
    normalized = status.strip().lower()
    if normalized in CANONICAL_STATUSES:
        return normalized, True
    alias = _STATUS_ALIASES.get(normalized)
    if alias is not None:
        return alias, True
    return normalized, False


def _check_status_canonical(frontmatter: dict[str, object]) -> list[str]:
    """PRD-QUAL-097-FR02: warn (never block) when ``status`` is non-canonical.

    A non-canonical status is brownfield-safe (NFR01): it produces a WARNING
    naming the suggested canonical alias, and never a ``ValidationFailure``.
    """
    raw = str(frontmatter.get("status", "")).strip()
    if not raw:
        return []
    canonical, is_canonical = normalize_status(raw)
    if is_canonical and canonical == raw.lower():
        return []  # already a canonical value as written
    if is_canonical:
        return [
            f"PRD status {raw!r} is non-canonical; canonicalize to {canonical!r} "
            f"(canonical set: {', '.join(sorted(CANONICAL_STATUSES))})."
        ]
    return [
        f"PRD status {raw!r} is non-canonical and has no known alias; pick one of the "
        f"canonical statuses: {', '.join(sorted(CANONICAL_STATUSES))}."
    ]


def _check_functionality_level_matches_status(
    frontmatter: dict[str, object],
) -> list[ValidationFailure]:
    """Enforce FPI #7 (2026-04-18): status=implemented requires functionality_level=live."""
    raw_status = str(frontmatter.get("status", "")).strip().lower()
    level = str(frontmatter.get("functionality_level", "")).strip().lower()

    # PRD-QUAL-097-FR03 (corrected): the HARD ValidationFailures fire ONLY for the
    # pre-QUAL-097 trigger set — raw ``implemented`` plus the ``partial``/``stub``
    # status sentinels. The implemented-family ALIASES (done/delivered/complete) are
    # deliberately NOT hard triggers here: collapsing them into ``implemented`` was a
    # behavior superset that regressed ~286 corpus PRDs (a ``status: done`` PRD lacking
    # functionality_level became valid=False). The ratchet gate ``make
    # prd-truthfulness-gate`` is the hard enforcement for active-lies (it counts ``done``
    # as implemented-family); this integrity check instead WARNS at validate-time via
    # ``_check_implemented_alias_functionality`` — consistent with the truthfulness
    # script treating ``done`` + no-level as a non-blocking class-C migration item, so no
    # corpus-wide ``valid`` regression occurs.
    is_hard_trigger = (raw_status == "implemented") or (raw_status in {"partial", "stub"})
    if not is_hard_trigger:
        return []
    is_implemented_family = raw_status == "implemented"

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

    if is_implemented_family and level != "live":
        # Documented FPI #7 exception (docs/requirements-aare-f/CLAUDE.md
        # §Functionality-Level Frontmatter, 2026-04-18): status=implemented is
        # ALSO permitted with functionality_level=partial when an EXPLICIT
        # implementation_scope note names the deferred paths AND stubs[]
        # enumerates them. Prior to PRD-CORE-213 the code hard-failed every
        # non-live combination, forcing truthful partial claims to either lie
        # (level: live) or misstate status — the opposite of FPI #7's intent.
        implementation_scope = str(frontmatter.get("implementation_scope", "") or "").strip()
        stubs_enumerated = bool(frontmatter.get("stubs", []))
        documented_partial_exception = level == "partial" and implementation_scope and stubs_enumerated
        if not documented_partial_exception:
            failures.append(
                ValidationFailure(
                    field="status",
                    rule="aaref_implemented_requires_live",
                    message=(
                        f"PRD at status=implemented but functionality_level={level!r}. "
                        "Per FPI #7: status=implemented requires functionality_level=live "
                        "AND stubs[]==[], OR functionality_level=partial WITH an explicit "
                        "implementation_scope naming the deferred paths AND enumerated "
                        "stubs[]. Downgrade status, land the remaining stubs, or add the "
                        "scope note + stub entries. See DISTILLERY-DEFECT-LEDGER-2026-04-18.md "
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


def _check_implemented_alias_functionality(frontmatter: dict[str, object]) -> list[str]:
    """PRD-QUAL-097-FR03 (corrected): WARN (never block) on implemented-alias mismatches.

    The implemented-family aliases (``done``/``delivered``/``complete``) are NOT hard
    triggers in :func:`_check_functionality_level_matches_status` — collapsing them into
    canonical ``implemented`` regressed ~286 corpus PRDs to ``valid=False``. Instead, when
    a PRD uses one of those aliases AND its functionality_level is unset OR not ``live`` OR
    (``live`` with a non-empty ``stubs[]``), surface ONE advisory warning recommending the
    canonical, audit-clean shape. This warning never flips ``valid``; the hard enforcement
    for active-lies remains ``make prd-truthfulness-gate``.
    """
    raw_status = str(frontmatter.get("status", "")).strip().lower()
    if raw_status not in {"done", "delivered", "complete"}:
        return []

    level = str(frontmatter.get("functionality_level", "")).strip().lower()
    stubs = frontmatter.get("stubs", [])
    is_clean_live = level == "live" and not stubs
    if is_clean_live:
        return []

    return [
        f"PRD status {raw_status!r} is an implemented-family alias but its "
        "functionality_level is unset, not 'live', or 'live' with a non-empty stubs[]. "
        "For an audit-clean implemented claim, use canonical `status: implemented` with "
        "`functionality_level: live` + empty `stubs[]`, or set the accurate "
        "functionality_level (stub/partial + enumerated stubs[]). This is a WARNING — it "
        "does not block validation; `make prd-truthfulness-gate` is the hard gate."
    ]


# PRD-CORE-218-FR08: SurfaceDelta gate extracted to the sibling module per the
# 350 effective-LOC gate; re-exported here so the facade contract is unchanged.
from trw_mcp.state.validation._prd_integrity_surface_delta import (  # noqa: E402
    _check_surface_delta as _check_surface_delta,
)


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


def build_path_index_partial_warning(partial_report: dict[str, object]) -> str | None:
    """Render the LOUD grounding-degrade warning from a *partial_report*.

    Returns a single ``path_index_partial:``-prefixed string when the repo
    basename index truncated at a runaway cap (so bare-filename grounding was
    skipped and hallucinated references could not be detected), else ``None``.
    Callers prepend it to ``integrity_warnings`` — the channel the trw_prd_validate
    tool serializes into its output — so a truncated index is never silent.
    """
    if not partial_report.get("path_index_partial"):
        return None
    raw_skipped = partial_report.get("path_index_skipped_refs", 0)
    skipped = raw_skipped if isinstance(raw_skipped, int) else 0
    return (
        f"path_index_partial: bare-filename grounding was SKIPPED for {skipped} reference(s) — "
        "the repo file index hit a runaway cap (path_index_max_files/path_index_max_seconds) and "
        "is INCOMPLETE, so hallucinated bare-filename references could not be detected. Prune bulk "
        "trees via .trw/config.yaml path_index_exclude_dirs or raise the caps so the index completes."
    )


# PRD-FIX-112: named integrity check groups, in execution order. The deadline
# guard is evaluated BETWEEN groups, so the ordering here is the skip order —
# cheap/authoritative content checks run first, the repo-grounded checks last.
INTEGRITY_CHECK_GROUPS: tuple[str, ...] = (
    "integrity:frontmatter_parse",
    "integrity:allowed_category",
    "integrity:compatibility_exceptions",
    "integrity:surface_delta",
    "integrity:repo_path_references",
    "integrity:functionality_level",
    "integrity:status_canonical",
    "integrity:implemented_alias",
    "integrity:duplicate_candidates",
)


def run_prd_integrity_checks(
    content: str,
    frontmatter: dict[str, object],
    *,
    project_root: Path,
    prds_relative_path: str,
    extra_roots: list[Path] | None = None,
    partial_report: dict[str, object] | None = None,
    deadline: float | None = None,
    skipped: list[str] | None = None,
) -> tuple[list[ValidationFailure], list[str]]:
    """Return integrity failures and warnings for a PRD document.

    *extra_roots* are sibling repo roots checked (in addition to
    *project_root*) when resolving backtick-quoted path references, for
    multi-repo workspaces — Potemkin-Gate defect B (sub_zAfRqZYYq2KtF72d).

    *partial_report* (opt-in out-param) is forwarded to
    :func:`_check_repo_path_references`; when supplied it receives the
    bare-filename grounding-degrade signal (``path_index_partial`` /
    ``path_index_skipped_refs``) so callers can surface a truncated index in the
    validation result rather than silently trusting a skipped check.

    *deadline* (PRD-FIX-112) is an optional ``time.monotonic()`` timestamp. It is
    checked BETWEEN check groups: once ``time.monotonic() > deadline`` the
    remaining groups are SKIPPED (never raised, never silently passed) and their
    names (see :data:`INTEGRITY_CHECK_GROUPS`) are appended to *skipped* (an
    opt-in out-param list) so the caller can surface a visibly-partial result.
    """
    failures: list[ValidationFailure] = []
    warnings: list[str] = []
    _skipped = skipped if skipped is not None else []

    def _budget_ok(group: str) -> bool:
        if deadline is not None and time.monotonic() > deadline:
            _skipped.append(group)
            return False
        return True

    if _budget_ok("integrity:frontmatter_parse"):
        failures.extend(_check_frontmatter_parses(content))
    if _budget_ok("integrity:allowed_category"):
        failures.extend(_check_allowed_category(frontmatter))
    if _budget_ok("integrity:compatibility_exceptions"):
        failures.extend(_check_compatibility_exceptions(frontmatter))
    if _budget_ok("integrity:surface_delta"):
        failures.extend(_check_surface_delta(frontmatter))
    if _budget_ok("integrity:repo_path_references"):
        failures.extend(
            _check_repo_path_references(content, project_root, extra_roots=extra_roots, partial_report=partial_report)
        )
    if _budget_ok("integrity:functionality_level"):
        failures.extend(_check_functionality_level_matches_status(frontmatter))
    if _budget_ok("integrity:status_canonical"):
        warnings.extend(_check_status_canonical(frontmatter))
    if _budget_ok("integrity:implemented_alias"):
        warnings.extend(_check_implemented_alias_functionality(frontmatter))
    if _budget_ok("integrity:duplicate_candidates"):
        warnings.extend(_check_duplicate_candidates(content, frontmatter, project_root, prds_relative_path))

    return failures, warnings
