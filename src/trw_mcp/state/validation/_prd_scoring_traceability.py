"""PRD scoring — traceability matrix + reference extraction helpers.

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

Reference-extraction regexes + traceability-matrix scoring helpers
(spread across 9 helper functions and 4 regexes). Used by both the
top-level ``compute_grounding_penalty`` (parent) and the
``score_traceability_v2`` scorer (parent).

Extracted as DIST-243 batch 52 to chip away at the 847-LOC parent
``_prd_scoring.py`` module.
"""

from __future__ import annotations

import re

# Known test file naming conventions supported by _TEST_REF_RE.
_KNOWN_TEST_PATTERNS: dict[str, str] = {
    "python": "test_*.py or test_*.py::test_func (pytest prefix convention)",
    "typescript": "*.test.ts, *.test.tsx (Jest/Vitest suffix convention)",
    "javascript": "*.test.js, *.spec.js (Jest/Jasmine conventions)",
    "go": "*_test.go (Go testing suffix convention)",
    "rust": "tests/*.rs (Rust integration tests directory convention)",
    "java": "*Test.java, *Tests.java (JUnit suffix convention)",
    "ruby": "*_spec.rb (RSpec suffix convention)",
    "generic_spec": "*.spec.ts, *.spec.tsx (spec suffix, any extension)",
}

# Backtick-wrapped test-file references covering Python prefix, TS/JS
# suffix, Go/Ruby suffix, Java suffix, and tests/ directory conventions.
_TEST_REF_RE = re.compile(
    r"`(?:"
    r"test[\w_]*\.[\w.]+[:\w]*"
    r"|[\w/]+\.(?:test|spec)\.[\w]+[:\w]*"
    r"|[\w/]+(?:_test|_spec)\.[\w]+[:\w]*"
    r"|[\w/]+(?:Test|Tests|Spec)\.[\w]+[:\w]*"
    r"|tests?/[\w/]+\.[\w]+[:\w]*"
    r")`",
)

# Backtick-wrapped implementation file references.
_IMPL_REF_RE = re.compile(
    r"`(?:[-\w./*]+(?:\.[\w]+)(?:[:#][-\w./*#]+)?)`",
)

# Bare (non-backtick-wrapped) test file references.
_BARE_TEST_REF_RE = re.compile(
    r"(?<!`)(?:"
    r"test[\w./-]*\.[\w]+(?:::[\w.-]+)?"
    r"|[\w./-]+\.(?:test|spec)\.[\w]+(?:::[\w.-]+)?"
    r"|[\w./-]+(?:_test|_spec)\.[\w]+(?:::[\w.-]+)?"
    r"|[\w./-]+(?:Test|Tests|Spec)\.[\w]+(?:::[\w.-]+)?"
    r"|tests?/[\w./-]+\.[\w]+(?:::[\w.-]+)?"
    r")(?!`)",
)

# Bare implementation file references.
_BARE_IMPL_REF_RE = re.compile(
    r"(?<!`)(?:"
    r"(?:[-\w*]+/)+[-\w.*]+\.[A-Za-z][\w]*(?:[:#][-\w./*#]+)?"
    r"|(?:[-A-Za-z0-9_]*[A-Za-z_][-A-Za-z0-9_]*)\.[A-Za-z][\w]*(?:[:#][-\w./*#]+)?"
    r")(?!`)",
)


def _count_table_rows(content: str, heading: str) -> int:
    """Count substantive markdown table rows under a named subsection."""
    marker = f"### {heading}"
    start = content.find(marker)
    if start == -1:
        return 0
    tail = content[start + len(marker) :]
    next_heading = re.search(r"^###\s+.+$", tail, re.MULTILINE)
    body = tail[: next_heading.start()] if next_heading else tail
    rows = 0
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue
        rows += 1
    return max(rows - 1, 0)


def _collect_reference_matches(content: str, *patterns: re.Pattern[str]) -> set[str]:
    """Collect unique implementation/test reference tokens across regex variants."""
    matches: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(content):
            token = match.group(0).strip("`")
            if token:
                matches.add(token)
    return matches


def _normalize_reference_token(token: str) -> str:
    """Collapse selectors/anchors so test refs can be excluded from impl counts."""
    return token.split("::", 1)[0].split("#", 1)[0]


def _has_impl_reference(content: str) -> bool:
    """Return True when content contains at least one implementation file ref."""
    impl_refs = _collect_reference_matches(content, _IMPL_REF_RE, _BARE_IMPL_REF_RE)
    test_refs = _collect_reference_matches(content, _TEST_REF_RE, _BARE_TEST_REF_RE)
    normalized_test_refs = {_normalize_reference_token(token) for token in test_refs}
    return any(_normalize_reference_token(token) not in normalized_test_refs for token in impl_refs)


def _has_test_reference(content: str) -> bool:
    """Return True when content contains at least one test file ref."""
    return bool(_collect_reference_matches(content, _TEST_REF_RE, _BARE_TEST_REF_RE))


def _extract_fr_id(label: str) -> str | None:
    """Extract the normalized FR identifier from a heading or row label."""
    match = re.search(r"\bFR\d+\b", label)
    return match.group(0) if match else None


def _extract_traceability_matrix_rows(content: str) -> dict[str, str]:
    """Map each FR in the traceability matrix to its corresponding row content."""
    if "Traceability Matrix" not in content:
        return {}
    matrix_tail = content.split("Traceability Matrix", 1)[1]
    next_heading = re.search(r"^##\s+\d+\.\s+.+$", matrix_tail, re.MULTILINE)
    matrix_section = matrix_tail[: next_heading.start()] if next_heading else matrix_tail
    rows_by_fr: dict[str, list[str]] = {}
    for line in matrix_section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or re.match(r"^\|[\s\-:|]+\|$", stripped):
            continue
        fr_id = _extract_fr_id(stripped)
        if fr_id is None:
            continue
        rows_by_fr.setdefault(fr_id, []).append(stripped)
    return {fr_id: "\n".join(rows) for fr_id, rows in rows_by_fr.items()}


def _count_populated_trace_fields(trace_data: object) -> int:
    """Count populated traceability frontmatter fields."""
    if not isinstance(trace_data, dict):
        return 0
    populated_fields = 0
    for field in ("implements", "depends_on", "enables"):
        value = trace_data.get(field, [])
        if isinstance(value, list) and value:
            populated_fields += 1
    return populated_fields


def _score_traceability_matrix(content: str) -> tuple[float, float]:
    """Return matrix-score and proof-score for the traceability matrix."""
    if "Traceability Matrix" not in content:
        return 0.0, 0.0
    traceability_rows = _extract_traceability_matrix_rows(content)
    fr_refs = sorted(traceability_rows)
    if not fr_refs:
        return 0.0, 0.0
    rows_with_impl = sum(1 for row in traceability_rows.values() if _has_impl_reference(row))
    rows_with_test = sum(1 for row in traceability_rows.values() if _has_test_reference(row))
    rows_with_both = sum(
        1 for row in traceability_rows.values() if _has_impl_reference(row) and _has_test_reference(row)
    )
    matrix_score = min((rows_with_impl + rows_with_test) / (2 * len(fr_refs)), 1.0)
    proof_score = min(rows_with_both / len(fr_refs), 1.0)
    return matrix_score, proof_score
