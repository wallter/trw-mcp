"""PRD-CORE-348-FR01: Markdown view (SPEC §13 order, R-SEC-5 escaping). Golden files are TRW's own."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.handoff._vectors import STANDARD, VECTORS
from trw_mcp.handoff import load, seal
from trw_mcp.handoff._render import escape, render_markdown

GOLDEN = Path(__file__).parent / "golden"
ORDER = [
    "Objective",
    "Constraints",
    "Risks",
    "First action",
    "Read first",
    "State",
    "Not done",
    "Unknowns",
    "Contingencies",
    "Decisions",
    "Remaining actions",
    "Read-back requested",
    "Extensions",
]


@pytest.mark.parametrize("name", ["01-standard-handoff", "03-minimal-unaddressed"])
def test_render_matches_golden(name: str) -> None:
    rendered = render_markdown(load(VECTORS / "valid" / f"{name}.json"))
    assert rendered == (GOLDEN / f"{name}.md").read_text(encoding="utf-8")


def test_header_first_and_sections_in_spec_order() -> None:
    doc = load(STANDARD)
    lines = render_markdown(doc).splitlines()
    assert lines[0].startswith(f"# Handoff {doc['handoff_id']} ")
    assert "(tier: standard;" in lines[0]
    assert lines[2].startswith(f"Rendered view of {doc['handoff_id']} sha256\\:{doc['integrity']['digest'][7:]}")
    titles = [line[3:] for line in lines if line.startswith("## ")]
    assert titles == ORDER


def test_minimal_note_is_labelled_unadmitted_and_none_known_shows_check() -> None:
    rendered = render_markdown(load(VECTORS / "valid" / "03-minimal-unaddressed.json"))
    assert "unadmitted note" in rendered
    assert "None known — checked: " in rendered


def test_escape_two_passes_and_single_line() -> None:
    assert escape("a & b <c> *d*\nx") == "a &amp; b &lt;c&gt; \\*d\\* x"
    assert escape("[x](javascript:alert)") == "\\[x\\]\\(javascript\\:alert\\)"


def test_escape_strips_terminal_controls_and_bidi_overrides() -> None:
    assert escape("a\x1b[31mb\u202ec\x00d") == "a \\[31mb c d"


def test_injection_in_field_cannot_open_markdown_structure() -> None:
    doc = load(STANDARD)
    doc["objective"]["goal"] = "ok\n## Injected\n<script>alert(1)</script> [link](http://x)"
    rendered = render_markdown(seal(doc))
    assert "\n## Injected" not in rendered
    assert "<script>" not in rendered
    assert "](http" not in rendered


def test_evidence_without_the_optional_producer_renders() -> None:
    """``producer`` is optional in the schema; the view printed a KeyError instead (eval, 2026-10-05)."""
    doc = load(STANDARD)
    evidence = [ev for claim in doc["claims"] for ev in claim.get("evidence", [])]
    assert evidence, "the standard vector needs a verified claim for this test"
    for ev in evidence:
        ev.pop("producer", None)
    assert "— " in render_markdown(doc) or "—;" in render_markdown(doc)
