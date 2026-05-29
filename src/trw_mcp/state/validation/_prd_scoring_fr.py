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

# FR section heading: ### FR<digits> ... or ### PRD-X-FR<digits> ...
_FR_HEADING_RE = re.compile(r"^###\s+(?:PRD-[\w-]+-)?FR\d+.*$", re.MULTILINE)


def _extract_fr_sections(content: str) -> list[tuple[str, str]]:
    """Extract (fr_name, fr_body) pairs from PRD ``### FR`` headings.

    Body extraction stops at the next ``## `` heading to avoid leaking
    into non-FR sections.
    """
    matches = list(_FR_HEADING_RE.finditer(content))
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
    return sections


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
