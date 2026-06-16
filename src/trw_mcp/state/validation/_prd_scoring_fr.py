"""PRD scoring — FR-section extraction + per-FR coverage scorers.

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

3 helpers covering FR section parsing and per-FR file-path / assertion
coverage scoring (used by score_traceability_v2 + score_implementation_readiness).

Extracted as DIST-243 batch 57.
"""

from __future__ import annotations

import re

from trw_mcp.state.validation._prd_scoring_counts import _has_assertion_evidence
from trw_mcp.state.validation._prd_scoring_traceability import (
    _extract_fr_id,
    _extract_traceability_matrix_rows,
    _has_impl_reference,
    _has_test_reference,
)

# FR section heading (historical, un-hyphenated form) — ``### FR01 ...`` or
# ``### PRD-X-FR01 ...``. Behavior unchanged for every PRD variant.
_FR_HEADING_RE = re.compile(r"^###\s+(?:PRD-[\w-]+-)?FR\d+.*$", re.MULTILINE)

# Hyphenated FR heading — ``### FR-01: ...`` / ``### PRD-X-FR-01: ...``. Only
# counted when followed by ``:`` (a true FR heading) so a non-FR prose heading
# such as ``### FR-01 rollback`` is NOT matched. Applied only to FIX-variant
# PRDs (PRD-FIX-103-FR01) so feature/infra PRDs keep their prior fr_count
# (FR03 no-regression).
_FR_HYPHEN_HEADING_RE = re.compile(r"^###\s+(?:PRD-[\w-]+-)?FR-\d+\s*:.*$", re.MULTILINE)

# Markdown-table FR row, e.g. ``| FR-1 | requirement | verification |`` or
# ``| PRD-X-FR01 | ... |``. The id appears in the first cell (PRD-FIX-103-FR01).
_FR_TABLE_ROW_RE = re.compile(r"^\|\s*(?:PRD-[\w-]+-)?FR-?(\d+)\b.*\|", re.MULTILINE)

# Locates the Functional Requirements section so table-FR detection is scoped
# to it (a Traceability Matrix lists the same FR ids and must not double-count).
_FR_SECTION_HEADING_RE = re.compile(r"^##\s+(?:\d+\.\s+)?Functional Requirements.*$", re.MULTILINE)


def _functional_requirements_segment(content: str) -> str:
    """Return the body of the ``## Functional Requirements`` section (or '')."""
    match = _FR_SECTION_HEADING_RE.search(content)
    if match is None:
        return ""
    start = match.end()
    next_section = re.search(r"^##\s+(?!#)", content[start:], re.MULTILINE)
    end = start + next_section.start() if next_section else len(content)
    return content[start:end]


def _content_is_fix_variant(content: str) -> bool:
    """Return True when the PRD frontmatter category maps to the FIX variant.

    The frontmatter is re-parsed from ``content`` (which includes the YAML
    block) because ``_extract_fr_sections`` is called with content only.
    Fail-open: a parse error is treated as non-FIX.
    """
    try:
        from trw_mcp.state.prd_utils import parse_frontmatter
        from trw_mcp.state.validation.template_variants import get_variant_for_category

        frontmatter = parse_frontmatter(content)
    except Exception:  # justified: scoring is fail-open — a parse error must not raise
        return False
    return get_variant_for_category(str(frontmatter.get("category", "") or "")) == "fix"


def _extract_table_fr_sections(content: str) -> list[tuple[str, str]]:
    """Extract (fr_name, fr_body) pairs from a table-style FR list.

    Scoped to the Functional Requirements section so FR rows in other tables
    (e.g. the Traceability Matrix) are not counted. Each distinct FR id is
    counted once; the row text is returned as the body so per-FR coverage
    scorers can still scan it for impl/test references.
    """
    segment = _functional_requirements_segment(content)
    if not segment:
        return []
    sections: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in _FR_TABLE_ROW_RE.finditer(segment):
        fr_id = f"FR{row.group(1)}"
        if fr_id in seen:
            continue
        seen.add(fr_id)
        sections.append((fr_id, row.group(0)))
    return sections


def _extract_fr_sections(content: str) -> list[tuple[str, str]]:
    """Extract (fr_name, fr_body) pairs from PRD FR headings or FR tables.

    Heading-style ``### FR01`` / ``### PRD-X-FR-01:`` sections are detected
    first (body extraction stops at the next ``## `` heading). When no
    heading-style FRs are found AND the PRD is a FIX-variant, a table-style FR
    list inside the Functional Requirements section is used instead so compact
    FIX PRDs are not under-counted (PRD-FIX-103-FR01).

    The table fallback is scoped to the FIX variant so feature/infrastructure
    PRDs that previously relied on the global ``FR\\d+`` count fallback keep
    their existing ``fr_count`` (FR03 no-regression).
    """
    is_fix = _content_is_fix_variant(content)
    matches = list(_FR_HEADING_RE.finditer(content))
    if is_fix:
        # FIX PRDs may use the hyphenated heading form too; merge + sort by
        # position so the body-extraction span logic stays correct.
        hyphen_matches = [m for m in _FR_HYPHEN_HEADING_RE.finditer(content) if not _FR_HEADING_RE.match(m.group(0))]
        matches = sorted([*matches, *hyphen_matches], key=lambda m: m.start())
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        name = m.group(0).strip().lstrip("#").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        segment = content[start:end]
        for line in segment.splitlines(keepends=True):
            if line.startswith("## ") and not line.startswith("###"):
                end = start + segment.find(line)
                break
        sections.append((name, content[start:end]))
    if sections:
        return sections
    if not is_fix:
        return []
    return _extract_table_fr_sections(content)


def _score_file_path_coverage(content: str, fr_sections: list[tuple[str, str]]) -> float:
    """Score (FRs_with_impl + FRs_with_test) / (2 * total_FRs)."""
    if not fr_sections:
        return 0.0
    total_frs = len(fr_sections)
    frs_with_impl = 0
    frs_with_test = 0
    traceability_rows = _extract_traceability_matrix_rows(content)
    for name, body in fr_sections:
        fr_id = _extract_fr_id(name)
        matrix_row = traceability_rows.get(fr_id, "") if fr_id is not None else ""
        combined = body if not matrix_row else f"{body}\n{matrix_row}"
        if _has_impl_reference(combined):
            frs_with_impl += 1
        if _has_test_reference(combined):
            frs_with_test += 1
    return (frs_with_impl + frs_with_test) / (2 * total_frs)


def _score_assertion_coverage(content: str, fr_sections: list[tuple[str, str]]) -> float:
    """Score FRs_with_assertion / total_FRs (explicit-syntax detector)."""
    del content  # accepted for API parity; coverage is computed from fr_sections only
    if not fr_sections:
        return 0.0
    frs_with_assertion = sum(1 for _name, body in fr_sections if _has_assertion_evidence(body))
    return frs_with_assertion / len(fr_sections)
