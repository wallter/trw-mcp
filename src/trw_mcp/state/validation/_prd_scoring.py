"""PRD quality scoring — metric computation for content density, structure, traceability.

Extracted from prd_quality.py to separate scoring (numeric metric computation)
from validation (pass/fail gate checks). All functions here compute and return
DimensionScore / SectionScore values without making pass/fail decisions.
"""

from __future__ import annotations

import functools
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    SectionScore,
)
from trw_mcp.state.validation.template_variants import get_required_sections, get_variant_for_category

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Section heading pattern: ## N. Title
_HEADING_RE = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)

# Placeholder patterns for content density (common template defaults)
_PLACEHOLDER_RE = re.compile(
    r"^\s*<!--.*?-->\s*$"
    r"|^\s*\{[^}]+\}\s*$"
    r"|^\s*\[.*TODO.*\]\s*$",
    re.IGNORECASE,
)

# Section headings expected in an AARE-F compliant PRD
_EXPECTED_SECTION_NAMES: list[str] = [
    "Problem Statement",
    "Goals & Non-Goals",
    "User Stories",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Technical Approach",
    "Test Strategy",
    "Rollout Plan",
    "Success Metrics",
    "Dependencies & Risks",
    "Open Questions",
    "Traceability Matrix",
]

# Sections with higher weight in density scoring
_HIGH_WEIGHT_SECTIONS: dict[str, float] = {
    "Problem Statement": 2.0,
    "Functional Requirements": 2.0,
    "Traceability Matrix": 1.5,
}

# Section weights used by external consumers
_SECTION_WEIGHTS: dict[str, float] = _HIGH_WEIGHT_SECTIONS

# Pre-compiled regexes for ambiguity rate computation (FR02 -- PRD-FIX-054).
# Using word-boundary matching to avoid false positives on substrings.
# Compiled once at module load to prevent ReDoS.
_VAGUE_TERMS_RE = re.compile(
    r"\b(?:might|possibly|approximately|as needed|if possible|as appropriate)\b"
    r"|should consider"
    r"|etc\."
    r"|and/or",
    re.IGNORECASE,
)

# Lines that qualify as requirement-like statements
_REQUIREMENT_LINE_RE = re.compile(
    r"FR\d+|NFR\d+|- \[ \]|\bWhen\b|\bWhile\b|\bIf\b|\bWhere\b",
)

# Known test file naming conventions supported by _TEST_REF_RE.
# Used for documentation and external introspection.
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

# Pre-compiled regex matching test file references (backtick-wrapped) for all
# supported languages. Covers:
#   - Python prefix:  `test_foo.py`, `test_foo.py::test_bar`
#   - TS/JS suffix:   `Component.test.tsx`, `api.spec.ts`
#   - Go suffix:      `handler_test.go`
#   - Java suffix:    `UserServiceTest.java`, `UserServiceTests.java`
#   - Ruby suffix:    `user_spec.rb`
#   - tests/ dir:     `tests/integration.rs`
_TEST_REF_RE = re.compile(
    r"`(?:"
    r"test[\w_]*\.[\w.]+[:\w]*"  # Python: test_foo.py, test_foo.py::bar
    r"|[\w/]+\.(?:test|spec)\.[\w]+[:\w]*"  # TS/JS: foo.test.ts, foo.spec.tsx
    r"|[\w/]+(?:_test|_spec)\.[\w]+[:\w]*"  # Go/Ruby: foo_test.go, user_spec.rb
    r"|[\w/]+(?:Test|Tests|Spec)\.[\w]+[:\w]*"  # Java: FooTest.java, FooTests.java
    r"|tests?/[\w/]+\.[\w]+[:\w]*"  # tests/ dir: tests/integration.rs
    r")`",
)

# Backtick-wrapped source / implementation file references used inside
# traceability matrices. Accept hyphenated repo roots (e.g. ``trw-mcp/``),
# nested dirs, shell scripts, markdown, and optional line/anchor suffixes.
_IMPL_REF_RE = re.compile(
    r"`(?:[-\w./*]+(?:\.[\w]+)(?:[:#][-\w./*#]+)?)`",
)

