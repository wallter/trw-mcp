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


def _check_frontmatter_parses(content: str) -> list[ValidationFailure]:
    """FR01 (PRD-QUAL-091): malformed frontmatter is a failure, not a silent skip.

    ``parse_frontmatter`` degrades to ``{}`` on unparseable YAML, so a PRD with a
    broken ``---`` block (duplicate keys, unclosed flow, bad alias) is
    indistinguishable from a no-frontmatter PRD and escapes every frontmatter
    gate. This re-parses strictly: if a ``---`` block exists but does NOT parse to
    a mapping, emit ``aaref_frontmatter_parse``.

    Returns ``[]`` when there is no frontmatter block at all (a distinct,
    legitimate case) or when the block parses to a mapping.
    """
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    match = _FRONTMATTER_RE.match(content)
    if match is None:
        return []  # no --- block: not a malformed PRD, just frontmatter-less

    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    yaml = YAML(typ="safe")
    detail = ""
    try:
        data = yaml.load(match.group(1))
    except (YAMLError, ValueError, TypeError) as exc:
        data = None
        detail = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
    else:
        if isinstance(data, dict):
            return []
        detail = f"frontmatter parsed to {type(data).__name__}, not a mapping"

    return [
        ValidationFailure(
            field="frontmatter",
            rule="aaref_frontmatter_parse",
            message=(
                "PRD begins with a `---` frontmatter delimiter but the enclosed "
                "block does not parse to a YAML mapping (likely a duplicate key, "
                "unclosed flow sequence, or undefined alias). Such a PRD silently "
                "escapes every frontmatter gate (status, functionality_level, "
                f"ip_tier). Fix the YAML so it parses. Detail: {detail}"
            ),
            severity="error",
        )
    ]


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

    failures.extend(_check_frontmatter_parses(content))
    failures.extend(_check_allowed_category(frontmatter))
    failures.extend(_check_repo_path_references(content, project_root))
    failures.extend(_check_functionality_level_matches_status(frontmatter))
    warnings.extend(_check_status_canonical(frontmatter))
    warnings.extend(_check_implemented_alias_functionality(frontmatter))
    warnings.extend(_check_duplicate_candidates(content, frontmatter, project_root, prds_relative_path))

    return failures, warnings
