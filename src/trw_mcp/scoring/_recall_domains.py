"""Domain inference from file paths and query text for recall scoring.

PRD-CORE-102: Domain inference for contextual recall boosts.
PRD-CORE-116-FR02: Two-stage prefix-map + directory-stem resolution with
path-traversal sanitization.

Belongs to the ``_recall.py`` facade. Re-exported there for back-compat --
existing ``from trw_mcp.scoring._recall import infer_domains`` imports continue
to work.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import structlog

_logger = structlog.get_logger(__name__)

# Structural path stems excluded from domain inference
_STRUCTURAL_STEMS: frozenset[str] = frozenset(
    {
        "src",
        "lib",
        "test",
        "tests",
        "spec",
        "specs",
        "dist",
        "build",
        "node_modules",
        "vendor",
        "venv",
        ".venv",
        "__pycache__",
        "migrations",
        "fixtures",
        "mocks",
        "stubs",
        "helpers",
    }
)


def _extract_path_stems(paths: list[str]) -> list[str]:
    """Extract meaningful directory/module stems from file paths.

    Filters out structural stems (src, test, lib, etc.) and single-char names.
    Returns unique stems in order of first appearance.
    """
    stems: list[str] = []
    seen: set[str] = set()

    for p in paths:
        parts = PurePosixPath(p).parts
        for part in parts:
            stem = part.split(".")[0].lower()  # Strip extension
            if stem and len(stem) > 1 and stem not in _STRUCTURAL_STEMS and stem not in seen:
                seen.add(stem)
                stems.append(stem)

    return stems


def _sanitize_path(p: str) -> str:
    """Sanitize a file path for domain inference.

    Strips leading '/', rejects null bytes, and rejects any path containing
    '..' traversal components entirely (PRD-CORE-116-FR02).
    """
    if "\0" in p:
        return ""
    # Reject paths containing traversal sequences entirely
    if ".." in p.split("/"):
        return ""
    p = p.lstrip("/")
    return p


def infer_domains(
    file_paths: list[str] | None = None,
    query: str | None = None,
    path_domain_map: dict[str, str] | None = None,
    *,
    modified_files: list[str] | None = None,
) -> set[str]:
    """Infer domain labels from file paths and query text.

    Two-stage resolution per PRD-CORE-116-FR02:
    1. Configurable prefix mapping (longest prefix wins)
    2. Directory name extraction fallback

    Args:
        file_paths: File paths to extract domains from.
        query: Search query text for keyword extraction.
        path_domain_map: Explicit prefix-to-domain mapping
            (e.g. ``{"backend/payments": "payments"}``).
        modified_files: Deprecated alias for ``file_paths``.

    Returns:
        Set of unique domain label strings.
    """
    # Handle deprecated alias
    effective_paths = file_paths
    if effective_paths is None and modified_files is not None:
        effective_paths = modified_files

    domains: set[str] = set()

    if effective_paths:
        # Sanitize paths
        sanitized = [_sanitize_path(p) for p in effective_paths]
        sanitized = [p for p in sanitized if p]

        if path_domain_map:
            # Sort prefixes by length descending for greedy matching
            sorted_prefixes = sorted(path_domain_map.keys(), key=len, reverse=True)
            # Filter out prefixes with path traversal
            safe_prefixes = []
            for pfx in sorted_prefixes:
                if ".." in pfx.split("/"):
                    _logger.warning("domain_map_traversal_dropped", prefix=pfx)
                else:
                    safe_prefixes.append(pfx)

            mapped_paths: set[int] = set()
            for i, p in enumerate(sanitized):
                for pfx in safe_prefixes:
                    if p.startswith(pfx):
                        domain_label = path_domain_map[pfx]
                        domains.add(domain_label)
                        mapped_paths.add(i)
                        _logger.debug("domain_prefix_mapped", prefix=pfx, domain=domain_label)
                        break

            # Fallback: extract stems from unmapped paths
            unmapped = [p for i, p in enumerate(sanitized) if i not in mapped_paths]
            if unmapped:
                domains.update(_extract_path_stems(unmapped))
        else:
            domains.update(_extract_path_stems(sanitized))

    if query:
        for token in query.lower().split():
            token = token.strip(".,;:!?()[]{}\"'")
            if token and len(token) > 1 and token not in _STRUCTURAL_STEMS:
                domains.add(token)

    return domains
