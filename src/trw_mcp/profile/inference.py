"""Domain + task-type inference — PRD-HPO-PROF-001 FR-6 / FR-7.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``infer_domain`` resolves the ``domain`` layer name from (in precedence
order): an explicit flag, a source-path prefix, else ``unknown``.
``infer_task_type`` resolves the ``task-type`` layer name from an explicit
value, else keyword matching on the task name / PRD category, else
``generic``. Both are pure and side-effect free.

The path→domain table is NOT hardcoded here. A source layout belongs to the
consuming project, so the table is the typed ``profile_domain_path_map``
config field: generic directory conventions by default
(``models/config/_defaults.DEFAULT_DOMAIN_PATH_MAP``), overridden wholesale by
``.trw/config.yaml`` for a project whose tree differs. ``resolve_session_profile``
passes the live value; ``path_domain_map`` here is the seam that keeps this
function pure.
"""

from __future__ import annotations

from collections.abc import Mapping

from trw_mcp.models.config._defaults import DEFAULT_DOMAIN_PATH_MAP

#: Keyword → task-type mapping (FR-7). Order matters: more-specific tokens
#: should precede generic ones. Matched as substrings (case-insensitive).
_TASK_TYPE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("refactor", "refactor"),
    ("bug", "bugfix"),
    ("fix", "bugfix"),
    ("feat", "feature"),
    ("feature", "feature"),
    ("test", "test"),
    ("doc", "docs"),
)


def _normalize_prefix(prefix: str) -> str:
    """Repo-relative, forward-slashed, trailing-slash-terminated prefix."""
    cleaned = prefix.strip().replace("\\", "/").lstrip("/")
    if cleaned and not cleaned.endswith("/"):
        cleaned += "/"
    return cleaned


def infer_domain(
    *,
    explicit: str | None = None,
    prd_path: str | None = None,
    path_domain_map: Mapping[str, str] | None = None,
) -> str:
    """Infer the ``domain`` layer name (FR-6).

    Precedence: (a) explicit flag wins, (b) source-path prefix, (c) fallback
    ``unknown``. ``explicit`` is trusted verbatim (trimmed) when non-empty.

    ``path_domain_map`` is the project's prefix→domain table (normally
    ``TRWConfig.profile_domain_path_map``); ``None`` falls back to the generic
    defaults. The LONGEST matching prefix wins, so a config-supplied mapping
    resolves the same way whatever order its keys were written in.
    """
    if explicit is not None and explicit.strip():
        return explicit.strip()
    if not prd_path:
        return "unknown"

    mapping = DEFAULT_DOMAIN_PATH_MAP if path_domain_map is None else path_domain_map
    normalized = prd_path.strip().lstrip("./").replace("\\", "/")
    best_prefix = ""
    best_domain = ""
    for raw_prefix, domain in mapping.items():
        prefix = _normalize_prefix(raw_prefix)
        if not prefix or not domain or len(prefix) <= len(best_prefix):
            continue
        if normalized.startswith(prefix) or f"/{prefix}" in normalized:
            best_prefix, best_domain = prefix, domain
    return best_domain or "unknown"


def infer_task_type(
    *,
    explicit: str | None = None,
    task_name: str | None = None,
    prd_category: str | None = None,
) -> str:
    """Infer the ``task-type`` layer name (FR-7).

    Precedence: (a) explicit value wins, (b) keyword match on task name then
    PRD category, (c) fallback ``generic``. Matching is case-insensitive
    substring against the keyword table.
    """
    if explicit is not None and explicit.strip():
        return explicit.strip()
    haystacks = [h for h in (task_name, prd_category) if h]
    for haystack in haystacks:
        lowered = haystack.lower()
        for keyword, task_type in _TASK_TYPE_KEYWORDS:
            if keyword in lowered:
                return task_type
    return "generic"


__all__ = ["infer_domain", "infer_task_type"]
