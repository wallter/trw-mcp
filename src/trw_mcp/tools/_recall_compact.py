"""Ultra-compact recall rendering — the summary-only shape for tiny token budgets.

Responsibility: turn ranked learnings into the ``ultra_compact`` recall result
and trim each summary to a small token budget while keeping a readable suffix.
Interface: ``build_ultra_compact_recall_result``. Split out of ``_recall_impl``
(350 effective-LOC gate) with no behaviour change.
"""

from __future__ import annotations

from trw_mcp.models.typed_dicts._tools import RecallResultDict


def build_ultra_compact_recall_result(ranked_learnings: list[dict[str, object]]) -> RecallResultDict:
    """Build the ultra-compact recall response payload."""
    return {
        "learnings": [
            {
                "id": str(entry.get("id", "")),
                "summary": _truncate_ultra_compact_summary(str(entry.get("summary", ""))),
                **(
                    {"verification_evidence": entry["verification_evidence"]}
                    if "verification_evidence" in entry
                    else {}
                ),
            }
            for entry in ranked_learnings
        ],
        "count": len(ranked_learnings),
        "ceremony_hint": "Call trw_session_start() first to load prior learnings and active run state.",
    }


def _truncate_ultra_compact_summary(summary: str, token_limit: int = 32) -> str:
    """Trim summaries to a small token budget while preserving a readable suffix."""
    from trw_memory.retrieval.token_budget import estimate_tokens

    normalized = " ".join(summary.split())
    if estimate_tokens(normalized) <= token_limit:
        return normalized

    words = normalized.split()
    while words:
        candidate = " ".join(words) + "…"
        if estimate_tokens(candidate) <= token_limit:
            return candidate
        words.pop()

    return "…"
