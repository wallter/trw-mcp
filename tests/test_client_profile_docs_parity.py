"""Parity tests for client-profile documentation generated from runtime code."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.client_profiles.markdown import render_matrix_page, render_quick_reference_table

DOC_ROOT = Path(__file__).resolve().parents[2] / "docs"
OVERVIEW_DOC = DOC_ROOT / "CLIENT-PROFILES.md"
MATRIX_DOC = DOC_ROOT / "client-profiles" / "matrix.md"


def _extract_table(doc_text: str, heading: str) -> str:
    lines = doc_text.splitlines()
    start = lines.index(heading)
    table_lines: list[str] = []
    for line in lines[start + 1 :]:
        if table_lines and not line.startswith("|"):
            break
        if line.startswith("|"):
            table_lines.append(line)
    return "\n".join(table_lines)


def test_generated_matrix_doc_matches_renderer() -> None:
    assert MATRIX_DOC.read_text(encoding="utf-8") == render_matrix_page()


def test_overview_quick_reference_matches_renderer() -> None:
    overview = OVERVIEW_DOC.read_text(encoding="utf-8")
    assert _extract_table(overview, "## Quick Reference") == render_quick_reference_table()


def test_overview_doc_stays_within_350_loc() -> None:
    assert len(OVERVIEW_DOC.read_text(encoding="utf-8").splitlines()) <= 350
