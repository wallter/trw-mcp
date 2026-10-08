"""Bounded-time matching of ``objective.paths`` globs against repository paths (AHR rc.2, R-REC-3).

Belongs to the :mod:`trw_mcp.handoff` package (``trw-mcp handoff check`` scope warning). Patterns are
record content, so untrusted: a regex built from them can backtrack exponentially (``*a*a*...b``), so
this matcher never builds one. Semantics, per ``/``-separated segment:

- ``*`` matches any run of characters inside one segment, ``?`` exactly one character;
- a ``**`` segment matches zero or more whole segments;
- a pattern with no wildcard also covers everything under it (``docs`` covers ``docs/a.md``);
- every other character, newline included, matches only itself.

Cost is O(segments^2) segment comparisons, each O(len(pattern segment) * len(path segment)).
"""

from __future__ import annotations

from functools import cache

__all__ = ["covers"]

_WILD = frozenset("*?")


def _segment(pattern: str, text: str) -> bool:
    """Wildcard match of one segment with the classic greedy two-pointer walk (no backtracking blow-up)."""
    p = t = 0
    star = mark = -1
    while t < len(text):
        if p < len(pattern) and (pattern[p] == "?" or pattern[p] == text[t]):
            p += 1
            t += 1
        elif p < len(pattern) and pattern[p] == "*":
            star, mark = p, t
            p += 1
        elif star >= 0:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def _full(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    @cache
    def match(i: int, j: int) -> bool:
        if i == len(pattern):
            return j == len(path)
        if pattern[i] == "**":
            return any(match(i + 1, k) for k in range(j, len(path) + 1))
        return j < len(path) and _segment(pattern[i], path[j]) and match(i + 1, j + 1)

    return match(0, 0)


def covers(pattern: str, path: str) -> bool:
    """True when ``pattern`` (an ``objective.paths`` entry) covers the repository-relative ``path``."""
    pat = tuple(pattern.rstrip("/").split("/"))
    parts = tuple(path.rstrip("/").split("/"))
    if not any(_WILD & set(pattern)):
        return parts[: len(pat)] == pat  # a literal path covers itself and everything under it
    return _full(pat, parts)
