"""Context-aware learning injection for subagent prompts.

PRD-CORE-075: Selects and formats learnings relevant to a subagent's
task description and file ownership for injection into spawn prompts.

The pipeline:
  1. ``infer_domain_tags`` extracts domain hints from file paths.
  2. ``select_learnings_for_task`` queries recall, filters by tag overlap,
     and ranks by combined relevance (tag overlap + impact score).
  3. ``format_learning_injection`` renders a markdown section suitable for
     prepending to a subagent's spawn prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

# Domain tag mapping: path component -> domain tags.
# Stems are matched case-insensitively against each path component.
#
# Two coverage groups:
# - TRW-internal directories (backend/routers/trw-mcp/etc.) — original set.
# - External eval-corpus roots (sphinx/pylint/astropy/etc.) — added 2026-04-27
#   after the iter-22 root-cause investigation found that empty tag sets on
#   external repos collapsed the relevance ranker to pure-impact, surfacing
#   off-domain TRW-framework learnings against SWE-bench tasks. See
#   docs/research/trw-distill/ITER-22-NAIVE-INJECTION-INVESTIGATION-2026-04-27.md.
_PATH_DOMAIN_MAP: dict[str, set[str]] = {
    "backend": {"backend", "fastapi", "api"},
    "routers": {"api", "endpoints"},
    "admin": {"admin", "auth"},
    "auth": {"auth", "security", "jwt"},
    "models": {"models", "database", "orm"},
    "database": {"database", "orm", "sqlalchemy"},
    "alembic": {"migration", "database", "alembic"},
    "platform": {"frontend", "nextjs", "react"},
    "components": {"components", "ui", "react"},
    "dashboard": {"dashboard", "ui"},
    "marketing": {"marketing", "frontend"},
    "trw-mcp": {"mcp", "tools", "framework"},
    "trw_mcp": {"mcp", "tools", "framework"},
    "trw-memory": {"memory", "retrieval", "storage"},
    "trw_memory": {"memory", "retrieval", "storage"},
    "state": {"state", "persistence"},
    "tools": {"tools", "mcp"},
    "scoring": {"scoring", "utility"},
    "retrieval": {"retrieval", "search", "embeddings"},
    "skills": {"skills", "agents"},
    "agents": {"agents", "orchestration"},
    "config": {"config", "settings"},
    "tests": {"testing"},
    "middleware": {"middleware", "api"},
    "services": {"services"},
    "security": {"security", "auth"},
    "ui": {"ui", "components", "frontend"},
    # SWE-bench Verified repo roots (12 repos).
    "sphinx": {"docs", "sphinx", "documentation"},
    "django": {"django", "web", "orm"},
    "astropy": {"astropy", "astronomy", "scientific"},
    "sympy": {"sympy", "symbolic", "math"},
    "pytest": {"pytest", "testing"},
    "pylint": {"pylint", "linting", "static-analysis"},
    "matplotlib": {"matplotlib", "plotting", "visualization"},
    "seaborn": {"seaborn", "plotting", "visualization"},
    "flask": {"flask", "web", "api"},
    "requests": {"requests", "http", "networking"},
    "xarray": {"xarray", "scientific", "arrays"},
    "scikit-learn": {"scikit-learn", "ml", "scientific"},
    "sklearn": {"scikit-learn", "ml", "scientific"},
    # FeatureBench repo roots (frequent ones).
    "pandas": {"pandas", "dataframes", "scientific"},
    "transformers": {"transformers", "ml", "huggingface"},
    "mlflow": {"mlflow", "ml", "tracking"},
    "numpy": {"numpy", "arrays", "scientific"},
    "pytorch": {"pytorch", "ml", "deep-learning"},
    "torch": {"pytorch", "ml", "deep-learning"},
    "lightning": {"lightning", "ml", "deep-learning"},
    "fastapi": {"fastapi", "web", "api"},
}


def infer_domain_tags(file_paths: list[str]) -> set[str]:
    """Extract domain hints from file paths.

    PRD-CORE-075-FR04: Maps path components to relevant domain tags
    for learning retrieval matching.

    Args:
        file_paths: List of file paths (relative or absolute).

    Returns:
        Set of inferred domain tags.
    """
    tags: set[str] = set()
    for path_str in file_paths:
        # Normalize: handle both / and \\ separators
        parts = re.split(r"[/\\]", path_str.lower())
        for part in parts:
            # Strip file extension for stem matching
            stem = part.rsplit(".", 1)[0] if "." in part else part
            if stem in _PATH_DOMAIN_MAP:
                tags.update(_PATH_DOMAIN_MAP[stem])
    return tags


def _resolve_trw_dir() -> Path:
    """Lazy-resolve the .trw directory to avoid circular imports."""
    from trw_mcp.state._paths import resolve_trw_dir

    return resolve_trw_dir()


def recall_learnings(
    query: str,
    *,
    tags: list[str] | None = None,
    min_impact: float = 0.0,
    max_results: int = 25,
    status: str | None = None,
) -> list[dict[str, object]]:
    """Thin wrapper around memory_adapter.recall_learnings.

    Resolves ``trw_dir`` automatically so callers in this module
    don't need to pass it explicitly.
    """
    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall

    trw_dir = _resolve_trw_dir()
    return adapter_recall(
        trw_dir,
        query=query,
        tags=tags,
        min_impact=min_impact,
        max_results=max_results,
        status=status,
    )


def select_learnings_for_task(
    task_description: str,
    file_paths: list[str],
    tags: list[str] | None = None,
    *,
    max_results: int | None = None,
    min_impact: float | None = None,
) -> list[dict[str, object]]:
    """Select learnings relevant to a subagent's task.

    PRD-CORE-075-FR01: Queries recall with the task description,
    filters by tag overlap with inferred domain tags, ranks by
    combined relevance (semantic + tag overlap + impact).

    When *max_results* or *min_impact* are ``None``, values are read
    from the ``agent_learning_max`` / ``agent_learning_min_impact``
    config fields (FR-05: Parameter Default Alignment).

    Args:
        task_description: Natural language description of the task.
        file_paths: Files the subagent will work on.
        tags: Additional explicit tags to filter by.
        max_results: Maximum number of learnings to return (None -> config).
        min_impact: Minimum impact score threshold (None -> config).

    Returns:
        List of learning entry dicts, ranked by relevance.
    """
    from trw_mcp.models.config import get_config

    cfg = get_config()

    # Resolve sentinel defaults from config (FR-05, Parameter Default Alignment)
    effective_max = max_results if max_results is not None else cfg.agent_learning_max
    effective_min = min_impact if min_impact is not None else cfg.agent_learning_min_impact

    # Infer domain tags from file paths (FR-04)
    domain_tags = infer_domain_tags(file_paths)
    if tags:
        domain_tags.update(tags)

    # Query recall with domain tags
    results: list[dict[str, object]] = []
    try:
        all_tags = list(domain_tags) if domain_tags else None
        results = recall_learnings(
            query=task_description,
            tags=all_tags,
            min_impact=effective_min,
            max_results=effective_max * 3,  # Over-fetch for re-ranking
            status="active",
        )
    except Exception:  # justified: fail-open, recall failure degrades to empty injection
        logger.debug(
            "learning_injection_recall_failed",
            task=task_description[:80],
        )

    if not results:
        # Fallback: try query-only search without tag filter
        try:
            results = recall_learnings(
                query=task_description,
                min_impact=effective_min,
                max_results=effective_max * 2,
                status="active",
            )
        except Exception:  # justified: fail-open, fallback recall failure returns empty list
            logger.debug(
                "learning_injection_fallback_failed",
                task=task_description[:80],
            )
            return []

    # Rank by combined score: tag overlap (60%) + impact (40%)
    scored: list[tuple[float, dict[str, object]]] = []
    for entry in results:
        entry_tags_raw = entry.get("tags", [])
        tag_list: list[str] = [str(t) for t in entry_tags_raw] if isinstance(entry_tags_raw, list) else []

        # Tag overlap score (0-1)
        if domain_tags and tag_list:
            overlap = len(domain_tags.intersection(set(tag_list)))
            tag_score = min(overlap / max(len(domain_tags), 1), 1.0)
        else:
            tag_score = 0.0

        # Impact score (already 0-1)
        impact = float(str(entry.get("impact", 0.0)))

        # Combined score: 60% tag relevance + 40% impact
        combined = 0.6 * tag_score + 0.4 * impact
        scored.append((combined, entry))

    # Sort by combined score descending
    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [entry for _, entry in scored[:effective_max]]

    logger.debug(
        "learning_injection_selected",
        task=task_description[:80],
        candidates=len(results),
        selected=len(selected),
    )

    return selected


def format_learning_injection(learnings: list[dict[str, object]]) -> str:
    """Format selected learnings as a markdown section for prompt injection.

    PRD-CORE-075-FR02: Renders a clearly delimited section with IDs,
    summaries, impact scores, and tags. Designed for prepending to
    subagent spawn prompts.

    Args:
        learnings: List of learning entry dicts.

    Returns:
        Markdown-formatted string, or empty string if no learnings.
    """
    if not learnings:
        return ""

    lines: list[str] = [
        "## Task-Relevant Learnings (auto-injected)",
        "",
        "The following learnings from prior sessions are relevant to your "
        "current task. Treat them as high-priority constraints.",
        "",
    ]

    for entry in learnings:
        entry_id = str(entry.get("id", "unknown"))
        summary = str(entry.get("summary", ""))
        impact = float(str(entry.get("impact", 0.0)))
        tags_raw = entry.get("tags", [])
        tag_list: list[str] = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
        # Truncate to first 5 tags
        tag_str = ", ".join(tag_list[:5])

        lines.append(f"- **[{entry_id}]** {summary} (impact: {impact:.1f}, tags: {tag_str})")

    lines.append("")
    return "\n".join(lines)
