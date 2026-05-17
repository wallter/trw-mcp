"""Shared learnings-collector substrate for cross-package MCP tools (PRD-DIST-2000, cycle 749).

Extracted from c746 ``before_edit_hint.py`` to support the multi-source
pattern across all 5 cross-package consumer tools (c746-c748). The
learnings half is **always tier-independent** — free-tier operators
receive learnings via ``trw_recall`` even when the trw-distill sidecar
feature is ungated.

Each consumer tool calls ``collect_learnings(queries=[...])`` with
tool-appropriate queries:

- ``trw_before_edit_hint``: ``[file_path, basename(file_path)]``
- ``trw_before_edit_hint_batch``: per-file [path, basename] flattened
- ``trw_codebase_risk_report``: top-N risk paths + basenames
- ``trw_ordering_compare``: divergent paths (only_in_a + only_in_b)
- ``trw_cross_repo_ordering``: aggregate-level ("cross-repo divergence")

IP boundary: trw-mcp PUBLIC; trw-distill PROPRIETARY. This module
calls trw-mcp's own ``recall_learnings`` only — no trw_distill import.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_TOP_N: int = 5
MAX_QUERIES: int = 10


class LearningSummary(BaseModel):
    """Compact learning entry shown to consumers (subset of full record).

    Reused across all 5 cross-package tools — single shape contract.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    id: str
    summary: str
    impact: float = 0.0
    tags: list[str] = Field(default_factory=list)


def collect_learnings(
    queries: list[str],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> list[LearningSummary]:
    """Best-effort trw_recall over a list of queries.

    NEVER raises — empty list on any error path. Tier-independent
    (free-tier operators get value even without paid features).

    Args:
        queries: One or more recall queries. Typically file paths +
            basenames, or aggregate-level domain strings. Capped at
            ``MAX_QUERIES`` to bound retrieval cost.
        top_n: Maximum returned summaries (deduped by learning id).

    Returns:
        Up to ``top_n`` LearningSummary objects, deduped by id, in
        first-seen recall order.
    """
    if not queries:
        return []
    try:
        from trw_mcp.state.learning_injection import recall_learnings
    except Exception:
        return []
    deduplicated: set[str] = set()
    out: list[LearningSummary] = []
    for q in queries[:MAX_QUERIES]:
        if not isinstance(q, str) or not q:
            continue
        try:
            rows = recall_learnings(q, max_results=top_n)
        except Exception:
            continue
        for r in rows:
            if not isinstance(r, dict):
                continue
            rid = r.get("id")
            if not isinstance(rid, str) or rid in deduplicated:
                continue
            deduplicated.add(rid)
            summary = r.get("summary")
            if not isinstance(summary, str):
                continue
            impact = r.get("impact", 0.0)
            tags = r.get("tags", [])
            out.append(LearningSummary(
                id=rid,
                summary=summary,
                impact=float(impact) if isinstance(impact, (int, float)) else 0.0,
                tags=[str(t) for t in tags] if isinstance(tags, list) else [],
            ))
            if len(out) >= top_n:
                return out
    return out


_FILE_QUERY_KIND = Literal["file_path_basename", "explicit"]


def build_file_queries(
    file_path: str,
    *,
    kind: _FILE_QUERY_KIND = "file_path_basename",
) -> list[str]:
    """Build the standard query list for a file-targeted tool.

    Default: ``[file_path, basename(file_path)]``. Basename is added
    only when it differs from the full path. Maintained as a helper so
    the convention is centralized for all file-targeted tools.
    """
    import os

    queries = [file_path]
    basename = os.path.basename(file_path)
    if basename and basename != file_path:
        queries.append(basename)
    return queries


__all__ = [
    "DEFAULT_TOP_N",
    "LearningSummary",
    "MAX_QUERIES",
    "build_file_queries",
    "collect_learnings",
]
