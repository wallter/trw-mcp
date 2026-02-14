"""Grooming plan generation — pure-function analysis for PRD grooming.

PRD-CORE-011-FR06: Parses PRD content, identifies placeholder sections,
scores density, maps research topics, and estimates iterations.
"""

from __future__ import annotations

import re

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.planning import (
    GroomingPlan,
    ResearchScope,
    SectionAnalysis,
    SectionStatus,
)
from trw_mcp.state.validation import (
    _EXPECTED_SECTION_NAMES,
    _is_substantive_line,
    _parse_section_content,
    validate_prd_quality_v2,
)

logger = structlog.get_logger()

# Section-to-research-topic mapping
_SECTION_RESEARCH_MAP: dict[str, list[str]] = {
    "Problem Statement": [
        "existing codebase patterns",
        "user pain points",
        "current workarounds",
    ],
    "Goals & Non-Goals": [
        "project scope boundaries",
        "related feature scope",
    ],
    "User Stories": [
        "user personas and workflows",
        "acceptance criteria patterns",
    ],
    "Functional Requirements": [
        "implementation constraints",
        "API surface design",
        "data model requirements",
        "integration points",
    ],
    "Non-Functional Requirements": [
        "performance baselines",
        "security requirements",
        "scalability considerations",
    ],
    "Technical Approach": [
        "codebase architecture",
        "dependency graph",
        "existing patterns to follow",
    ],
    "Test Strategy": [
        "existing test patterns",
        "test infrastructure",
    ],
    "Rollout Plan": [
        "deployment process",
        "rollback procedures",
    ],
    "Success Metrics": [
        "measurable outcomes",
        "baseline metrics",
    ],
    "Dependencies & Risks": [
        "upstream dependencies",
        "known risks",
        "blocking issues",
    ],
    "Open Questions": [
        "unresolved technical decisions",
    ],
    "Traceability Matrix": [
        "requirement-to-code mapping",
        "test coverage mapping",
    ],
}

# Estimated research queries per section type
_SECTION_QUERY_ESTIMATES: dict[str, int] = {
    "Problem Statement": 3,
    "Goals & Non-Goals": 2,
    "User Stories": 2,
    "Functional Requirements": 5,
    "Non-Functional Requirements": 2,
    "Technical Approach": 4,
    "Test Strategy": 2,
    "Rollout Plan": 1,
    "Success Metrics": 1,
    "Dependencies & Risks": 2,
    "Open Questions": 1,
    "Traceability Matrix": 2,
}


def _extract_prd_id(content: str) -> str:
    """Extract PRD ID from content frontmatter or heading.

    Args:
        content: Full PRD markdown content.

    Returns:
        PRD ID string, or 'UNKNOWN' if not found.
    """
    # Try frontmatter id field
    id_match = re.search(r"^\s*id:\s*(PRD-\S+)", content, re.MULTILINE)
    if id_match:
        return id_match.group(1)
    # Try heading
    heading_match = re.search(r"^#\s+(PRD-\S+):", content, re.MULTILINE)
    if heading_match:
        return heading_match.group(1)
    return "UNKNOWN"


def _extract_background_keywords(content: str) -> list[str]:
    """Extract keywords from the PRD Background/Problem Statement section.

    Used to derive research topics for sections that need grooming.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of extracted keywords.
    """
    keywords: list[str] = []

    # Extract Background subsection content
    bg_match = re.search(
        r"###\s+Background\s*\n(.*?)(?=###|\n##\s|\Z)",
        content,
        re.DOTALL,
    )
    if bg_match:
        bg_text = bg_match.group(1)
    else:
        # Fall back to Problem Statement section
        ps_match = re.search(
            r"##\s+\d+\.\s+Problem Statement\s*\n(.*?)(?=\n##\s|\Z)",
            content,
            re.DOTALL,
        )
        bg_text = ps_match.group(1) if ps_match else ""

    if not bg_text:
        return keywords

    # Extract technical terms (CamelCase, snake_case, hyphenated-terms)
    technical_terms = re.findall(
        r"\b(?:[A-Z][a-z]+(?:[A-Z][a-z]+)+|[a-z]+_[a-z_]+|[a-z]+-[a-z-]+)\b",
        bg_text,
    )
    keywords.extend(technical_terms[:10])

    # Extract quoted terms
    quoted = re.findall(r"`([^`]+)`", bg_text)
    keywords.extend(quoted[:5])

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for kw in keywords:
        lower = kw.lower()
        if lower not in seen:
            seen.add(lower)
            unique.append(kw)

    return unique


def _analyze_section(
    section_name: str,
    section_body: str,
    section_number: int,
    background_keywords: list[str],
    placeholder_threshold: float = 0.10,
    partial_threshold: float = 0.20,
) -> SectionAnalysis:
    """Analyze a single PRD section for grooming needs.

    Args:
        section_name: Name of the section.
        section_body: Raw markdown body of the section.
        section_number: 1-based section index.
        background_keywords: Keywords from the Background section.
        placeholder_threshold: Density below which section is placeholder.
        partial_threshold: Density below which section is partial.

    Returns:
        SectionAnalysis with density, status, and research topics.
    """
    lines = section_body.split("\n")
    total = len(lines)
    substantive = sum(1 for line in lines if _is_substantive_line(line))

    density = substantive / max(total, 1)

    # Classify status using config-driven thresholds
    if density < placeholder_threshold:
        status = SectionStatus.PLACEHOLDER
    elif density < partial_threshold:
        status = SectionStatus.PARTIAL
    else:
        status = SectionStatus.COMPLETE

    # Build research topics for sections needing work
    research_topics: list[str] = []
    if status != SectionStatus.COMPLETE:
        base_topics = _SECTION_RESEARCH_MAP.get(section_name, [])
        research_topics.extend(base_topics)

        # Augment with background keywords for relevant sections
        if background_keywords and section_name in (
            "Functional Requirements",
            "Technical Approach",
            "Test Strategy",
        ):
            for kw in background_keywords[:3]:
                research_topics.append(f"{section_name.lower()} for {kw}")

    return SectionAnalysis(
        section_name=section_name,
        section_number=section_number,
        status=status,
        density=round(density, 4),
        substantive_lines=substantive,
        total_lines=total,
        research_topics=research_topics,
    )


