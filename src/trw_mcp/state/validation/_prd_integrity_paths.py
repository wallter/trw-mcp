"""PRD integrity helpers for repo-path normalization and existence checks."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state.validation._path_exclusions import PATH_INDEX_EXCLUDE_DIRS

logger = structlog.get_logger(__name__)

# Fallback caps used only when ``get_config()`` is unavailable; they MIRROR the
# ``path_index_max_files`` / ``path_index_max_seconds`` Pydantic field defaults
# in ``models/config/_fields_prd.py`` (the config knobs are authoritative).
_DEFAULT_PATH_INDEX_MAX_FILES = 1_000_000
_DEFAULT_PATH_INDEX_MAX_SECONDS = 5.0

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
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
# PRD-QUAL-067: bare-filename resolver gate. Only tokens with these extensions
# trigger the bounded-rglob resolver; other extensions fall through to the
# legacy repo-root-anchored _path_exists contract. Strict subset of
# _PATH_SUFFIXES — extensions agents most commonly cite by basename in PRDs.
_KNOWN_SOURCE_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".ts", ".tsx", ".md", ".yaml", ".yml", ".json", ".sh", ".toml"}
)
# Directories excluded from the bare-filename basename walk so vendor trees,
# build outputs, and run-artifact dumps don't inflate match counts or latency
# (NFR-02/NFR-04). Module-level alias of the single shared constant
# ``PATH_INDEX_EXCLUDE_DIRS`` — kept as a named attribute so existing test
# monkeypatches on ``_GLOB_EXCLUDE_DIRS`` still resolve, and the walk (which
# reads the module global by name at call time) still sees the patch.
_GLOB_EXCLUDE_DIRS: frozenset[str] = PATH_INDEX_EXCLUDE_DIRS


def _check_repo_path_references(
    content: str,
    project_root: Path,
    *,
    extra_roots: list[Path] | None = None,
    partial_report: dict[str, object] | None = None,
) -> list[ValidationFailure]:
    """Verify backtick-quoted repo paths in *content* exist.

    Resolves each referenced path against *project_root* first, then against
    any *extra_roots* (sibling repos in a multi-repo workspace). A path that
    exists under ANY supplied root is considered present.

    Potemkin-Gate submission sub_zAfRqZYYq2KtF72d defect B: without
    *extra_roots*, key-file paths living in a sibling code repo registered as
    6 hard ``repo_path_exists`` errors and dragged a structurally-perfect PRD
    to grade D / valid:false. *extra_roots* defaults to ``None`` so the
    single-repo contract is byte-for-byte unchanged.

    *partial_report* (opt-in out-param): when supplied, it is populated with the
    grounding-degrade signal so callers can surface it in the validation result.
    Keys written: ``path_index_partial`` (bool — the basename index truncated at
    a runaway cap) and ``path_index_skipped_refs`` (int — bare filenames that
    degraded to advisory-skip because a partial index cannot prove absence). A
    silent skip is exactly what let a broken (always-partial) index masquerade as
    a passing grounding check, so this makes the degrade observable.
    """
    roots = [project_root, *(extra_roots or [])]
    failures: list[ValidationFailure] = []
    skipped_bare_refs = 0
    # One basename index PER root (lazily built on first bare lookup).
    bare_caches: list[dict[str, tuple[bool, int]]] = [{} for _ in roots]
    for ref in _extract_repo_path_refs(content):
        if "/" not in ref and Path(ref).suffix in _KNOWN_SOURCE_SUFFIXES:
            resolved, count = _resolve_bare_filename_any_root(roots, ref, caches=bare_caches)
            if resolved:
                logger.debug(
                    "prd_integrity_bare_filename_resolved",
                    raw=ref,
                    match_count=count,
                )
                continue
            if count > 1:
                logger.debug(
                    "prd_integrity_bare_filename_ambiguous",
                    raw=ref,
                    match_count=count,
                )
                failures.append(
                    ValidationFailure(
                        field="traceability",
                        rule="repo_path_exists",
                        message=(
                            f"Bare filename `{ref}` has multiple matches ({count}+); "
                            "disambiguate with a directory prefix."
                        ),
                        severity="warning",
                    )
                )
            elif _index_is_partial(bare_caches):
                # Bounded walk hit a file/time cap: a truncated index cannot
                # prove the basename is absent, so degrade to advisory-skip
                # rather than emit a false "no match in repo" warning.
                skipped_bare_refs += 1
                logger.debug(
                    "prd_integrity_bare_filename_partial_skip",
                    raw=ref,
                )
            else:
                logger.debug(
                    "prd_integrity_bare_filename_unresolved",
                    raw=ref,
                    match_count=0,
                )
                failures.append(
                    ValidationFailure(
                        field="traceability",
                        rule="repo_path_exists",
                        message=(
                            f"Bare filename `{ref}` has no match in repo; "
                            "disambiguate with a directory prefix or verify the reference."
                        ),
                        severity="warning",
                    )
                )
            continue

        if any(_path_exists(root, ref) for root in roots):
            continue
        failures.append(
            ValidationFailure(
                field="traceability",
                rule="repo_path_exists",
                message=f"Referenced repo path does not exist: `{ref}`.",
                severity="error",
            )
        )
    if partial_report is not None:
        partial_report["path_index_partial"] = _index_is_partial(bare_caches)
        partial_report["path_index_skipped_refs"] = skipped_bare_refs
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
    if "..." in candidate:
        logger.debug("prd_integrity_ellipsis_skip", raw=raw)
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
    if ".." in Path(candidate).parts:
        return None
    return candidate if _looks_like_repo_path(candidate) else None


def _looks_like_repo_path(candidate: str) -> bool:
    if candidate in _ROOT_FILENAMES:
        return True
    if "/" in candidate or candidate.startswith("."):
        return True
    return Path(candidate).suffix in _PATH_SUFFIXES


def _path_exists(
    project_root: Path,
    rel_path: str,
    *,
    extra_roots: list[Path] | None = None,
) -> bool:
    """True if *rel_path* exists under *project_root* or any *extra_roots*.

    Each root is checked independently with the same path-escape guard, so a
    sibling repo (multi-repo workspace) can satisfy a reference without
    weakening the traversal protection for any single root. *extra_roots*
    defaults to ``None`` — the original single-root contract.
    """
    return any(_path_exists_under_root(root, rel_path) for root in (project_root, *(extra_roots or [])))


def _path_exists_under_root(project_root: Path, rel_path: str) -> bool:
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


_INDEX_BUILT_SENTINEL = "\x00__index_built__\x00"
# Marks a basename index that stopped early at a file/time cap. When present,
# an absent basename is NOT a proven miss (the walk may have skipped the file),
# so the resolver degrades to advisory-skip instead of a false "no match"
# warning. Keyed with a leading NUL so it can never collide with a real file.
_INDEX_PARTIAL_SENTINEL = "\x00__index_partial__\x00"


def _resolve_walk_bounds() -> tuple[frozenset[str], int, float]:
    """Resolve the effective exclude-dir set and file/time caps for the walk.

    Unions the shared :data:`_GLOB_EXCLUDE_DIRS` with the project's
    ``path_index_exclude_dirs`` knob, and reads the ``path_index_max_files`` /
    ``path_index_max_seconds`` caps. Reads ``_GLOB_EXCLUDE_DIRS`` as a module
    global (not a closure) so test monkeypatches on it still apply. Degrades to
    the shared constant + default caps if config is unavailable.
    """
    exclude = _GLOB_EXCLUDE_DIRS
    max_files = _DEFAULT_PATH_INDEX_MAX_FILES
    max_seconds = _DEFAULT_PATH_INDEX_MAX_SECONDS
    try:
        cfg = get_config()
        extra = getattr(cfg, "path_index_exclude_dirs", None) or []
        if extra:
            exclude = exclude | frozenset(str(d) for d in extra)
        max_files = int(getattr(cfg, "path_index_max_files", max_files))
        max_seconds = float(getattr(cfg, "path_index_max_seconds", max_seconds))
    except Exception:  # justified: config unavailable must not break validation
        logger.warning("prd_integrity_path_index_config_unavailable", exc_info=True)
    return exclude, max_files, max_seconds


def _populate_basename_index(
    project_root: Path,
    cache: dict[str, tuple[bool, int]],
) -> None:
    """One-shot BOUNDED walk that builds {basename: (unique, count)} for files
    under ``project_root``, pruning the exclude-dir set in-place so vendor /
    cache / build trees never get descended into.

    Single os.walk pass replaces per-token ``rglob`` calls. The walk is bounded
    by ``path_index_max_files`` and ``path_index_max_seconds``: on a large
    monorepo an unbounded walk cost ~8s per validated PRD. When either cap trips
    the walk stops early and the index is marked partial via
    :data:`_INDEX_PARTIAL_SENTINEL`, so a truncated index can never emit a false
    "no match in repo" warning (the resolver degrades to advisory-skip).
    """

    exclude, max_files, max_seconds = _resolve_walk_bounds()
    counts: dict[str, int] = {}
    seen = 0
    partial = False
    start = time.monotonic()
    try:
        for _dirpath, dirnames, filenames in os.walk(project_root):
            # Prune excluded subtrees BEFORE descending — this is the order-of-
            # magnitude speedup vs the prior rglob-then-filter approach.
            dirnames[:] = [d for d in dirnames if d not in exclude]
            for fname in filenames:
                counts[fname] = counts.get(fname, 0) + 1
                seen += 1
            if seen >= max_files or (time.monotonic() - start) >= max_seconds:
                partial = True
                break
    except OSError:
        return

    for fname, n in counts.items():
        cache[fname] = (n == 1, n)
    cache[_INDEX_BUILT_SENTINEL] = (True, 0)
    if partial:
        cache[_INDEX_PARTIAL_SENTINEL] = (True, 0)
        logger.debug(
            "prd_integrity_basename_index_partial",
            files_indexed=seen,
            max_files=max_files,
            max_seconds=max_seconds,
        )


def _index_is_partial(caches: list[dict[str, tuple[bool, int]]]) -> bool:
    """True when ANY root's basename index stopped early at a cap.

    A partial index cannot prove a basename is absent, so its unresolved
    lookups degrade to advisory-skip rather than a false-negative warning.
    """
    return any(_INDEX_PARTIAL_SENTINEL in cache for cache in caches)


def _resolve_bare_filename(
    project_root: Path,
    rel_path: str,
    cache: dict[str, tuple[bool, int]] | None = None,
) -> tuple[bool, int]:
    """PRD-QUAL-067 FR-01: bounded resolver for ``/``-less path tokens.

    When ``cache`` is supplied, lazily builds a basename index on the first
    call and serves subsequent lookups from memory. When ``cache`` is None
    (legacy path), falls back to a single bounded ``rglob`` for one-shot use.
    """

    if cache is not None and rel_path in cache:
        return cache[rel_path]

    if cache is not None:
        if _INDEX_BUILT_SENTINEL not in cache:
            _populate_basename_index(project_root, cache)
        return cache.get(rel_path, (False, 0))

    # Legacy single-shot path (cache=None): one rglob, bounded by exclude dirs.
    match_count = 0
    try:
        for p in project_root.rglob(rel_path):
            try:
                parts = p.relative_to(project_root).parts
            except ValueError:
                continue
            if any(part in _GLOB_EXCLUDE_DIRS for part in parts):
                continue
            match_count += 1
            if match_count > 1:
                break
    except OSError:
        match_count = 0

    return (match_count == 1, match_count)


def _resolve_bare_filename_any_root(
    roots: list[Path],
    rel_path: str,
    caches: list[dict[str, tuple[bool, int]]],
) -> tuple[bool, int]:
    """Resolve a bare filename across multiple workspace roots.

    Returns ``(resolved, count)`` where *resolved* is True if the basename
    matches exactly once across ALL roots combined, and *count* is the
    aggregate match count (so an ambiguous-across-repos token still surfaces
    the disambiguation warning). Each root keeps its own lazily-built basename
    index via the parallel *caches* list. Multi-root support for Potemkin-Gate
    defect B (sub_zAfRqZYYq2KtF72d).
    """
    total = 0
    for root, cache in zip(roots, caches, strict=True):
        _resolved, count = _resolve_bare_filename(root, rel_path, cache=cache)
        total += count
    return (total == 1, total)
