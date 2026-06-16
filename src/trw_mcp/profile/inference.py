"""Domain + task-type inference — PRD-HPO-PROF-001 FR-6 / FR-7.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``infer_domain`` resolves the ``domain`` layer name from (in precedence
order): an explicit flag, a PRD/file path prefix, else ``unknown``.
``infer_task_type`` resolves the ``task-type`` layer name from an explicit
value, else keyword matching on the task name / PRD category, else
``generic``. Both are pure and side-effect free.
"""

from __future__ import annotations

#: PRD/file path-prefix → domain mapping (FR-6 branch b). Checked in order;
#: the first prefix that matches the normalized path wins.
_PATH_PREFIX_DOMAINS: tuple[tuple[str, str], ...] = (
    ("platform/", "frontend"),
    ("backend/", "backend"),
    ("trw-eval/", "eval"),
    ("trw-mcp/", "core"),
    ("trw-memory/", "memory"),
)

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


def infer_domain(
    *,
    explicit: str | None = None,
    prd_path: str | None = None,
) -> str:
    """Infer the ``domain`` layer name (FR-6).

    Precedence: (a) explicit flag wins, (b) PRD/file path prefix, (c) fallback
    ``unknown``. ``explicit`` is trusted verbatim (trimmed) when non-empty.
    """
    if explicit is not None and explicit.strip():
        return explicit.strip()
    if prd_path:
        normalized = prd_path.strip().lstrip("./").replace("\\", "/")
        for prefix, domain in _PATH_PREFIX_DOMAINS:
            if normalized.startswith(prefix) or f"/{prefix}" in normalized:
                return domain
    return "unknown"


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
