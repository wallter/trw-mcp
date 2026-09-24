"""Recall presentation: ranked rows become one-line stubs inside a byte budget.

PRD-CORE-294 FR01/FR02. Ranking is finished before this module runs; it only
decides how much of the ranked list the caller pays for. A stub is
``{id, claim, anchor?}`` and stubs are added in rank order until the next one
would take the WHOLE rendered response over the budget. The first stub is
kept by cutting long strings (query echo, advisories, its own claim) instead,
so a non-empty recall never reads as empty. Full rows are one
``trw_recall(ids=[...])`` call away.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

#: FR01: longest claim a stub carries, including the trailing ellipsis.
CLAIM_MAX_CHARS = 160
#: FR01 / NFR02: default ``trw_recall`` response ceiling, in rendered JSON bytes.
RECALL_BYTE_BUDGET = 3_000
#: FR02 / NFR02: session_start learning block ceiling, and its stub count.
SESSION_BYTE_BUDGET = 1_500
SESSION_MAX_STUBS = 3
#: Shortest a cut envelope string gets before a stub is dropped instead.
_MIN_FIELD_CHARS = 16


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def claim(summary: object) -> str:
    """Collapse *summary* to one line and cut it at ``CLAIM_MAX_CHARS``."""
    return _cut(" ".join(str(summary or "").split()), CLAIM_MAX_CHARS)


def _anchor(row: Mapping[str, object], query_tokens: Sequence[str]) -> str:
    """The first anchor whose path matches a query token, else the first anchor."""
    anchors = row.get("anchors")
    rendered = []
    for anchor in anchors if isinstance(anchors, list) else []:
        if isinstance(anchor, Mapping) and anchor.get("file"):
            symbol = anchor.get("symbol_name")
            rendered.append(f"{anchor['file']}:{symbol}" if symbol else str(anchor["file"]))
    matching = (a for a in rendered if any(token in a.split(":")[0].lower() for token in query_tokens))
    return next(matching, rendered[0] if rendered else "")


def stub(row: Mapping[str, object], query_tokens: Sequence[str] = ()) -> dict[str, object]:
    """Render one ranked row as ``{id, claim, anchor?}``, every field bounded."""
    rendered: dict[str, object] = {"id": str(row.get("id", "")), "claim": claim(row.get("summary"))}
    anchor = _anchor(row, query_tokens)
    if anchor:
        rendered["anchor"] = _cut(anchor, CLAIM_MAX_CHARS)
    return rendered


def _size(payload: Mapping[str, object]) -> int:
    # ASCII-escaped JSON is one byte per character and never shorter than the
    # UTF-8 encoding of the same payload, so the bound holds for either wire form.
    return len(json.dumps(payload, default=str).encode("ascii"))


def _cut_longest_string(payload: dict[str, object]) -> bool:
    """Halve the longest string in *payload*, at any depth; ``id`` values are never cut.

    Returns False once every string is at ``_MIN_FIELD_CHARS`` or shorter.
    """
    longest: tuple[dict[str, object] | list[object], str | int, str] | None = None
    stack: list[dict[str, object] | list[object]] = [payload]
    while stack:
        node = stack.pop()
        items = node.items() if isinstance(node, dict) else enumerate(node)
        for key, value in items:
            if isinstance(value, (dict, list)):
                stack.append(value)
            elif (
                isinstance(value, str)
                and key != "id"
                and len(value) > max(_MIN_FIELD_CHARS, len(longest[2]) if longest else 0)
            ):
                longest = (node, key, value)
    if longest is None:
        return False
    node, key, value = longest
    node[key] = _cut(value, max(_MIN_FIELD_CHARS, len(value) // 2))  # type: ignore[index]
    return True


def present(
    envelope: dict[str, object],
    rows: Sequence[Mapping[str, object]],
    *,
    query_tokens: Sequence[str] = (),
    byte_budget: int = RECALL_BYTE_BUDGET,
    max_stubs: int | None = None,
) -> list[dict[str, object]]:
    """Fill ``envelope["learnings"]`` with stubs so the WHOLE envelope renders within *byte_budget*.

    Callers attach every other key first: the budget covers them. Stubs are
    added in rank order and the first one that does not fit ends the list;
    when even the first does not fit, the longest strings in the envelope (the
    query echo, advisories, the stub's own claim and anchor) are cut until it
    does. ``omitted`` is set only when rows were left out.
    """
    stubs: list[dict[str, object]] = []
    envelope["learnings"] = stubs
    # Sized with the widest possible ``omitted`` so adding it later cannot overflow.
    bounded = {**envelope, "omitted": len(rows)}

    def over() -> bool:
        bounded["learnings"] = stubs
        return _size(bounded) > byte_budget

    for row in rows if max_stubs is None else rows[:max_stubs]:
        stubs.append(stub(row, query_tokens))
        if over() and len(stubs) > 1:
            stubs.pop()
            break
    while over() and _cut_longest_string(bounded):
        pass
    if over():
        stubs.clear()  # trw:intentional an id alone too long for the budget is dropped, never sent over it
    envelope.update({key: value for key, value in bounded.items() if key not in ("learnings", "omitted")})
    omitted = len(rows) - len(stubs)
    if omitted > 0:
        envelope["omitted"] = omitted
    return stubs


__all__ = [
    "CLAIM_MAX_CHARS",
    "RECALL_BYTE_BUDGET",
    "SESSION_BYTE_BUDGET",
    "SESSION_MAX_STUBS",
    "claim",
    "present",
    "stub",
]
