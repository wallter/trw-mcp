"""PRD integrity helpers for repo-path normalization and existence checks."""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from trw_mcp.models.requirements import ValidationFailure

logger = structlog.get_logger(__name__)

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
# Directories excluded from bare-filename rglob so vendor trees, build outputs,
# and run-artifact dumps don't inflate match counts or latency (NFR-02/NFR-04).
_GLOB_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".trw",
    }
)


def _check_repo_path_references(content: str, project_root: Path) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    bare_cache: dict[str, tuple[bool, int]] = {}
    for ref in _extract_repo_path_refs(content):
        if "/" not in ref and Path(ref).suffix in _KNOWN_SOURCE_SUFFIXES:
            resolved, count = _resolve_bare_filename(project_root, ref, cache=bare_cache)
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


def _resolve_bare_filename(
    project_root: Path,
    rel_path: str,
    cache: dict[str, tuple[bool, int]] | None = None,
) -> tuple[bool, int]:
    """PRD-QUAL-067 FR-01: bounded rglob resolver for `/`-less path tokens."""
    if cache is not None and rel_path in cache:
        return cache[rel_path]

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

    result = (match_count == 1, match_count)
    if cache is not None:
        cache[rel_path] = result
    return result
