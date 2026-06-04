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
    """Extract the normalized FR identifier from a heading or row label.

    Accepts both ``FR01`` (no hyphen) and ``FR-01`` (hyphenated) forms;
    normalizes to the un-hyphenated canonical key so callers can dict-
    join matrix rows + heading rows regardless of which style the PRD
    author chose. Both styles are common across the catalogue and the
    template; the prior ``\\bFR\\d+\\b`` regex only matched the un-
    hyphenated form, silently zeroing matrix_score on every PRD that
    used ``FR-01`` in its Traceability Matrix.
    """
    match = re.search(r"\bFR-?(\d+)\b", label)
    return f"FR{match.group(1)}" if match else None


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


# Headings whose backtick file references count as traceability evidence for
# compact FIX PRDs (PRD-FIX-103-FR02).
_FIX_EVIDENCE_HEADINGS: tuple[str, ...] = ("Root Cause", "Fix Verification")

# Cap on the partial credit awarded to the matrix dimension when only
# evidence.sources / Root-Cause file refs are present (PRD-FIX-103-FR02).
# Kept well below 1.0 so a real, populated Traceability Matrix always scores
# strictly higher than the sources-only fallback.
_EVIDENCE_FALLBACK_MAX: float = 0.5


def _traceability_frontmatter_empty(frontmatter: dict[str, object]) -> bool:
    """Return True when no ``traceability.{implements,depends_on,enables}`` is set."""
    return _count_populated_trace_fields(frontmatter.get("traceability", {})) == 0


def _score_evidence_source_fallback(frontmatter: dict[str, object], content: str) -> float:
    """Partial traceability credit from evidence.sources + Root-Cause file refs.

    PRD-FIX-103-FR02: FIX/compact PRDs often record grounding under
    ``evidence.sources`` (or a prose Root Cause / Fix Verification section with
    backtick-wrapped file paths) rather than the ``traceability.*``
    frontmatter, which left ``field_ratio`` (and the whole dimension) at 0.
    This awards bounded partial credit so the dimension is not a false-zero.

    Scoped to the FIX variant only (the false-negative class this addresses)
    so feature/infrastructure PRDs — which intentionally use the full
    Traceability Matrix + frontmatter contract — are unaffected (FR03
    no-regression). Returns 0.0 when ``traceability.*`` is already populated.
    """
    from trw_mcp.state.validation.template_variants import get_variant_for_category

    if get_variant_for_category(str(frontmatter.get("category", "") or "")) != "fix":
        return 0.0
    if not _traceability_frontmatter_empty(frontmatter):
        return 0.0

    source_count = 0
    sources = frontmatter.get("sources")
    if not isinstance(sources, list) or not sources:
        evidence = frontmatter.get("evidence")
        if isinstance(evidence, dict):
            nested = evidence.get("sources")
            sources = nested if isinstance(nested, list) else []
    if isinstance(sources, list):
        source_count = len([s for s in sources if s])

    fix_ref_count = 0
    for heading in _FIX_EVIDENCE_HEADINGS:
        marker = f"### {heading}"
        idx = content.find(marker)
        if idx == -1:
            idx = content.find(f"## {heading}")
            # also tolerate a numbered "## N. Root Cause" heading
            if idx == -1:
                section_match = re.search(
                    rf"^##\s+(?:\d+\.\s+)?{re.escape(heading)}.*$",
                    content,
                    re.MULTILINE,
                )
                idx = section_match.start() if section_match else -1
        if idx == -1:
            continue
        tail = content[idx:]
        next_heading = re.search(r"^##\s+", tail[3:], re.MULTILINE)
        body = tail[: next_heading.start() + 3] if next_heading else tail
        if _has_impl_reference(body):
            fix_ref_count += 1

    if source_count == 0 and fix_ref_count == 0:
        return 0.0

    # >=2 sources OR a file-referencing Root Cause earns the full partial cap;
    # a single weak signal earns half of it.
    strong = source_count >= 2 or fix_ref_count >= 1
    return _EVIDENCE_FALLBACK_MAX if strong else _EVIDENCE_FALLBACK_MAX * 0.5


def _evidence_fallback_from_content(content: str) -> float:
    """Compute the FR02 evidence-source fallback by parsing frontmatter from content.

    ``_score_traceability_matrix`` only receives ``content`` (not the parsed
    frontmatter), but ``content`` includes the YAML frontmatter block, so the
    frontmatter is re-parsed here to look up ``sources`` / ``evidence.sources``
    (PRD-FIX-103-FR02). Fail-open: any parse error yields no credit.
    """
    try:
        from trw_mcp.state.prd_utils import parse_frontmatter

        frontmatter = parse_frontmatter(content)
    except Exception:  # justified: scoring is fail-open — a parse error must not raise
        return 0.0
    return _score_evidence_source_fallback(frontmatter, content)


def _score_traceability_matrix(content: str) -> tuple[float, float]:
    """Return matrix-score and proof-score for the traceability matrix.

    When no usable Traceability Matrix is present, falls back to bounded
    partial credit derived from ``evidence.sources`` / Root-Cause file refs so
    compact FIX PRDs are not scored a false zero (PRD-FIX-103-FR02).
    """
    if "Traceability Matrix" not in content:
        return _evidence_fallback_from_content(content), 0.0
    traceability_rows = _extract_traceability_matrix_rows(content)
    fr_refs = sorted(traceability_rows)
    if not fr_refs:
        return _evidence_fallback_from_content(content), 0.0
    rows_with_impl = sum(1 for row in traceability_rows.values() if _has_impl_reference(row))
    rows_with_test = sum(1 for row in traceability_rows.values() if _has_test_reference(row))
    rows_with_both = sum(
        1 for row in traceability_rows.values() if _has_impl_reference(row) and _has_test_reference(row)
    )
    matrix_score = min((rows_with_impl + rows_with_test) / (2 * len(fr_refs)), 1.0)
    proof_score = min(rows_with_both / len(fr_refs), 1.0)
    return matrix_score, proof_score
