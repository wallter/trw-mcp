"""Tests for _provenance.py — canonical provenance comment/frontmatter renderer."""

from __future__ import annotations

import re

from trw_mcp.channels._provenance import (
    now_utc_iso8601,
)

# ---------------------------------------------------------------------------
# now_utc_iso8601
# ---------------------------------------------------------------------------


def test_now_utc_iso8601_format() -> None:
    ts = now_utc_iso8601()
    # Should be YYYY-MM-DDTHH:MM:SS.mmmZ
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", ts), ts


def test_now_utc_iso8601_ends_with_z() -> None:
    ts = now_utc_iso8601()
    assert ts.endswith("Z")
