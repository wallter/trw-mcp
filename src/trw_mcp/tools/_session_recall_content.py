"""Pure focused startup body carry/projection; no retrieval or persistence."""

from __future__ import annotations

from trw_mcp.models.config._defaults import COMPACT_TAGS_CAP

_DETAIL_CARRY = "_session_focused_detail"
_DETAIL_ALLOWANCE = 2048  # Unicode characters of added body, not tokens or payload bytes.
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


def carry_focused_content(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep prior compact scoring inputs, carrying body separately on each row."""
    carried = []
    for source in rows:
        row = {key: source[key] for key in _COMPACT_CANDIDATE_FIELDS if key in source}
        tags = row.get("tags")
        if isinstance(tags, list):
            row["tags"] = tags[:COMPACT_TAGS_CAP]
        body = source.get("detail")
        row[_DETAIL_CARRY] = body if isinstance(body, str) else ""
        carried.append(row)
    return carried


def project_focused_content(rows: list[dict[str, object]], compact_fields: tuple[str, ...]) -> list[dict[str, object]]:
    """Project selected focused rows with fair bounded body shares; leave baseline alone."""
    bodies = [str(row[_DETAIL_CARRY]) for row in rows if row.get(_DETAIL_CARRY)]
    if bodies:
        share, remainder = divmod(_DETAIL_ALLOWANCE, len(bodies))
        allocations = [min(len(body), share + (index < remainder)) for index, body in enumerate(bodies)]
        unused = _DETAIL_ALLOWANCE - sum(allocations)
        for index, body in enumerate(bodies):
            extra = min(unused, len(body) - allocations[index])
            allocations[index] += extra
            unused -= extra
    else:
        allocations = []
    result = []
    body_index = 0
    for source in rows:
        if _DETAIL_CARRY not in source:
            result.append(source)
            continue
        row = {key: source[key] for key in compact_fields if key in source}
        body = str(source[_DETAIL_CARRY])
        if body:
            allowance = allocations[body_index]
            body_index += 1
            row["detail"] = body[:allowance]
            row["detail_truncated"] = allowance < len(body)
        result.append(row)
    return result
