"""Requirement smell detection engine — 9 categories of PRD quality issues.

Implements PRD-CORE-008-FR04. Each smell is defined as a data-driven
SmellPattern with compiled regex, severity, and suggestion. New smells can
be added by appending to SMELL_PATTERNS without changing scoring logic.

All functions are pure — no side effects, no file I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import DimensionScore, SmellFinding


@dataclass(frozen=True)
class SmellPattern:
    """Compiled smell detection pattern (data-driven registry)."""

    category: str
    pattern: re.Pattern[str]
    severity: str = "warning"
    suggestion: str = ""
    # Sections where this smell is relevant ("*" = all)
    applies_to: tuple[str, ...] = ("*",)


# ---------------------------------------------------------------------------
# 9 Smell Categories — compiled at module load (PRD-CORE-008-NFR01)
# ---------------------------------------------------------------------------

SMELL_PATTERNS: list[SmellPattern] = [
    # 1. Vague terms
    SmellPattern(
        category="vague_terms",
        pattern=re.compile(
            r"\b(?:fast|quick|efficient|easy|simple|intuitive|robust|scalable"
            r"|flexible|user-friendly|adequate|sufficient|as\s+needed"
            r"|as\s+appropriate|various|multiple|many|etc\.|and\s+so\s+on)\b",
            re.IGNORECASE,
        ),
        severity="warning",
        suggestion="Replace with a specific, measurable term (e.g., '<500ms' instead of 'fast').",
    ),
    # 2. Passive voice
    SmellPattern(
        category="passive_voice",
        pattern=re.compile(
            r"\b(?:is|are|was|were|been|being)\s+\w+(?:ed|en)\b",
            re.IGNORECASE,
        ),
        severity="info",
        suggestion="Consider rewriting in active voice with a clear actor.",
    ),
    # 3. Uncertain language
    SmellPattern(
        category="uncertain_language",
        pattern=re.compile(
            r"\b(?:should|might|could|possibly|probably|may|perhaps)\b",
            re.IGNORECASE,
        ),
        severity="warning",
        suggestion="Use definitive language — 'shall', 'must', or 'will' for requirements.",
    ),
    # 4. Unbounded scope
    SmellPattern(
        category="unbounded_scope",
        pattern=re.compile(
            r"\b(?:all|every|any|always|never)\b",
            re.IGNORECASE,
        ),
        severity="warning",
        suggestion="Add quantification or context (e.g., 'all active users' instead of 'all').",
    ),
    # 5. Missing actor (FR sections only)
    SmellPattern(
        category="missing_actor",
        pattern=re.compile(
            r"^\s*(?:shall|must|will)\b",
            re.IGNORECASE | re.MULTILINE,
        ),
        severity="error",
        suggestion="Add a subject noun before shall/must/will (e.g., 'The system shall...').",
        applies_to=("Functional Requirements",),
    ),
    # 6. Compound requirements
    SmellPattern(
        category="compound_requirements",
        pattern=re.compile(
            r"\b(?:shall|must|will)\b.{5,}?\b(?:and|or)\b.{0,30}?\b(?:shall|must|will)\b",
            re.IGNORECASE,
        ),
        severity="warning",
        suggestion="Split into separate requirements — each requirement should specify one behavior.",
    ),
    # 7. Dangling references
    SmellPattern(
        category="dangling_references",
        pattern=re.compile(
            r"\b(?:Section\s+\d+|see\s+above|see\s+below|as\s+described\s+in)\b",
            re.IGNORECASE,
        ),
        severity="warning",
        suggestion="Replace with a direct cross-reference (e.g., specific PRD ID or section name).",
    ),
    # 8. Missing quantification
    SmellPattern(
        category="missing_quantification",
        pattern=re.compile(
            r"\b(?:fast|slow|large|small|high|low|significant|minimal|acceptable)\s+"
            r"(?:response|latency|throughput|performance|size|time|delay|overhead)\b",
            re.IGNORECASE,
        ),
        severity="warning",
        suggestion="Add a numeric threshold (e.g., 'response time under 200ms').",
    ),
    # 9. Template-only content
    SmellPattern(
        category="template_only_content",
        pattern=re.compile(
            r"^\s*(?:<!--\s*.*?-->|TBD|TODO|FIXME|N/A|\[.*?\])\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        severity="error",
        suggestion="Replace template placeholder with actual content.",
    ),
]


def detect_smells(
    content: str,
    section_name: str = "*",
) -> list[SmellFinding]:
    """Run all smell patterns against content.

    Args:
        content: Text to analyze (full PRD or single section).
        section_name: Current section name for applies_to filtering.

    Returns:
        List of SmellFinding instances.
    """
    findings: list[SmellFinding] = []
    lines = content.split("\n")

    # Pre-compute which lines are inside code blocks
    in_code_block = False
    code_block_lines: set[int] = set()
    for i, line in enumerate(lines, start=1):
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            code_block_lines.add(i)
            continue
        if in_code_block:
            code_block_lines.add(i)

    for pattern in SMELL_PATTERNS:
        # Filter by applies_to
        if "*" not in pattern.applies_to and section_name not in pattern.applies_to:
            continue

        for i, line in enumerate(lines, start=1):
            # Skip lines inside code blocks
            if i in code_block_lines:
                continue
            # Skip frontmatter delimiter lines and headings
            stripped = line.strip()
            if stripped.startswith("---") or stripped.startswith("#"):
                continue
            # Skip YAML-like lines inside frontmatter
            if stripped.startswith("prd:") or ":" in stripped and stripped.endswith(":"):
                continue

            for match in pattern.pattern.finditer(line):
                findings.append(
                    SmellFinding(
                        category=pattern.category,
                        line_number=i,
                        matched_text=match.group(0)[:80],
                        severity=pattern.severity,
                        suggestion=pattern.suggestion,
                    )
                )

    return findings


def detect_smells_by_section(
    content: str,
) -> list[SmellFinding]:
    """Run smell detection section-aware against full PRD content.

    Parses sections and applies section-specific filters for
    patterns like missing_actor (FR sections only).

    Args:
        content: Full PRD markdown content.

    Returns:
        List of SmellFinding with line numbers relative to full content.
    """
    from trw_mcp.state.validation import _parse_section_content

    # Global pass for non-section-specific patterns
    findings = detect_smells(content, section_name="*")

    # Section-specific pass
    sections = _parse_section_content(content)
    for name, body in sections:
        if name == "Functional Requirements":
            section_findings = detect_smells(body, section_name=name)
            # Deduplicate by checking if we already have the same finding
            existing = {(f.category, f.matched_text) for f in findings}
            for f in section_findings:
                if (f.category, f.matched_text) not in existing:
                    findings.append(f)

    return findings


def score_smells(
    content: str,
    config: TRWConfig | None = None,
) -> tuple[DimensionScore, list[SmellFinding]]:
    """Score the Smell Detection dimension.

    Higher score = fewer smells (cleaner PRD). The score starts at
    max_score and is reduced by deductions per finding severity:
    - error: -1.5 points
    - warning: -0.5 points
    - info: -0.1 points

    Args:
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        Tuple of (DimensionScore, list of SmellFinding).
    """
    _config = config or TRWConfig()
    max_score = _config.validation_smell_weight

    findings = detect_smells_by_section(content)

    # Deduction per severity
    deductions = {"error": 1.5, "warning": 0.5, "info": 0.1}
    total_deduction = 0.0
    for f in findings:
        total_deduction += deductions.get(f.severity, 0.5)

    score = max(0.0, max_score - total_deduction)

    dim = DimensionScore(
        name="smell_score",
        score=round(score, 2),
        max_score=max_score,
        details={
            "total_findings": len(findings),
            "error_count": sum(1 for f in findings if f.severity == "error"),
            "warning_count": sum(1 for f in findings if f.severity == "warning"),
            "info_count": sum(1 for f in findings if f.severity == "info"),
        },
    )

    return dim, findings
