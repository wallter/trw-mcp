"""Domain inference and recall compatibility helpers.

The portable subagent workflow performs optional ``trw_recall`` through the
active harness. This module retains the shared path-to-domain mapper used by
live ceremony nudges and the recall shim used by the learning collector.
"""

from __future__ import annotations

import re
from pathlib import Path

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
    # Additional common benchmark-corpus repo roots (frequent ones).
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
) -> list[dict[str, object]]:
    """Resolve ``trw_dir`` for the live learning collector and recall.

    This is the one intentional file-level DRY shim permitted by PRD-FIX-085
    FR05: it adds directory resolution, not parameter drift. The collector
    intentionally uses the adapter's unfiltered default.
    """
    trw_dir = _resolve_trw_dir()
    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall

    return adapter_recall(
        trw_dir,
        query=query,
        tags=tags,
        min_impact=min_impact,
        max_results=max_results,
    )
