"""Completed-state local mapping-artifact grounding for the PRD integrity facade.

Checks file existence only, never execution, selector validity, or correctness.
Prospective mappings remain valid plans without pre-existing artifacts.
"""

from __future__ import annotations

import os
import re
import stat
from contextlib import ExitStack
from pathlib import Path

import structlog

from trw_mcp.models.requirements import ValidationFailure
from trw_mcp.state._evidence_fs import _MAX_LINK_HOPS, _resolve_absolute_metadata

logger = structlog.get_logger(__name__)

_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_LINE_SUFFIX = re.compile(r"(?::[1-9][0-9]*(?:-[1-9][0-9]*)?|#L[1-9][0-9]*(?:-L?[1-9][0-9]*)?)$")
_PLANNED_SUFFIX = re.compile(r"[ \t]+\((?:new|planned|future)\)$", re.IGNORECASE)
_SELECTOR = re.compile(
    r"[A-Za-z_][A-Za-z0-9_.]*(?:\[[A-Za-z0-9_.,=/+\-]+\])?(?:::[A-Za-z_][A-Za-z0-9_.]*(?:\[[A-Za-z0-9_.,=/+\-]+\])?)*"
)
_ROOT_FILES = frozenset({"Makefile", "Dockerfile", "LICENSE"})


def _artifact_mappings(frontmatter: dict[str, object]) -> list[object]:
    verification = frontmatter.get("verification")
    mappings = verification.get("mappings") if isinstance(verification, dict) else None
    return mappings if isinstance(mappings, list) else []


def has_completed_mapping_artifacts(frontmatter: dict[str, object], status: str) -> bool:
    return status == "implemented" and any(
        isinstance(mapping, dict) and bool(mapping.get("evidence_artifact"))
        for mapping in _artifact_mappings(frontmatter)
    )


def _local_locator(raw: str) -> tuple[str | None, str]:
    """Classify whole scalar; only explicit file/line/test selector syntax is local."""
    value = raw.strip()
    if value.startswith("`") and value.endswith("`"):
        value = value[1:-1]
    value = _PLANNED_SUFFIX.sub("", value)
    if re.match(r"^[A-Za-z]:[/\\]", value) or value.startswith(("/", "\\")):
        return None, "invalid"
    # Non-file schemes remain visibly outside this local-file check.
    if _URI.match(value) and "::" not in value and not re.search(r":\d+(?:-\d+)?$", value):
        return None, "unsupported"
    if ";" in value or re.search(r"\s+(?:and|plus)\s+|\s+--", value):
        return None, "unsupported"
    if "::" in value:
        value, selector = value.split("::", 1)
        if _SELECTOR.fullmatch(selector) is None:
            return None, "unsupported"
    value = _LINE_SUFFIX.sub("", value)
    if ":" in value or "#" in value:
        return None, "unsupported"
    if any(character.isspace() for character in value) and not value.startswith("./"):
        return None, "unsupported"
    value = value.removeprefix("./")
    if (
        not value
        or any(char in value for char in ("\\", "\x00", "\n", "\r", "*", "?", "[", "]", "{", "}"))
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        return None, "invalid"
    if "/" not in value and not Path(value).suffix and value not in _ROOT_FILES:
        return None, "unsupported"
    return value, "local"


def _identity(info: os.stat_result) -> tuple[int, int, int]:
    return stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino


def _existing_local_file(root: Path, path: str) -> bool:
    """Observe a confined regular file without following raced paths or reading bytes."""
    try:
        canonical_root = root.resolve(strict=True)
        candidate, observations, _ = _resolve_absolute_metadata(str(canonical_root / path), _MAX_LINK_HOPS)
        if not candidate.is_relative_to(canonical_root):
            return False
        if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
            return False
        parts = candidate.relative_to(canonical_root).parts
        if not parts:
            return False
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        with ExitStack() as cleanup:
            root_fd = os.open(canonical_root, flags)
            cleanup.callback(os.close, root_fd)
            parent = root_fd
            edges: list[tuple[int, str, int]] = []
            for part in parts[:-1]:
                child = os.open(part, flags, dir_fd=parent)
                cleanup.callback(os.close, child)
                edges.append((parent, part, child))
                parent = child
            before = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                return False
            current, current_observations, _ = _resolve_absolute_metadata(str(canonical_root / path), _MAX_LINK_HOPS)
            if (
                current != candidate
                or current_observations != observations
                or root.resolve(strict=True) != canonical_root
            ):
                return False
            after = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            if _identity(before) != _identity(after):
                return False
            for directory, name, descriptor in reversed(edges):
                if _identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _identity(os.fstat(descriptor)):
                    return False
            return _identity(os.stat(canonical_root, follow_symlinks=False)) == _identity(os.fstat(root_fd))
    # trw-fail-silent-allow: False makes the caller emit a blocking verification_artifact_exists failure.
    except (OSError, ValueError, RuntimeError, NotImplementedError):
        logger.debug("mapping_artifact_observation_failed", exc_info=True)
        return False


def _check_mapping_artifacts(
    frontmatter: dict[str, object], project_root: Path, *, status: str, extra_roots: list[Path] | None = None
) -> list[ValidationFailure]:
    """Ground declared completed artifacts in authorized roots, without execution claims."""
    if status != "implemented":
        return []
    failures: list[ValidationFailure] = []
    roots = [project_root, *(extra_roots or [])]
    for index, mapping in enumerate(_artifact_mappings(frontmatter)):
        if not isinstance(mapping, dict) or not isinstance(mapping.get("evidence_artifact"), str):
            continue  # Schema validation owns malformed mapping structures.
        raw = mapping["evidence_artifact"]
        path, kind = _local_locator(raw)
        field = f"verification.mappings[{index}].evidence_artifact"
        if kind == "unsupported":
            failures.append(
                ValidationFailure(
                    field=field,
                    rule="verification_artifact_locator_unsupported",
                    severity="warning",
                    message=f"Artifact locator {raw!r} is outside supported local-file syntax; existence was NOT verified.",
                )
            )
        elif kind == "invalid":
            failures.append(
                ValidationFailure(
                    field=field,
                    rule="verification_artifact_locator_invalid",
                    severity="error",
                    message=f"Artifact locator {raw!r} must name a confined relative file, without traversal or globs.",
                )
            )
        elif path is not None and not any(_existing_local_file(root, path) for root in roots):
            failures.append(
                ValidationFailure(
                    field=field,
                    rule="verification_artifact_exists",
                    severity="error",
                    message=f"Could not confirm completed-state artifact {raw!r} as an existing confined file in the authorized roots. "
                    "File existence does not establish execution or verification success.",
                )
            )
    return failures