# Bare (non-backtick-wrapped) test file references — catches references in
# prose, table cells, and list items that lack backtick delimiters.
_BARE_TEST_REF_RE = re.compile(
    r"(?<!`)(?:"
    r"test[\w./-]*\.[\w]+(?:::[\w.-]+)?"
    r"|[\w./-]+\.(?:test|spec)\.[\w]+(?:::[\w.-]+)?"
    r"|[\w./-]+(?:_test|_spec)\.[\w]+(?:::[\w.-]+)?"
    r"|[\w./-]+(?:Test|Tests|Spec)\.[\w]+(?:::[\w.-]+)?"
    r"|tests?/[\w./-]+\.[\w]+(?:::[\w.-]+)?"
    r")(?!`)",
)
# Bare implementation file references (non-backtick-wrapped).
_BARE_IMPL_REF_RE = re.compile(
    r"(?<!`)(?:"
    r"(?:[-\w*]+/)+[-\w.*]+\.[A-Za-z][\w]*(?:[:#][-\w./*#]+)?"
    r"|(?:[-A-Za-z0-9_]*[A-Za-z_][-A-Za-z0-9_]*)\.[A-Za-z][\w]*(?:[:#][-\w./*#]+)?"
    r")(?!`)",
)

_SUBHEADING_RE = re.compile(r"^###\s+(.+)$", re.MULTILINE)

# FR heading pattern for extracting individual FR sections (PRD-QUAL-056-FR01)
_FR_HEADING_RE = re.compile(r"^###\s+(?:PRD-[\w-]+-)?FR\d+.*$", re.MULTILINE)

# Assertion keyword pattern for machine-verifiable assertions (PRD-QUAL-056-FR02)
_ASSERTION_RE = re.compile(
    r"grep_present|grep_absent|file_exists|command_succeeds|glob_exists"
)
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

# AI/Agentic detection keywords (PRD-QUAL-055). Keep these boundary-aware to
# avoid false positives from ordinary words like "maintainers".
_AI_KEYWORD_RE = re.compile(
    r"\b(?:ai|llm|agentic|generative|prompt(?:ing)?|inference|foundation model|language model)\b",
    re.IGNORECASE,
)
_AI_OPERATIONAL_HEADINGS = (
    "Data / Context Provenance",
    "Failure Modes",
    "Safe Degradation",
    "Human Oversight",
    "Escalation",
    "Evaluation Plan",
    "Release Gate",
    "Monitoring Plan",
    "Risk Register",
    "Failure Class",
)

