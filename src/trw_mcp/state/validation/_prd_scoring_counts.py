"""PRD scoring — assertion + reference + verification count helpers.

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

5 regex constants + 5 count helpers used by the score_* orchestrators.

Extracted as DIST-243 batch 56.
"""

from __future__ import annotations

import re

from trw_mcp.state.validation._prd_scoring_traceability import (
    _BARE_IMPL_REF_RE,
    _BARE_TEST_REF_RE,
    _IMPL_REF_RE,
    _TEST_REF_RE,
    _collect_reference_matches,
    _normalize_reference_token,
)

# Assertion keyword pattern for machine-verifiable assertions (PRD-QUAL-056-FR02)
_ASSERTION_RE = re.compile(r"grep_present|grep_absent|file_exists|command_succeeds|glob_exists")
_ASSERTION_BLOCK_RE = re.compile(r"```assertions\b.*?```", re.IGNORECASE | re.DOTALL)
_ASSERTIONS_HEADING_RE = re.compile(
    r"^\s*(?:\*\*|__)?Assertions(?:\*\*|__)?\s*:\s*$",
    re.IGNORECASE,
)
_ASSERTION_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?`?(?:grep_present|grep_absent|file_exists|command_succeeds|glob_exists)\b",
    re.MULTILINE,
)
_ASSERTION_JSON_TYPE_RE = re.compile(
    r'"type"\s*:\s*"(?:grep_present|grep_absent|file_exists|command_succeeds|glob_exists)"',
    re.IGNORECASE,
)

# Recognizable verification commands in PRD text.
_VERIFICATION_COMMAND_RE = re.compile(
    r"\b(?:pytest|python -m pytest|npx vitest run|npm(?: run)? test|make test|go test|cargo test)\b",
    re.IGNORECASE,
)


def _has_assertion_evidence(content: str) -> bool:
    """Return True when content contains explicit assertion syntax, not prose mentions."""
    if _ASSERTION_BLOCK_RE.search(content) or _ASSERTION_LINE_RE.search(content):
        return True
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if not _ASSERTIONS_HEADING_RE.match(line):
            continue
        for assertion_line in lines[index + 1 :]:
            stripped = assertion_line.strip()
            if not stripped:
                break
            if not re.match(r"^[-*]\s+", stripped):
                break
            if _ASSERTION_JSON_TYPE_RE.search(stripped):
                return True
    return False


def _count_impl_refs(content: str) -> int:
    """Count unique implementation file references, wrapped or bare."""
    impl_refs = _collect_reference_matches(content, _IMPL_REF_RE, _BARE_IMPL_REF_RE)
    test_refs = _collect_reference_matches(content, _TEST_REF_RE, _BARE_TEST_REF_RE)
    normalized_test_refs = {_normalize_reference_token(token) for token in test_refs}
    return len({token for token in impl_refs if _normalize_reference_token(token) not in normalized_test_refs})


def _count_test_refs(content: str) -> int:
    """Count unique test file references, wrapped or bare."""
    return len(_collect_reference_matches(content, _TEST_REF_RE, _BARE_TEST_REF_RE))


def _count_verification_commands(content: str) -> int:
    """Count recognizable verification commands referenced in the PRD."""
    return len(_VERIFICATION_COMMAND_RE.findall(content))


def _count_planned_requirements(content: str, fr_sections: list[tuple[str, str]] | None = None) -> int:
    """Count planned FRs without over-counting repeated traceability references."""
    from trw_mcp.state.validation._prd_scoring import _extract_fr_sections

    resolved_fr_sections = fr_sections if fr_sections is not None else _extract_fr_sections(content)
    if resolved_fr_sections:
        return len(resolved_fr_sections)
    unique_refs = set(re.findall(r"\bFR\d+\b", content))
    return max(len(unique_refs), 1)
