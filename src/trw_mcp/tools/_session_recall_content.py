"""Session-start rows: rank on compact scoring inputs, return full rows only on request.

PRD-CORE-294 FR02. Ranking reads the same compact candidate fields it always
has, so moving the response to stubs changes presentation, not rank (NFR01).
Each compact row carries its full source row under a private key, which
``full_rows`` resolves for ``verbose=True`` without a second lookup.
"""

from __future__ import annotations

from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP
from trw_mcp.tools._recall_projection import strip_internal_response_fields

_FULL_ROW = "_session_full_row"
_COMPACT_CANDIDATE_FIELDS = (
    "id",
    "summary",
    "tags",
    "impact",
    "status",
    "assertions",
    "anchors",
    "verification_status",
    "verification_checked_at",
    "anchor_validity",
)


def carry_full_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project each row to its compact scoring inputs, carrying the full row beside them."""
    carried = []
    for source in rows:
        row = {key: source[key] for key in _COMPACT_CANDIDATE_FIELDS if key in source}
        tags = row.get("tags")
        if isinstance(tags, list):
            row["tags"] = tags[:COMPACT_TAGS_CAP]
        row[_FULL_ROW] = source
        carried.append(row)
    return carried


def full_rows(ranked: list[dict[str, object]], internal_fields: frozenset[str]) -> list[dict[str, object]]:
    """The full rows behind *ranked*, in rank order, with the ranking pass's field values on top.

    Internal scoring fields are stripped exactly as ``trw_recall(ids=...)`` strips them.
    """
    merged = []
    for row in ranked:
        source = row.get(_FULL_ROW)
        compact = {key: value for key, value in row.items() if key != _FULL_ROW}
        merged.append({**source, **compact} if isinstance(source, dict) else compact)
    return strip_internal_response_fields(merged, internal_fields)