def _estimate_iterations(placeholder_count: int) -> int:
    """Estimate the number of validate-fix iterations needed.

    Follows PRD-CORE-011-FR07 heuristic:
    - 1-3 placeholder sections: 1 iteration
    - 4-7 placeholder sections: 2 iterations
    - 8-12 placeholder sections: 3 iterations

    Args:
        placeholder_count: Number of sections needing work.

    Returns:
        Estimated iteration count (1-3).
    """
    if placeholder_count <= 3:
        return 1
    if placeholder_count <= 7:
        return 2
    return 3


def generate_grooming_plan(
    content: str,
    prd_path: str,
    config: TRWConfig | None = None,
    max_iterations: int | None = None,
    target_completeness: float | None = None,
    research_scope: str | None = None,
) -> GroomingPlan:
    """Generate a structured grooming plan for a PRD.

    Pure function that analyzes PRD content and produces a plan
    without modifying any files.

    Args:
        content: Full PRD markdown content.
        prd_path: Absolute path to the PRD file.
        config: Optional TRW configuration.
        max_iterations: Override for maximum grooming iterations.
        target_completeness: Override for target completeness score.
        research_scope: Override for research depth.

    Returns:
        GroomingPlan with section analysis and grooming estimates.
    """
    _config = config or TRWConfig()

    # Resolve parameters from config defaults
    _max_iter = max_iterations if max_iterations is not None else _config.grooming_max_iterations
    _target = target_completeness if target_completeness is not None else _config.grooming_target_completeness
    _scope_str = research_scope if research_scope is not None else _config.grooming_research_scope

    # Validate research scope
    try:
        _scope = ResearchScope(_scope_str)
    except ValueError:
        _scope = ResearchScope.FULL

    # Extract PRD ID
    prd_id = _extract_prd_id(content)

    # Extract background keywords for research topic augmentation
    bg_keywords = _extract_background_keywords(content)

    # Parse sections
    parsed_sections = _parse_section_content(content)

    # Build section number mapping
    section_number_map: dict[str, int] = {}
    for i, name in enumerate(_EXPECTED_SECTION_NAMES, 1):
        section_number_map[name] = i

    # Analyze each section
    sections_needing_work: list[SectionAnalysis] = []
    sections_complete: list[str] = []

    analyzed_names: set[str] = set()

    # Read density thresholds from config (Zero Magic)
    placeholder_threshold = _config.grooming_placeholder_density_threshold
    partial_threshold = _config.grooming_partial_density_threshold

    for section_name, section_body in parsed_sections:
        sec_num = section_number_map.get(section_name, 0)
        if sec_num == 0:
            # Non-standard section; skip
            continue

        analysis = _analyze_section(
            section_name, section_body, sec_num, bg_keywords,
            placeholder_threshold=placeholder_threshold,
            partial_threshold=partial_threshold,
        )
        analyzed_names.add(section_name)

        if analysis.status == SectionStatus.COMPLETE:
            sections_complete.append(section_name)
        else:
            sections_needing_work.append(analysis)

    # Flag missing sections as placeholder
    for i, name in enumerate(_EXPECTED_SECTION_NAMES, 1):
        if name not in analyzed_names:
            sections_needing_work.append(
                SectionAnalysis(
                    section_name=name,
                    section_number=i,
                    status=SectionStatus.PLACEHOLDER,
                    density=0.0,
                    substantive_lines=0,
                    total_lines=0,
                    research_topics=_SECTION_RESEARCH_MAP.get(name, []),
                )
            )

    # Sort by section number
    sections_needing_work.sort(key=lambda s: s.section_number)

    # Estimate research queries
    total_queries = sum(
        _SECTION_QUERY_ESTIMATES.get(s.section_name, 1)
        for s in sections_needing_work
    )

    # Estimate iterations
    placeholder_count = len(sections_needing_work)
    estimated_iters = _estimate_iterations(placeholder_count)

    # Compute current completeness from section analysis
    total_sections = len(_EXPECTED_SECTION_NAMES)
    complete_count = len(sections_complete)
    current_completeness = complete_count / max(total_sections, 1)

    # Get V2 quality score
    current_total_score = 0.0
    current_tier = "skeleton"
    try:
        v2_result = validate_prd_quality_v2(content, _config)
        current_total_score = v2_result.total_score
        current_tier = str(v2_result.quality_tier)
    except (ValueError, KeyError, AttributeError) as exc:
        logger.debug("v2_validation_unavailable", error=str(exc))

    plan = GroomingPlan(
        prd_id=prd_id,
        prd_path=prd_path,
        current_completeness=round(current_completeness, 4),
        current_total_score=round(current_total_score, 2),
        current_quality_tier=current_tier,
        target_completeness=_target,
        sections_needing_work=sections_needing_work,
        sections_complete=sections_complete,
        estimated_research_queries=total_queries,
        estimated_iterations=estimated_iters,
        max_iterations=_max_iter,
        research_scope=_scope,
    )

    logger.info(
        "grooming_plan_generated",
        prd_id=prd_id,
        sections_needing_work=len(sections_needing_work),
        sections_complete=len(sections_complete),
        estimated_iterations=estimated_iters,
    )

    return plan