_REQUIRED_SUBSECTIONS_BY_VARIANT: dict[str, list[str]] = {
    "feature": [
        "Primary Control Points",
        "Behavior Switch Matrix",
        "Unit Tests",
        "Integration Tests",
        "Acceptance Tests",
        "Regression Tests",
        "Negative / Fallback Tests",
        "Completion Evidence (Definition of Done)",
        "Migration / Backward Compatibility",
    ],
    "infrastructure": [
        "Primary Control Points",
        "Behavior Switch Matrix",
        "Unit Tests",
        "Integration Tests",
        "Acceptance Tests",
        "Regression Tests",
        "Negative / Fallback Tests",
        "Completion Evidence (Definition of Done)",
        "Migration / Backward Compatibility",
    ],
    "fix": [
        "Root Cause",
        "Contributing Factors",
        "Fix Verification",
        "Regression Tests",
        "Negative / Fallback Tests",
        "Completion Evidence (Definition of Done)",
        "Migration / Backward Compatibility",
    ],
    "research": [
        "Approach",
        "Data Sources",
        "Evaluation Criteria",
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def get_project_files(project_root: Path) -> frozenset[str]:
    """Cache the set of all repository files relative to project_root to make grounding checks fast."""
    files = set()
    for root, _, filenames in os.walk(project_root):
        if ".git" in root or "node_modules" in root or ".venv" in root:
            continue
        rel_root = Path(root).relative_to(project_root)
        for name in filenames:
            files.add(str(rel_root / name) if str(rel_root) != "." else name)
    return frozenset(files)


def compute_grounding_penalty(content: str, project_root: Path | None) -> tuple[float, list[str]]:
    """Compute multiplicative penalty for hallucinated file paths (PRD-QUAL-063).

    Checks all backtick-wrapped paths against the cached project file listing.
    Paths containing 'new: ' or ending with '(new)' are exempt.

    Returns:
        tuple[penalty_multiplier, list[hallucinated_paths]]
    """
    if not project_root:
        return 1.0, []

    impl_refs = _collect_reference_matches(content, _IMPL_REF_RE)
    test_refs = _collect_reference_matches(content, _TEST_REF_RE)
    all_refs = impl_refs | test_refs

    hallucinated: list[str] = []
    try:
        project_files = get_project_files(project_root)
        for ref in all_refs:
            # Strip markdown/prose exemptions
            clean_ref = ref.strip("` ").split()[0]  # Take first token if spaces exist
            clean_ref = _normalize_reference_token(clean_ref)

            # Exempt 'new:' or '(new)'
            # We match raw 'ref' for text exemptions but clean_ref for existence.
            if "(new)" in ref.lower() or "new:" in ref.lower() or "new " in ref.lower():
                continue

            if clean_ref not in project_files:
                hallucinated.append(clean_ref)

        penalty = 0.9 ** len(hallucinated)
        return penalty, sorted(hallucinated)
    except Exception:  # justified: fail-open, missing filesystem context should not zero traceability scoring
        # Fail open if filesystem access fails
        return 1.0, []

def _get_section_weights(config: TRWConfig) -> dict[str, float]:
    """Build per-section weight map from TRWConfig (PRD-CORE-080-FR04).

    Reads configurable per-section weights from TRWConfig flat fields
    (``validation_section_weight_*``) and returns a dict ready for use
    in weighted-average density computation. Falls back to module-level
    ``_HIGH_WEIGHT_SECTIONS`` defaults when config fields are at default.

    Args:
        config: TRWConfig instance to read weight fields from.

    Returns:
        Dict mapping section name to weight multiplier (default 1.0 for
        unlisted sections).
    """
    return {
        "Problem Statement": config.density_weight_problem_statement,
        "Functional Requirements": config.density_weight_functional_requirements,
        "Traceability Matrix": config.density_weight_traceability_matrix,
    }


def _compute_ambiguity_rate(content: str) -> float:
    """Compute ambiguity rate: vague-term count / requirement-statement count.

    Counts occurrences of vague terms (might, should consider, possibly,
    approximately, as needed, etc., and/or, if possible, as appropriate)
    divided by the total number of requirement-like lines (lines matching
    FR\\d+, NFR\\d+, '- [ ]', or EARS keywords When/While/If/Where).

    Returns 0.0 when no requirement-like statements are found to avoid
    division by zero.

    Args:
        content: Full PRD markdown content.

    Returns:
        Ambiguity rate as a non-negative float (>= 0.0).
    """
    vague_count = len(_VAGUE_TERMS_RE.findall(content))
    req_lines = [line for line in content.splitlines() if _REQUIREMENT_LINE_RE.search(line)]
    total_req = len(req_lines)
    if total_req == 0:
        return 0.0
    return vague_count / total_req


def _parse_section_content(content: str) -> list[tuple[str, str]]:
    """Split PRD content into (section_name, section_body) pairs.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of (section_name, section_body) tuples.
    """
    # Strip frontmatter
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    fm_match = _FRONTMATTER_RE.match(content)
    body = content[fm_match.end() :] if fm_match else content

    sections: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(body))

    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((name, body[start:end]))

    return sections


def _extract_subheadings(content: str) -> set[str]:
    """Return all level-3 markdown subheading titles present in the PRD body."""
    return {match.group(1).strip() for match in _SUBHEADING_RE.finditer(content)}


def _is_ai_agentic_prd(frontmatter: dict[str, object], content: str) -> bool:
    """Heuristic detection of AI/LLM/agentic PRDs.

    Returns True if:
    - PRD contains explicit AI/LLM/agentic keywords as standalone terms, or
    - PRD includes AI operational headings used by the hardening template, or
    - PRD category is QUAL and the document contains one of those explicit cues

    Args:
        frontmatter: Parsed YAML frontmatter dictionary.
        content: Full PRD markdown content.

    Returns:
        True if the PRD appears to be AI/LLM/agentic in nature.
    """
    category = str(frontmatter.get("category", "")).upper()
    title = str(frontmatter.get("title", ""))
    title_keyword_match = _AI_KEYWORD_RE.search(title) is not None
    body_keyword_matches = {match.group(0).lower() for match in _AI_KEYWORD_RE.finditer(content)}
    operational_heading_match = any(heading in content for heading in _AI_OPERATIONAL_HEADINGS)

    if operational_heading_match or title_keyword_match:
        return True

    if category == "QUAL":
        return bool(body_keyword_matches)

    return len(body_keyword_matches) >= 2


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
    return any(
        _normalize_reference_token(token) not in normalized_test_refs
        for token in impl_refs
    )


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
    rows_with_both = sum(1 for row in traceability_rows.values() if _has_impl_reference(row) and _has_test_reference(row))
    matrix_score = min((rows_with_impl + rows_with_test) / (2 * len(fr_refs)), 1.0)
    proof_score = min(rows_with_both / len(fr_refs), 1.0)
    return matrix_score, proof_score


def _score_ai_operational_evidence(content: str) -> tuple[float, float, float]:
    """Return evaluation, release, and monitoring evidence scores."""
    ai_evaluation_score = 0.0
    ai_release_score = 0.0
    ai_monitoring_score = 0.0

    if "Evaluation Plan" in content:
        eval_section = content.split("Evaluation Plan")[-1].lower()
        eval_keywords = [
            "baseline",
            "criteria",
            "threshold",
            "accuracy",
            "latency",
            "reliability",
            "A/B",
            "test",
            "user study",
            "metric",
        ]
        ai_evaluation_score = min(sum(1 for kw in eval_keywords if kw in eval_section) / len(eval_keywords), 1.0)

    if "Release Gate" in content:
        release_section = content.split("Release Gate")[-1].lower()
        release_keywords = [
            "canary",
            "phased",
            "rollback",
            "trigger",
            "threshold",
            "error rate",
            "latency",
            "confidence",
        ]
        ai_release_score = min(sum(1 for kw in release_keywords if kw in release_section) / len(release_keywords), 1.0)

    if "Monitoring Plan" in content:
        monitoring_section = content.split("Monitoring Plan")[-1].lower()
        monitoring_keywords = [
            "primary signal",
            "target threshold",
            "escalation",
            "alert",
            "drift",
            "latency",
            "error rate",
            "trust",
        ]
        ai_monitoring_score = min(
            sum(1 for kw in monitoring_keywords if kw in monitoring_section) / len(monitoring_keywords),
            1.0,
        )

    return ai_evaluation_score, ai_release_score, ai_monitoring_score


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
    resolved_fr_sections = fr_sections if fr_sections is not None else _extract_fr_sections(content)
    if resolved_fr_sections:
        return len(resolved_fr_sections)
    unique_refs = set(re.findall(r"\bFR\d+\b", content))
    return max(len(unique_refs), 1)


def _is_substantive_line(line: str) -> bool:
    """Check if a line is substantive (not blank, comment, heading, or placeholder).

    Args:
        line: Single line of text.

    Returns:
        True if the line contains substantive content.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if _PLACEHOLDER_RE.match(line):
        return False
    # Single-line HTML comment
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return False
    # Table separator rows (|---|---|)
    if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
        return False
    # Horizontal rules
    return not re.match(r"^\s*---\s*$", line)


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
        # Truncate at next ## heading (non-FR section boundary)
        segment = content[start:end]
        for line in segment.splitlines(keepends=True):
            if line.startswith("## ") and not line.startswith("###"):
                end = start + segment.find(line)
                break
        sections.append((name, content[start:end]))
    return sections


def _score_file_path_coverage(
    content: str,
    fr_sections: list[tuple[str, str]],
) -> float:
    """Score (FRs_with_impl + FRs_with_test) / (2 * total_FRs).

    Counts file/test refs found either directly in each FR body or in the
    corresponding Traceability Matrix row. Supports both bare and backtick-
    wrapped references. Returns 0.0--1.0.
    """
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


def _score_assertion_coverage(
    content: str,
    fr_sections: list[tuple[str, str]],
) -> float:
    """Score FRs_with_assertion / total_FRs.

    An FR "has an assertion" when it contains explicit assertion syntax
    (fenced `````assertions```` blocks or assertion list items), not when it
    merely mentions assertion keywords in prose. Returns 0.0--1.0.
    """
    if not fr_sections:
        return 0.0

    frs_with_assertion = sum(
        1 for _name, body in fr_sections if _has_assertion_evidence(body)
    )
    return frs_with_assertion / len(fr_sections)


# ---------------------------------------------------------------------------
# Dimension Scorers
# ---------------------------------------------------------------------------


def score_section_density(
    section_name: str,
    section_body: str,
) -> SectionScore:
    """Score the content density of a single PRD section.

    Args:
        section_name: Name of the section.
        section_body: Raw markdown body of the section.

    Returns:
        SectionScore with density ratio and line counts.
    """
    lines = section_body.split("\n")
    total = len(lines)

    substantive = 0
    placeholder = 0
    for line in lines:
        if _is_substantive_line(line):
            substantive += 1
        elif _PLACEHOLDER_RE.match(line) or (line.strip().startswith("<!--") and line.strip().endswith("-->")):
            placeholder += 1

    density = substantive / max(total, 1)
    return SectionScore(
        section_name=section_name,
        density=density,
        substantive_lines=substantive,
        total_lines=total,
        placeholder_lines=placeholder,
    )


def score_content_density(
    content: str,
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Content Density dimension (25 points max).

    Computes per-section density and aggregates via weighted average.
    Problem Statement and Functional Requirements get 2x weight;
    Traceability Matrix gets 1.5x weight.

    Args:
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        DimensionScore for content density.
    """
    _config = config or get_config()
    max_score = _config.validation_density_weight

    sections = _parse_section_content(content)
    if not sections:
        return DimensionScore(
            name="content_density",
            score=0.0,
            max_score=max_score,
            details={"section_count": 0},
        )

    section_scores: list[SectionScore] = []
    weighted_sum = 0.0
    weight_total = 0.0
    section_weights = _get_section_weights(_config)

    for name, body in sections:
        ss = score_section_density(name, body)
        section_scores.append(ss)
        weight = section_weights.get(name, _config.density_weight_default)
        weighted_sum += ss.density * weight
        weight_total += weight

    avg_density = weighted_sum / max(weight_total, 1.0)
    score = avg_density * max_score

    return DimensionScore(
        name="content_density",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "avg_density": round(avg_density, 4),
            "sections_scored": len(section_scores),
        },
    )


def score_structural_completeness(
    frontmatter: dict[str, object],
    sections: list[str],
    config: TRWConfig | None = None,
    category: str | None = None,
    content: str | None = None,
) -> DimensionScore:
    """Score the Structural Completeness dimension (15 points max).

    Checks: category-appropriate sections present, required frontmatter
    fields, confidence scores present (PRD-CORE-080-FR05).

    The expected section count is derived from the PRD's ``category``
    field in frontmatter via the category-to-template-variant mapping.
    Unknown or missing categories default to the 12-section Feature
    template for backward compatibility.

    For AI/LLM/agentic PRDs, also scores AI/agentic operational subsections
    in section 7 ("AI/LLM Operational Sections"): Data/Context Provenance,
    Failure Modes, Human Oversight, Evaluation Plan, Release Gate,
    Monitoring Plan, and Risk Register By Failure Class.

    Args:
        frontmatter: Parsed YAML frontmatter.
        sections: List of section heading names found.
        config: Optional config for weight override.
        category: Optional explicit category override. When ``None``,
            extracted from ``frontmatter["category"]``.
        content: Full PRD markdown content. Required for structural scoring.

    Returns:
        DimensionScore for structural completeness.
    """
    _config = config or get_config()
    max_score = _config.validation_structure_weight

    # Resolve category: explicit param > frontmatter field > default (feature=12)
    resolved_category = category or str(frontmatter.get("category", ""))
    required_sections = get_required_sections(resolved_category)

    # Section coverage: how many of the category-specific expected sections are present
    expected = len(required_sections)
    found = min(len(sections), expected)
    section_ratio = found / expected

    # Frontmatter field coverage
    required_fm_fields = ["id", "title", "version", "status", "priority"]
    fm_present = sum(1 for f in required_fm_fields if frontmatter.get(f))
    fm_ratio = fm_present / len(required_fm_fields)

    # Confidence scores present
    confidence = frontmatter.get("confidence", {})
    confidence_fields = [
        "implementation_feasibility",
        "requirement_clarity",
        "estimate_confidence",
    ]
    conf_present = 0
    if isinstance(confidence, dict):
        conf_present = sum(1 for f in confidence_fields if f in confidence)
    conf_ratio = conf_present / len(confidence_fields)

    subsection_ratio = 1.0
    matched_subsections = 0
    expected_subsections = 0
    if content is not None:
        from trw_mcp.state.validation.template_variants import get_variant_for_category

        variant = get_variant_for_category(resolved_category)
        required_subsections = _REQUIRED_SUBSECTIONS_BY_VARIANT.get(variant, [])
        expected_subsections = len(required_subsections)
        if required_subsections:
            present_subsections = _extract_subheadings(content)
            matched_subsections = sum(1 for name in required_subsections if name in present_subsections)
            subsection_ratio = matched_subsections / expected_subsections

    # AI/LLM/agentic detection and operational sections scoring (PRD-QUAL-055)
    ai_operational_sections_found = 0
    ai_operational_sections_expected = 7
    ai_section_keywords = [
        "Data / Context Provenance",
        "Failure Modes",
        "Safe Degradation",
        "Human Oversight",
        "Escalation",
        "Evaluation Plan",
        "Release Gate",
        "Monitoring Plan",
        "Risk Register",
        "Failure Class",
    ]

    ai_operational_section_found = False
    if content is not None:
        ai_operational_section_found = _is_ai_agentic_prd(frontmatter, content)
        if ai_operational_section_found:
            present_subsections = _extract_subheadings(content)
            ai_operational_sections_found = sum(
                1 for kw in ai_section_keywords if any(kw.lower() in ss.lower() for ss in present_subsections)
            )
            subsection_ratio = (subsection_ratio * 0.75) + (
                ai_operational_sections_found / ai_operational_sections_expected * 0.25
            )

    # Weighted: sections 35%, frontmatter 25%, confidence 15%, required subsections 25%
    composite = section_ratio * 0.35 + fm_ratio * 0.25 + conf_ratio * 0.15 + subsection_ratio * 0.25
    score = composite * max_score

    details: dict[str, object] = {
        "sections_found": found,
        "sections_expected": expected,
        "frontmatter_fields": fm_present,
        "confidence_fields": conf_present,
        "required_subsections_found": matched_subsections,
        "required_subsections_expected": expected_subsections,
    }
    if ai_operational_section_found:
        details["ai_operational_sections_found"] = ai_operational_sections_found
        details["ai_operational_sections_expected"] = ai_operational_sections_expected
        details["ai_section_detected"] = True

    return DimensionScore(
        name="structural_completeness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
    )


def score_traceability_v2(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
    project_root: Path | None = None,
) -> DimensionScore:
    """Score the Traceability dimension (20 points max).

    Checks: traceability link population, traceability matrix row quality.

    Args:
        frontmatter: Parsed YAML frontmatter.
        content: Full PRD markdown content.
        config: Optional config for weight override.
        project_root: Optional absolute path to project root for grounding checks.

    Returns:
        DimensionScore for traceability.
    """
    _config = config or get_config()
    max_score = _config.validation_traceability_weight

    # Check traceability fields in frontmatter
    populated_fields = _count_populated_trace_fields(frontmatter.get("traceability", {}))
    field_ratio = populated_fields / 3
    matrix_score, proof_score = _score_traceability_matrix(content)

    behavior_switch_rows = _count_table_rows(content, "Behavior Switch Matrix")
    behavior_switch_score = min(behavior_switch_rows / max(len(re.findall(r"FR\d+", content)), 1), 1.0)

    # AI/LLM(agentic evaluation, release, monitoring evidence scoring (PRD-QUAL-055)
    ai_operational_evidence_detected = _is_ai_agentic_prd(frontmatter, content)

    ai_evaluation_score = 0.0
    ai_release_score = 0.0
    ai_monitoring_score = 0.0
    if ai_operational_evidence_detected:
        ai_evaluation_score, ai_release_score, ai_monitoring_score = _score_ai_operational_evidence(content)

    ai_operational_evidence_score = (ai_evaluation_score + ai_release_score + ai_monitoring_score) / 3

    # Composite: field population 40%, matrix quality 35%, proof coverage 15%,
    # switch-matrix coverage 10%. AI operational evidence adds 10% weight for AI AGentic PRDs.
    composite = field_ratio * 0.40 + matrix_score * 0.35 + proof_score * 0.15 + behavior_switch_score * 0.10
    if ai_operational_evidence_detected:
        composite = composite * 0.90 + ai_operational_evidence_score * 0.10
    score = composite * max_score

    details: dict[str, object] = {
        "populated_fields": populated_fields,
        "field_ratio": round(field_ratio, 4),
        "matrix_score": round(matrix_score, 4),
        "proof_score": round(proof_score, 4),
        "behavior_switch_score": round(behavior_switch_score, 4),
    }
    if ai_operational_evidence_detected:
        details["ai_evaluation_score"] = round(ai_evaluation_score, 4)
        details["ai_release_score"] = round(ai_release_score, 4)
        details["ai_monitoring_score"] = round(ai_monitoring_score, 4)
        details["ai_operational_evidence_score"] = round(ai_operational_evidence_score, 4)
        details["ai_operational_evidence_detected"] = True

    # PRD-QUAL-056-FR01/FR02: File path and assertion coverage sub-dimensions
    fr_sections = _extract_fr_sections(content)
    file_path_cov = _score_file_path_coverage(content, fr_sections)
    assertion_cov = _score_assertion_coverage(content, fr_sections)

    details["file_path_coverage"] = round(file_path_cov, 4)
    details["assertion_coverage"] = round(assertion_cov, 4)

    # Additive bonus: file paths and assertions improve the score but their
    # absence does not penalize (backward compat per NFR01). The 15% ceiling
    # is high enough that partial concrete coverage beats placeholder-only
    # traceability, while still keeping matrix/proof coverage as the primary driver.
    coverage_bonus = (file_path_cov * 0.5 + assertion_cov * 0.5) * 0.15 * max_score
    score = min(score + coverage_bonus, max_score)

    # PRD-QUAL-063: Filesystem Grounding Penalty
    if project_root is not None:
        penalty_mult, hallucinated = compute_grounding_penalty(content, project_root)
        if hallucinated:
            score *= penalty_mult
            details["grounding_penalty_mult"] = round(penalty_mult, 4)
            details["hallucinated_paths"] = len(hallucinated)

    # Suggestions when coverage is low
    suggestions: list[str] = []
    if project_root is not None and hallucinated:
        suggestions.append(f"Remove or fix {len(hallucinated)} non-existent file paths (e.g. {hallucinated[0]}) to improve technical grounding.")
    if file_path_cov < 0.7:
        suggestions.append(
            "Add implementation and test file paths to FR acceptance "
            "criteria for first-pass audit compliance"
        )
    if assertion_cov < 0.5:
        suggestions.append(
            "Add machine-verifiable assertions (grep_present/grep_absent) "
            "to FRs for automated audit pre-flight"
        )
    if suggestions:
        details["suggestions"] = suggestions

    return DimensionScore(
        name="traceability",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
    )


def score_implementation_readiness(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
    project_root: Path | None = None,
) -> DimensionScore:
    """Score execution-readiness signals distinct from raw prose density.

    Rewards concrete planning evidence such as control points, behavior switches,
    key files, verification tests, and completion/migration semantics. The
    scoring is variant-aware so FIX and RESEARCH PRDs are not penalized for
    missing feature-only scaffolding.
    """
    _config = config or get_config()
    max_score = _config.validation_implementation_readiness_weight
    if not content:
        return DimensionScore(
            name="implementation_readiness",
            score=0.0,
            max_score=max_score,
            details={"variant": "feature"},
        )

    category = str(frontmatter.get("category", ""))
    variant = get_variant_for_category(category)
    fr_sections = _extract_fr_sections(content)
    fr_count = _count_planned_requirements(content, fr_sections)
    impl_refs = _count_impl_refs(content)
    test_refs = _count_test_refs(content)
    verification_commands = _count_verification_commands(content)

    # Pre-compute subheadings once (DRY — avoids redundant regex scans
    # across all variant branches that check for named subheadings).
    present_subheadings = _extract_subheadings(content)

    completion_ratio = (
        sum(
            1
            for heading in (
                "Completion Evidence (Definition of Done)",
                "Migration / Backward Compatibility",
            )
            if heading in present_subheadings
        )
        / 2
    )

    details: dict[str, object] = {
        "variant": variant,
        "fr_count": fr_count,
        "implementation_refs": impl_refs,
        "test_refs": test_refs,
        "verification_commands": verification_commands,
        "completion_ratio": round(completion_ratio, 4),
    }

    if variant in {"feature", "infrastructure"}:
        control_point_rows = _count_table_rows(content, "Primary Control Points")
        behavior_switch_rows = _count_table_rows(content, "Behavior Switch Matrix")
        key_files_rows = _count_table_rows(content, "Key Files")
        test_subsections = (
            "Unit Tests",
            "Integration Tests",
            "Acceptance Tests",
            "Regression Tests",
            "Negative / Fallback Tests",
        )
        test_subsection_ratio = sum(
            1 for heading in test_subsections if heading in present_subheadings
        ) / len(test_subsections)
        control_ratio = min(control_point_rows / fr_count, 1.0)
        behavior_switch_ratio = min(behavior_switch_rows / fr_count, 1.0)
        file_map_ratio = min(max(key_files_rows, impl_refs) / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        verification_ratio = min(verification_commands / fr_count, 1.0)
        test_plan_ratio = (test_subsection_ratio * 0.5) + (test_ref_ratio * 0.3) + (verification_ratio * 0.2)
        completion_ratio = (completion_ratio * 0.8) + (verification_ratio * 0.2)
        composite = (
            control_ratio * 0.20
            + behavior_switch_ratio * 0.20
            + file_map_ratio * 0.20
            + test_plan_ratio * 0.25
            + completion_ratio * 0.15
        )
        details.update(
            {
                "control_point_rows": control_point_rows,
                "behavior_switch_rows": behavior_switch_rows,
                "key_files_rows": key_files_rows,
                "control_ratio": round(control_ratio, 4),
                "behavior_switch_ratio": round(behavior_switch_ratio, 4),
                "file_map_ratio": round(file_map_ratio, 4),
                "test_subsection_ratio": round(test_subsection_ratio, 4),
                "test_ref_ratio": round(test_ref_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
                "test_plan_ratio": round(test_plan_ratio, 4),
            }
        )
    elif variant == "fix":
        root_cause_ratio = (
            sum(
                1
                for heading in ("Root Cause", "Contributing Factors", "Fix Verification")
                if heading in present_subheadings
            )
            / 3
        )
        regression_ratio = (
            sum(
                1
                for heading in ("Regression Tests", "Negative / Fallback Tests")
                if heading in present_subheadings
            )
            / 2
        )
        file_map_ratio = min(max(impl_refs, 1 if "Key Files" in present_subheadings else 0) / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        verification_ratio = min(max(test_ref_ratio, verification_commands / fr_count), 1.0)
        completion_ratio = (completion_ratio * 0.8) + (verification_ratio * 0.2)
        composite = (
            root_cause_ratio * 0.30
            + regression_ratio * 0.20
            + file_map_ratio * 0.20
            + verification_ratio * 0.15
            + completion_ratio * 0.15
        )
        details.update(
            {
                "root_cause_ratio": round(root_cause_ratio, 4),
                "regression_ratio": round(regression_ratio, 4),
                "file_map_ratio": round(file_map_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
            }
        )
    else:
        # Research variant
        present_subheadings_lower = {sub.lower() for sub in present_subheadings}
        research_ratio = (
            sum(
                1
                for heading in ("Approach", "Data Sources", "Evaluation Criteria")
                if any(heading.lower() in sub for sub in present_subheadings_lower)
            )
            / 3
        )
        evidence_ratio = min((impl_refs + test_refs + verification_commands) / 3, 1.0)
        composite = (research_ratio * 0.65) + (evidence_ratio * 0.20) + (completion_ratio * 0.15)
        details.update(
            {
                "research_ratio": round(research_ratio, 4),
                "evidence_ratio": round(evidence_ratio, 4),
            }
        )

    score = composite * max_score

    # PRD-QUAL-063: Filesystem Grounding Penalty
    if project_root is not None:
        penalty_mult, hallucinated = compute_grounding_penalty(content, project_root)
        if hallucinated:
            score *= penalty_mult
            details["grounding_penalty_mult"] = round(penalty_mult, 4)
            details["hallucinated_paths"] = len(hallucinated)

            suggestions: list[str] = details.get("suggestions", []) # type: ignore
            suggestions.append(f"Remove or fix {len(hallucinated)} non-existent file paths (e.g. {hallucinated[0]}) to improve technical grounding.")
            details["suggestions"] = suggestions

    return DimensionScore(
        name="implementation_readiness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
    )
