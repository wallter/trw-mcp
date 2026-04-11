"""Audit-finding recurrence helpers for consolidation.

PRD-QUAL-056-FR10 keeps the promotion logic separate from the main
consolidation cycle so ``_cycle.py`` stays focused on orchestration.
"""

from __future__ import annotations

import re

from trw_mcp.state._helpers import truncate_nudge_line

# Known audit-finding category tags.
_AUDIT_FINDING_CATEGORIES: frozenset[str] = frozenset({
    "spec_gap",
    "impl_gap",
    "test_gap",
    "integration_gap",
    "traceability_gap",
})

_PRD_TAG_PREFIX = "PRD-"
_AUDIT_PATTERN_STOPWORDS: frozenset[str] = frozenset({
    "a",
    "an",
    "and",
    "another",
    "audit",
    "audits",
    "detected",
    "finding",
    "findings",
    "for",
    "from",
    "in",
    "into",
    "is",
    "missing",
    "not",
    "of",
    "on",
    "same",
    "surfaced",
    "the",
    "this",
    "to",
    "with",
})
_AUDIT_PATTERN_SYNONYMS: dict[str, str] = {
    "callsite": "callsite",
    "callsites": "callsite",
    "hook": "wiring",
    "hookup": "wiring",
    "hookups": "wiring",
    "implementation": "implementation",
    "implementations": "implementation",
    "integrate": "integration",
    "integrated": "integration",
    "integration": "integration",
    "matrixes": "matrix",
    "matrices": "matrix",
    "miss": "missing",
    "missed": "missing",
    "regressions": "regression",
    "tests": "test",
    "trace": "traceability",
    "traces": "traceability",
    "traceability": "traceability",
    "wired": "wiring",
    "wire": "wiring",
    "wireup": "wiring",
    "wiring": "wiring",
}
_AUDIT_PATTERN_PREVENTION_STRATEGIES: dict[str, str] = {
    "spec_gap": "Require FR-by-FR spec reconciliation before review sign-off.",
    "impl_gap": "Verify the production call path and integration wiring before closing remediation.",
    "test_gap": "Add requirement-linked regression coverage before marking the fix complete.",
    "integration_gap": "Exercise end-to-end integration points, not just isolated units, before delivery.",
    "traceability_gap": "Update traceability artifacts alongside code changes before delivery sign-off.",
}


def _normalize_audit_pattern_token(token: str) -> str:
    """Normalize a token from an audit summary for recurrence grouping."""
    normalized = _AUDIT_PATTERN_SYNONYMS.get(token, token)
    if normalized.endswith("ies") and len(normalized) > 4:
        normalized = normalized[:-3] + "y"
    elif normalized.endswith("s") and len(normalized) > 4 and not normalized.endswith("ss"):
        normalized = normalized[:-1]
    return normalized


def _normalize_audit_pattern(summary: str) -> str:
    """Reduce a free-text audit summary to a stable recurrence key."""
    raw_tokens = re.findall(r"[a-z0-9]+", summary.lower())
    normalized_tokens: list[str] = []
    for token in raw_tokens:
        if token.startswith("prd"):
            continue
        normalized = _normalize_audit_pattern_token(token)
        if normalized in _AUDIT_PATTERN_STOPWORDS or len(normalized) <= 2:
            continue
        normalized_tokens.append(normalized)

    unique_tokens = sorted(dict.fromkeys(normalized_tokens))
    if unique_tokens:
        return " ".join(unique_tokens[:6])

    fallback = re.sub(r"\s+", " ", summary.strip().lower())
    return fallback[:80]


def _build_audit_pattern_summary(sample_summaries: list[str], normalized_pattern: str) -> str:
    """Build a human-readable pattern summary from sample summaries."""
    if sample_summaries:
        return sample_summaries[0]
    return normalized_pattern.replace("_", " ").strip()


def _build_audit_synthesized_summary(
    category: str,
    pattern_summary: str,
    prd_count: int,
    prevention_strategy: str,
) -> str:
    """Build the FR10-required synthesized summary."""
    category_name = category.replace("_", " ")
    return (
        f"Recurring {category_name} pattern: {pattern_summary}. "
        f"Observed across {prd_count} PRDs. Prevention: {prevention_strategy}"
    )


def _accumulate_audit_entry(
    entry: dict[str, object],
    pattern_data: dict[tuple[str, str], dict[str, list[str]]],
) -> None:
    """Accumulate a single entry's audit-finding data into *pattern_data*."""
    raw_tags = entry.get("tags")
    if not raw_tags or not isinstance(raw_tags, list):
        return

    tags: list[str] = [str(t) for t in raw_tags]
    if "audit-finding" not in tags:
        return

    categories: list[str] = []
    prd_ids: list[str] = []
    for tag in tags:
        if tag in _AUDIT_FINDING_CATEGORIES:
            categories.append(tag)
        elif tag.startswith(_PRD_TAG_PREFIX):
            prd_ids.append(tag)

    summary = str(entry.get("summary", ""))
    normalized_pattern = _normalize_audit_pattern(summary)

    for category in categories:
        key = (category, normalized_pattern)
        if key not in pattern_data:
            pattern_data[key] = {}
        for prd_id in prd_ids:
            if prd_id not in pattern_data[key]:
                pattern_data[key][prd_id] = []
            pattern_data[key][prd_id].append(summary)


def detect_audit_finding_recurrence(
    entries: list[dict[str, object]],
    threshold: int = 3,
) -> list[dict[str, object]]:
    """Detect audit-finding learnings that recur across distinct PRDs."""
    pattern_data: dict[tuple[str, str], dict[str, list[str]]] = {}

    for entry in entries:
        _accumulate_audit_entry(entry, pattern_data)

    candidates: list[dict[str, object]] = []
    for (category, normalized_pattern), prd_map in sorted(pattern_data.items()):
        distinct_prds = len(prd_map)
        if distinct_prds < threshold:
            continue

        prd_ids_sorted = sorted(prd_map.keys())
        sample_summaries: list[str] = []
        for prd_id in prd_ids_sorted[:3]:
            summaries = prd_map[prd_id]
            if summaries:
                sample_summaries.append(summaries[0])

        pattern_summary = _build_audit_pattern_summary(sample_summaries, normalized_pattern)
        prevention_strategy = _AUDIT_PATTERN_PREVENTION_STRATEGIES.get(
            category,
            "Add an explicit prevention checklist item before delivery sign-off.",
        )
        synthesized_summary = _build_audit_synthesized_summary(
            category,
            pattern_summary,
            distinct_prds,
            prevention_strategy,
        )
        nudge_line = truncate_nudge_line(
            f"Recurring {category.replace('_', ' ')}: {pattern_summary}",
        )

        candidates.append({
            "category": category,
            "normalized_pattern": normalized_pattern,
            "pattern_summary": pattern_summary,
            "prd_count": distinct_prds,
            "prd_ids": prd_ids_sorted,
            "sample_summaries": sample_summaries,
            "synthesized_summary": synthesized_summary,
            "prevention_strategy": prevention_strategy,
            "nudge_line": nudge_line,
        })

    return candidates
