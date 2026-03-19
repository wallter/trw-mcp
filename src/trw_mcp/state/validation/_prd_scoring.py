"""PRD quality scoring — metric computation for content density, structure, traceability.

Extracted from prd_quality.py to separate scoring (numeric metric computation)
from validation (pass/fail gate checks). All functions here compute and return
DimensionScore / SectionScore values without making pass/fail decisions.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    SectionScore,
)
from trw_mcp.state.validation.template_variants import get_required_sections

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
) -> DimensionScore:
    """Score the Structural Completeness dimension (15 points max).

    Checks: category-appropriate sections present, required frontmatter
    fields, confidence scores present (PRD-CORE-080-FR05).

    The expected section count is derived from the PRD's ``category``
    field in frontmatter via the category-to-template-variant mapping.
    Unknown or missing categories default to the 12-section Feature
    template for backward compatibility.

    Args:
        frontmatter: Parsed YAML frontmatter.
        sections: List of section heading names found.
        config: Optional config for weight override.
        category: Optional explicit category override. When ``None``,
            extracted from ``frontmatter["category"]``.

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

    # Weighted: sections 50%, frontmatter 30%, confidence 20%
    composite = section_ratio * 0.5 + fm_ratio * 0.3 + conf_ratio * 0.2
    score = composite * max_score

    return DimensionScore(
        name="structural_completeness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "sections_found": found,
            "sections_expected": expected,
            "frontmatter_fields": fm_present,
            "confidence_fields": conf_present,
        },
    )


def score_traceability_v2(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Traceability dimension (20 points max).

    Checks: traceability link population, traceability matrix row quality.

    Args:
        frontmatter: Parsed YAML frontmatter.
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        DimensionScore for traceability.
    """
    _config = config or get_config()
    max_score = _config.validation_traceability_weight

    # Check traceability fields in frontmatter
    trace_data = frontmatter.get("traceability", {})
    trace_fields = ["implements", "depends_on", "enables"]
    populated_fields = 0
    if isinstance(trace_data, dict):
        for field in trace_fields:
            val = trace_data.get(field, [])
            if isinstance(val, list) and val:
                populated_fields += 1

    field_ratio = populated_fields / len(trace_fields)

    # Check traceability matrix content
    matrix_score = 0.0
    if "Traceability Matrix" in content:
        matrix_section = content.split("Traceability Matrix")[-1]
        # Count rows with implementation file references
        impl_refs = re.findall(r"`[\w/]+\.\w+[:\w]*`", matrix_section)
        test_refs = _TEST_REF_RE.findall(matrix_section)
        # Count FR references in matrix
        fr_refs = re.findall(r"FR\d+", matrix_section)

        ref_count = len(impl_refs) + len(test_refs)
        if fr_refs:
            matrix_score = min(ref_count / max(len(fr_refs), 1), 1.0)

    # Composite: field population 40%, matrix quality 60%
    composite = field_ratio * 0.4 + matrix_score * 0.6
    score = composite * max_score

    return DimensionScore(
        name="traceability",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "populated_fields": populated_fields,
            "field_ratio": round(field_ratio, 4),
            "matrix_score": round(matrix_score, 4),
        },
    )
