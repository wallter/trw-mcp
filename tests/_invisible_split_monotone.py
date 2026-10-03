"""Fuzz corpus and exposure measure for the PII-INVISIBLE-SPLIT monotone property (vendored for trw-mcp).

A copy of trw-memory/tests/test_pii_invisible_split_monotone.py's helpers, so this package's tests do not reach
into a sibling package's test tree (which the public trw-mcp mirror does not ship). Keep the two in sync.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from trw_memory.security._scan_normalize import _PLACEHOLDER_RE, _is_format, _rebuild, _union_ranges

INVISIBLE = ["\u200b", "\u00ad", "\u202e", "\u200d", "\u2060", "\ufeff", "\u034f", "\u3164", "\ufe0f"]
SEPARATORS = [" ", " ", "\t", "\n", "\u00a0", ""]
PIECES = [
    "AKIAIOSFODNN7EXAMPLE",
    "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2,
    "sk_live_" + "Z9y8X7w6V5u4T3s2R1q0P9o8",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl",
    "123-45-6789",
    "123 45 6789",
    "jane.doe@example.com",
    "+1 (415) 555-0123",
    'PASSWORD="first SensitiveSuffixABC"',
    '{"password": "hunter2hunter2", "user": "x"}',
    "API_TOKEN=abcdEFGH1234ijkl",
    "Authorization: Bearer abcdef123456789",
    "Bearer abcdef123456",
    "Bearer\tabcdef123456",
    "Token\n\tzyxwvu987654",
    'PASSWORD="Bearer x y"',
    '"Bearer x"',
    "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEA\n-----END PRIVATE KEY-----",
    "postgres://admin:s3cretPass@db.internal",
    "?token=abc123def456ghi",
    # decoys and plain prose
    "Bearer",
    "PASSWORD=",
    '"',
    "token",
    "José",
    "東京",
    "x",
    "abcde",
    "x y",
]


def _case(rng: random.Random) -> str:
    text = ""
    for _ in range(rng.randint(2, 7)):
        text += rng.choice(PIECES) + rng.choice(SEPARATORS)
    chars = list(text)
    for _ in range(rng.randint(1, 3)):
        chars.insert(rng.randint(0, len(chars)), rng.choice(INVISIBLE))
    return "".join(chars)


def _alignments(text: str, out: str) -> tuple[set[int], set[int]] | None:
    """Independent of production code: the characters of *text* that *out* masks under the earliest and under
    the latest alignment of its literal stretches, or None when *out* is not a substitution of *text* at all.
    No fallback: a measure that reported "everything masked" on misalignment would hide exactly the bug."""
    parts, holders = _PLACEHOLDER_RE.split(out), _PLACEHOLDER_RE.findall(out)
    if not text.startswith(parts[0]) or not text.endswith(parts[-1]):
        return None
    last = len(parts) - 1
    early, pos = [0], len(parts[0])  # the first literal is a prefix, the last a suffix: both anchored
    for i in range(1, len(parts)):
        at = len(text) - len(parts[i]) if i == last else text.find(parts[i], pos)
        if at < pos:
            return None
        early.append(at)
        pos = at + len(parts[i])
    late, end = [0] * len(parts), len(text)
    for i in range(last, -1, -1):
        at = 0 if i == 0 else (len(text) - len(parts[i]) if i == last else text.rfind(parts[i], 0, end))
        if at < 0 or at + len(parts[i]) > end:
            return None
        late[i], end = at, at

    def covered(starts: list[int]) -> set[int]:
        return {
            k
            for i in range(len(holders))
            for k in range(starts[i] + len(parts[i]), starts[i + 1])
            if not _is_format(text[k])
        }

    return covered(early), covered(late)


def _exposed(text: str, as_written: Callable[[str], str], public: Callable[[str], str]) -> set[int]:
    """Characters the as-written output may mask (measured independently, see ``_alignments``) that the public
    output leaves visible. The public side uses the ranges production built its output from, after checking
    they reproduce that output exactly, so every character outside them is literally in the output."""
    written = _alignments(text, as_written(text))
    assert written is not None, ("as-written output is not a substitution of the input", text, as_written(text))
    ranges = _union_ranges(text, as_written)
    assert public(text) == _rebuild(text, ranges), ("public output is not built from its ranges", text)
    hidden = {k for start, end, _ in ranges for k in range(start, end)}
    return (written[0] | written[1]) - hidden
