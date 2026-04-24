"""PRD-QUAL-075 FR01/FR02: FRAMEWORK.md Opus 4.7 verification.

Guards:
  - Opus 4.7 references present, no Opus 4.6 references.
  - OPUS-4-7-BEST-PRACTICES callout present (near the top execution-summary).
  - Version date updated.
  - Bundled copy at ``trw-mcp/src/trw_mcp/data/framework.md`` matches the
    canonical repo copy at ``.trw/frameworks/FRAMEWORK.md`` byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CANONICAL = _REPO_ROOT / ".trw" / "frameworks" / "FRAMEWORK.md"
_BUNDLED = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp" / "data" / "framework.md"


def _canonical_text() -> str:
    return _CANONICAL.read_text(encoding="utf-8")


def test_opus_version() -> None:
    """FR01: at least three references to Opus 4.7; zero to Opus 4.6."""
    text = _canonical_text()
    assert text.count("Opus 4.7") >= 3, "FRAMEWORK.md should mention Opus 4.7 at least 3 times"
    assert "Opus 4.6" not in text, "FRAMEWORK.md still references Opus 4.6"


def test_version_date_updated() -> None:
    """FR01: the 'Version date: ' line must not reference a pre-4.7 date."""
    text = _canonical_text()
    # Expect format 'Version date: YYYY-MM-DD | Model: Opus 4.7'
    assert "Version date:" in text
    assert "| Model: Opus 4.7" in text


def test_best_practices_link() -> None:
    """FR02: callout references OPUS-4-7-BEST-PRACTICES.md."""
    text = _canonical_text()
    assert "OPUS-4-7-BEST-PRACTICES" in text, "FRAMEWORK.md missing Opus 4.7 best practices callout"


def test_callout_within_execution_summary() -> None:
    """FR02: the caveats callout lives near the top (before the execution-summary body).

    The callout line starts with 'Opus 4.7 caveats' and must appear within the
    first ~20 lines of the document, so agents encounter it before deep content.
    """
    lines = _canonical_text().splitlines()
    head = "\n".join(lines[:20])
    assert "Opus 4.7 caveats" in head, "Opus 4.7 caveats callout must appear near the top of FRAMEWORK.md"


def test_framework_md_bundled_copy_matches_root() -> None:
    """FR01: bundled ``data/framework.md`` must match the canonical copy byte-for-byte."""
    assert _CANONICAL.exists(), f"missing canonical FRAMEWORK.md at {_CANONICAL}"
    assert _BUNDLED.exists(), f"missing bundled framework.md at {_BUNDLED}"
    assert _BUNDLED.read_bytes() == _CANONICAL.read_bytes(), (
        "Bundled trw-mcp/src/trw_mcp/data/framework.md drifted from "
        ".trw/frameworks/FRAMEWORK.md. Run: cp .trw/frameworks/FRAMEWORK.md "
        "trw-mcp/src/trw_mcp/data/framework.md"
    )
