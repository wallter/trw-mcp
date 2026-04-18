"""Integrity checks layered on top of PRD quality validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.prd_utils import parse_frontmatter

logger = structlog.get_logger(__name__)

BUILTIN_PRD_CATEGORIES: frozenset[str] = frozenset({"CORE", "QUAL", "INFRA", "FIX", "LOCAL", "EXPLR", "RESEARCH"})
"""Framework-generic PRD categories shipped with trw-mcp.

Projects MAY extend this set via `.trw/config.yaml` field
``extra_prd_categories: [CATEGORY, ...]``. The union of built-in + configured
categories is available via :func:`allowed_prd_categories` for validation.
"""


def allowed_prd_categories() -> frozenset[str]:
    """Return built-in + config-extended PRD categories.

    Loads :class:`TRWConfig` lazily to avoid import-time side effects.
    Projects using trw-mcp get BUILTIN_PRD_CATEGORIES by default; TRW's own
    monorepo and other consumers may add project-specific categories (e.g.
    INTENT, SCALE, HPO, DIST) by setting ``extra_prd_categories`` in
    ``.trw/config.yaml``.
    """
    from trw_mcp.models.config import get_config

    extras = getattr(get_config(), "extra_prd_categories", ()) or ()
    return BUILTIN_PRD_CATEGORIES | frozenset(str(c).upper() for c in extras)


# Backward-compatible alias for existing callers; prefer allowed_prd_categories().
ALLOWED_PRD_CATEGORIES: frozenset[str] = BUILTIN_PRD_CATEGORIES

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_TITLE_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PATH_SUFFIXES = frozenset(
    {
        ".md",
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".toml",
        ".yaml",
        ".yml",
        ".sh",
        ".sql",
        ".css",
        ".html",
        ".txt",
    }
)
_ROOT_FILENAMES = frozenset(
    {
        "AGENTS.md",
        "AARE-F-FRAMEWORK.md",
        "CLAUDE.md",
        "FRAMEWORK.md",
        "Makefile",
        "README.md",
        "REVIEW.md",
    }
)
_TITLE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "from",
        "in",
        "of",
        "on",
        "the",
        "to",
        "with",
        "via",
        "spec",
        "docs",
        "document",
        "framework",
        "gates",
    }
)


@dataclass(slots=True)
class _PrdSnapshot:
    prd_id: str
    title: str
    status: str
    path_refs: set[str]


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


_VALID_FUNCTIONALITY_LEVELS: frozenset[str] = frozenset({"stub", "partial", "live"})


def _check_functionality_level_matches_status(
    frontmatter: dict[str, object],
) -> list[ValidationFailure]:
    """Enforce FPI #7 (2026-04-18): status=implemented requires functionality_level=live.

    See ``docs/requirements-aare-f/CLAUDE.md`` §Functionality-Level Frontmatter
    and ``docs/research/agentic-hpo/DISTILLERY-DEFECT-LEDGER-2026-04-18.md`` §FPI #7.
    """
    status = str(frontmatter.get("status", "")).strip().lower()
    level = str(frontmatter.get("functionality_level", "")).strip().lower()

    # Not all statuses require the field (draft/review/archived are pre-implementation).
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

    # Also check that stubs[] is consistent with functionality_level.
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


def _check_repo_path_references(content: str, project_root: Path) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    for ref in _extract_repo_path_refs(content):
        if _path_exists(project_root, ref):
            continue
        failures.append(
            ValidationFailure(
                field="traceability",
                rule="repo_path_exists",
                message=f"Referenced repo path does not exist: `{ref}`.",
                severity="error",
            )
        )
    return failures


def _extract_repo_path_refs(content: str) -> list[str]:
    refs: set[str] = set()
    for raw in _BACKTICK_RE.findall(content):
        candidate = _normalize_repo_path(raw)
        if candidate:
            refs.add(candidate)
    return sorted(refs)


def _normalize_repo_path(raw: str) -> str | None:
    candidate = raw.strip().strip("\"'")
    if not candidate or candidate.startswith(("http://", "https://")):
        return None
    if candidate.startswith("PRD-"):
        return None
    if " " in candidate:
        return None

    candidate = candidate.split("#", 1)[0].rstrip(").,;")
    if "::" in candidate:
        candidate = candidate.split("::", 1)[0]
    if ":" in candidate:
        prefix, _, _suffix = candidate.partition(":")
        if _looks_like_repo_path(prefix):
            candidate = prefix
    candidate = candidate.removeprefix("./")
    if candidate.startswith("/"):
        return None
    parts = Path(candidate).parts
    if ".." in parts:
        return None
    # Reject ellipsis-abbreviated path tokens (PRD-QUAL-066 FR-02). Authors often
    # write `.../foo.py` as prose shorthand for "path prefix elided"; it is not
    # a literal filesystem path. Skip rather than try to resolve.
    if any(part.startswith("...") for part in parts):
        logger.debug("prd_integrity_ellipsis_skip", raw=raw)
        return None
    return candidate if _looks_like_repo_path(candidate) else None


def _looks_like_repo_path(candidate: str) -> bool:
    if candidate in _ROOT_FILENAMES:
        return True
    if "/" in candidate or candidate.startswith("."):
        return True
    return Path(candidate).suffix in _PATH_SUFFIXES


def _path_exists(project_root: Path, rel_path: str) -> bool:
    try:
        if any(char in rel_path for char in "*?[]{}"):
            return any(project_root.glob(rel_path))

        full_path = (project_root / rel_path).resolve()
        try:
            full_path.relative_to(project_root.resolve())
        except ValueError:
            return False
        return full_path.exists()
    except OSError:
        return False


def _check_duplicate_candidates(
    content: str,
    frontmatter: dict[str, object],
    project_root: Path,
    prds_relative_path: str,
) -> list[str]:
    prds_dir = project_root / prds_relative_path
    if not prds_dir.exists():
        return []

    current_id = str(frontmatter.get("id", "")).strip()
    current_title_tokens = _title_tokens(str(frontmatter.get("title", "")))
    current_paths = set(_extract_repo_path_refs(content))
    if not current_title_tokens and not current_paths:
        return []

    warnings: list[str] = []
    for snapshot in _scan_prd_snapshots(prds_dir):
        if snapshot.prd_id == current_id or snapshot.status == "deprecated":
            continue

        title_similarity = _jaccard_similarity(current_title_tokens, _title_tokens(snapshot.title))
        shared_paths = sorted(current_paths & snapshot.path_refs)

        if title_similarity >= 0.75 or (title_similarity >= 0.45 and shared_paths) or len(shared_paths) >= 2:
            reasons: list[str] = []
            if title_similarity >= 0.45:
                reasons.append(f"title similarity {title_similarity:.2f}")
            if shared_paths:
                shared_preview = ", ".join(f"`{path}`" for path in shared_paths[:3])
                reasons.append(f"shared control points {shared_preview}")
            reason_text = "; ".join(reasons) if reasons else "overlapping scope"
            warnings.append(f"Potential overlap with {snapshot.prd_id}: {reason_text}.")

    return warnings


def _scan_prd_snapshots(prds_dir: Path) -> list[_PrdSnapshot]:
    entries: list[_PrdSnapshot] = []
    directories = [prds_dir, prds_dir.parent / "archive" / "prds"]
    for directory in directories:
        if not directory.exists():
            continue
        for prd_file in sorted(directory.glob("PRD-*.md")):
            try:
                content = prd_file.read_text(encoding="utf-8")
            except OSError:
                logger.debug("prd_integrity_snapshot_skip", path=str(prd_file), reason="read_failed")
                continue

            frontmatter = parse_frontmatter(content)
            prd_id = str(frontmatter.get("id", prd_file.stem)).strip()
            title = str(frontmatter.get("title", "")).strip()
            status = str(frontmatter.get("status", "draft")).lower().strip()
            entries.append(
                _PrdSnapshot(
                    prd_id=prd_id,
                    title=title,
                    status=status,
                    path_refs=set(_extract_repo_path_refs(content)),
                )
            )
    return entries


def _title_tokens(title: str) -> set[str]:
    tokens = {
        token for token in _TITLE_TOKEN_RE.findall(title.lower()) if len(token) > 2 and token not in _TITLE_STOPWORDS
    }
    return tokens


def _jaccard_similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)
