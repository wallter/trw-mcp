"""A nudge rendered from a learning summary must not end mid-token.

Reported 2026-09-15 by a peer session running against the 3.0.0 build: a
`build_check` nudge came back as "...everywhere surveyed 2026-09-1" — severed
inside the date. Cosmetic in effect, but it reads as corrupted data to whoever
sees it, and a governance nudge that looks like a data error is a nudge that
gets ignored.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools._ceremony_status_helpers import (
    _NUDGE_FALLBACK_CHARS,
    _deterministic_fallback_text,
)

pytestmark = pytest.mark.unit

#: The exact string from the report, which is longer than the budget.
REPORTED = "Sub-agent addressability is strictly intra-harness everywhere surveyed 2026-09-15 across nine harnesses"


def test_the_reported_summary_no_longer_ends_inside_a_token() -> None:
    out = _deterministic_fallback_text({"summary": REPORTED})
    assert out.endswith("…"), "an elided nudge must say it was elided"
    assert "2026-09-1…" not in out, "the date must not be severed mid-token"
    body = out.rstrip("…")
    assert REPORTED.startswith(body), "the kept text must be a true prefix of the summary"
    assert body == body.rstrip(), "no trailing whitespace before the ellipsis"


def test_a_summary_within_budget_is_returned_whole_and_unmarked() -> None:
    """The control: without this, an implementation that always elided would pass above."""
    short = "Keep the run pinned before calling deliver"
    assert len(short) <= _NUDGE_FALLBACK_CHARS
    assert _deterministic_fallback_text({"summary": short}) == short


def test_an_explicit_nudge_line_is_never_elided() -> None:
    """`nudge_line` is purpose-written to fit; only the summary fallback is trimmed."""
    line = "Mutation-check the harness too, not just shipped code."
    assert _deterministic_fallback_text({"summary": REPORTED, "nudge_line": line}) == line


@pytest.mark.parametrize(
    ("summary", "why"),
    [
        ("x" * 200, "a single token has no boundary to cut at, but must still be marked"),
        ("word " * 100, "many short tokens"),
        ("", "empty summary yields empty text, not an ellipsis"),
    ],
)
def test_the_budget_is_never_exceeded(summary: str, why: str) -> None:
    out = _deterministic_fallback_text({"summary": summary})
    assert len(out) <= _NUDGE_FALLBACK_CHARS, why
