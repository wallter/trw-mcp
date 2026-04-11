"""PRD quality validation — pass/fail gate checks, tier classification, suggestions.

Extracted from prd_quality.py to separate validation (gate checks, status
integrity, quality tiers) from scoring (numeric metric computation in
_prd_scoring.py).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import (
    DimensionScore,
    ImprovementSuggestion,
    PRDQualityGates,
    QualityTier,
    ValidationFailure,
    ValidationResult,
)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Quick Reference prose status line pattern (PRD-FIX-056-FR01)
# Matches: - **Status**: <word>  (in the Quick Reference block)
_PROSE_STATUS_RE = re.compile(r"-\s*\*\*Status\*\*:\s*(\w+)", re.IGNORECASE)

# FR section header pattern -- e.g. "### PRD-CORE-008-FR01: Title"
_FR_SECTION_RE = re.compile(r"^###\s+[\w-]+-FR\d+:", re.MULTILINE)

# FR-level status annotation pattern (PRD-FIX-056-FR04)
_FR_STATUS_RE = re.compile(r"\*\*Status\*\*:\s*(active|deferred|superseded|done)", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Grade map
# ---------------------------------------------------------------------------

_GRADE_MAP: dict[QualityTier, str] = {
    QualityTier.APPROVED: "A",
    QualityTier.REVIEW: "B",
    QualityTier.DRAFT: "D",
    QualityTier.SKELETON: "F",
}


# ---------------------------------------------------------------------------
# V1 Quality Validation
# ---------------------------------------------------------------------------


def validate_prd_quality(
    frontmatter: dict[str, object],
    sections: list[str],
    gates: PRDQualityGates | None = None,
) -> ValidationResult:
    """Validate a PRD against AARE-F quality gates (V1 -- simple scorer).

    .. deprecated::
        Prefer ``validate_prd_quality_v2()`` which provides the full
        multi-dimension semantic scorer (content density, structural
        completeness, implementation readiness, traceability) and a
        ``total_score`` on a 0-100 scale. This V1
        function is retained for backward compatibility and returns a
        ``completeness_score`` (0.0-1.0) based on frontmatter field presence
        and section count only.

    Args:
        frontmatter: Parsed YAML frontmatter dictionary.
        sections: List of section headings found in the PRD body.
        gates: Quality gate thresholds. Defaults to AARE-F standards.

    Returns:
        ValidationResult with quality scores and any failures.
    """
    _gates = gates or PRDQualityGates()
    failures: list[ValidationFailure] = []

    # Check required frontmatter fields
    required_fields = ["id", "title", "version", "status", "priority"]
    failures.extend(
        ValidationFailure(
            field=f"frontmatter:{field}",
            rule="required_field",
            message=f"Required frontmatter field missing: {field}",
            severity="error",
        )
        for field in required_fields
        if field not in frontmatter or not frontmatter[field]
    )

    # Check for 12 required sections
    expected_section_count = 12
    if len(sections) < expected_section_count:
        failures.append(
            ValidationFailure(
                field="sections",
                rule="section_count",
                message=f"PRD has {len(sections)} sections, expected {expected_section_count}",
                severity="error",
            )
        )

    # Check confidence scores exist
    confidence = frontmatter.get("confidence", {})
    if isinstance(confidence, dict):
        confidence_fields = [
            "implementation_feasibility",
            "requirement_clarity",
            "estimate_confidence",
        ]
        failures.extend(
            ValidationFailure(
                field=f"confidence:{field}",
                rule="confidence_present",
                message=f"Missing confidence score: {field}",
                severity="warning",
            )
            for field in confidence_fields
            if field not in confidence
        )

    # Check traceability
    traceability = frontmatter.get("traceability", {})
    has_traces = False
    if isinstance(traceability, dict):
        for key in ("implements", "depends_on", "enables"):
            val = traceability.get(key, [])
            if isinstance(val, list) and val:
                has_traces = True
                break
    if not has_traces:
        failures.append(
            ValidationFailure(
                field="traceability",
                rule="has_traces",
                message="PRD has no traceability links",
                severity="warning",
            )
        )

    # Calculate scores
    total_checks = len(required_fields) + 3  # sections, confidence, traceability
    error_count = sum(1 for f in failures if f.severity == "error")
    completeness = 1.0 - (error_count / max(total_checks, 1))

    # Traceability coverage: proportion of requirements with traces
    trace_coverage = 1.0 if has_traces else 0.0

    is_valid = (
        completeness >= _gates.completeness_min
        and trace_coverage >= _gates.traceability_coverage_min
        and error_count == 0
    )

    result = ValidationResult(
        valid=is_valid,
        failures=failures,
        completeness_score=completeness,
        traceability_coverage=trace_coverage,
    )

    logger.info(
        "prd_validated",
        valid=is_valid,
        completeness=completeness,
        traceability=trace_coverage,
        failures=len(failures),
    )
    return result


# ---------------------------------------------------------------------------
# Quality Tier Classification
# ---------------------------------------------------------------------------


def classify_quality_tier(
    total_score: float,
    config: TRWConfig | None = None,
) -> QualityTier:
    """Classify a total quality score into a quality tier.

    Args:
        total_score: Score from 0-100.
        config: Optional config for threshold overrides.

    Returns:
        QualityTier enum member.
    """
    _config = config or get_config()
    if total_score >= _config.validation_review_threshold:
        return QualityTier.APPROVED
    if total_score >= _config.validation_draft_threshold:
        return QualityTier.REVIEW
    if total_score >= _config.validation_skeleton_threshold:
        return QualityTier.DRAFT
    return QualityTier.SKELETON


def map_grade(tier: QualityTier) -> str:
    """Map a quality tier to a letter grade.

    Args:
        tier: Quality tier.

    Returns:
        Letter grade: A, B, D, or F.
    """
    return _GRADE_MAP.get(tier, "F")


# ---------------------------------------------------------------------------
# Improvement Suggestions
# ---------------------------------------------------------------------------


def generate_improvement_suggestions(
    dimensions: list[DimensionScore],
    max_suggestions: int = 5,
) -> list[ImprovementSuggestion]:
    """Generate prioritized improvement suggestions for low-scoring dimensions.

    Args:
        dimensions: List of dimension scores.
        max_suggestions: Maximum number of suggestions to return.

    Returns:
        List of suggestions sorted by potential gain descending.
    """
    # Messages only for active (implemented) dimensions.
    # Stub dimensions (smell_score, readability, ears_coverage) are excluded --
    # they have no scorer and will never appear in the dimensions list.
    _messages: dict[str, str] = {
        "content_density": "Add substantive content only where execution evidence is thin -- clarify rationale, proof, or acceptance details instead of inflating prose.",
        "structural_completeness": "Complete missing sections, frontmatter fields, and required subsections so the PRD matches its category contract.",
        "implementation_readiness": "Add executable planning evidence -- primary control points, behavior switches, key files, proof tests, and completion evidence.",
        "traceability": "Add traceability links (implements, depends_on, enables), prove each behavior switch with executable tests, and populate the Traceability Matrix with implementation plus test references.",
    }

    # AI/LLM/agentic operational gates suggestions (PRD-QUAL-055)
    _ai_operational_messages: dict[str, str] = {
        "structural_completeness": "Add AI/LLM/agentic operational sections (Data/Context Provenance, Failure Modes, Human Oversight, Evaluation Plan, Release Gate, Monitoring Plan, Risk Register By Failure Class) when AI/agentic behavior is involved.",
        "implementation_readiness": "Add operational proof for AI/LLM/agentic behavior -- evaluation baselines, release gates, monitoring thresholds, and rollback triggers.",
        "traceability": "Add AI/LLM/agentic operational evidence: evaluation plan with baseline criteria, release gate with rollback triggers, and monitoring plan with signal thresholds when AI/agentic behavior is involved.",
    }

    _thresholds: dict[str, float] = {
        "content_density": 0.50,
        "structural_completeness": 0.70,
        "implementation_readiness": 0.75,
        "traceability": 0.75,
    }
    _dimension_order: dict[str, int] = {
        "implementation_readiness": 0,
        "traceability": 1,
        "structural_completeness": 2,
        "content_density": 3,
    }

    suggestions: list[ImprovementSuggestion] = []
    for dim in dimensions:
        ratio = dim.score / dim.max_score if dim.max_score > 0 else 1.0
        threshold = _thresholds.get(dim.name, 0.7)
        if ratio < threshold:
            potential_gain = dim.max_score - dim.score
            priority = "high" if ratio < max(threshold * 0.5, 0.3) else "medium"

            # Check if AI operational evidence is detected in dimension details
            ai_detected = dim.details.get("ai_operational_evidence_detected", False) or dim.details.get(
                "ai_section_detected", False
            )
            message = (
                _ai_operational_messages.get(dim.name, _messages.get(dim.name, f"Improve {dim.name} score."))
                if ai_detected
                else _messages.get(dim.name, f"Improve {dim.name} score.")
            )

            suggestions.append(
                ImprovementSuggestion(
                    dimension=dim.name,
                    priority=priority,
                    message=message,
                    current_score=round(dim.score, 2),
                    potential_gain=round(potential_gain, 2),
                )
            )

    suggestions.sort(
        key=lambda s: (
            0 if s.priority == "high" else 1,
            _dimension_order.get(s.dimension, 99),
            -s.potential_gain,
        )
    )
    return suggestions[:max_suggestions]


# ---------------------------------------------------------------------------
# Status Integrity Checks (PRD-FIX-056)
# ---------------------------------------------------------------------------


def _check_status_drift(
    frontmatter: dict[str, object],
    content: str,
) -> list[str]:
    """Compare frontmatter status with prose Quick Reference status line.

    FR01 (PRD-FIX-056): If the YAML frontmatter ``status`` field differs from
    the ``- **Status**: {value}`` line in the prose Quick Reference block, a
    warning string is returned describing the drift.

    The comparison is case-insensitive so ``done`` matches ``Done``.
    If no Quick Reference block is found, the check is skipped gracefully.

    Args:
        frontmatter: Parsed YAML frontmatter dict (already flattened).
        content: Full PRD markdown content.

    Returns:
        List of warning strings (empty when no drift detected).
    """
    warnings: list[str] = []

    fm_status = frontmatter.get("status")
    if fm_status is None:
        return warnings

    fm_status_str = str(fm_status).lower()

    # Strip frontmatter block before searching for prose status
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    fm_match = _FRONTMATTER_RE.match(content)
    body = content[fm_match.end() :] if fm_match else content

    match = _PROSE_STATUS_RE.search(body)
    if match is None:
        # No Quick Reference prose block -- skip gracefully (NFR02)
        return warnings

    prose_status_str = match.group(1).lower()

    if fm_status_str != prose_status_str:
        warnings.append(
            f"Status drift: frontmatter status='{fm_status_str}' "
            f"differs from prose Quick Reference status='{prose_status_str}'. "
            "Update the prose '- **Status**:' line to match the frontmatter."
        )

    return warnings


def _check_fr_annotations(content: str) -> list[str]:
    """Check that each FR section has a **Status**: annotation.

    FR04 (PRD-FIX-056): Scans all FR section headers (### *-FRN: ...) in
    the Functional Requirements section and warns for any that lack a
    ``**Status**: active|deferred|superseded|done`` line.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of warning strings (empty when all FRs have annotations).
    """
    warnings: list[str] = []

    # Find the Functional Requirements section body
    fr_section_match = re.search(
        r"##\s+\d+\.\s+Functional Requirements(.*?)(?=^##\s+\d+\.|\Z)",
        content,
        re.DOTALL | re.MULTILINE,
    )
    if not fr_section_match:
        return warnings

    fr_body = fr_section_match.group(1)

    # Find all FR subsection headers in the FR section
    fr_headers = list(_FR_SECTION_RE.finditer(fr_body))
    if not fr_headers:
        return warnings

    # For each FR block, check whether it contains a Status annotation
    for i, header_match in enumerate(fr_headers):
        block_start = header_match.end()
        block_end = fr_headers[i + 1].start() if i + 1 < len(fr_headers) else len(fr_body)
        block_body = fr_body[block_start:block_end]

        if not _FR_STATUS_RE.search(block_body):
            header_text = header_match.group(0).strip()
            warnings.append(
                f"FR annotation missing: '{header_text}' has no '**Status**: active|deferred|superseded|done' line."
            )

    return warnings


def _check_partially_implemented(
    frontmatter: dict[str, object],
) -> list[str]:
    """Warn when a 'done' PRD has partially_implemented_frs listed.

    FR05 (PRD-FIX-056): If the frontmatter status is 'done' and
    ``partially_implemented_frs`` is a non-empty list, emit a warning
    naming the deferred FRs.

    Args:
        frontmatter: Parsed YAML frontmatter dict.

    Returns:
        List of warning strings (empty when not applicable).
    """
    warnings: list[str] = []

    fm_status = str(frontmatter.get("status", "")).lower()
    if fm_status != "done":
        return warnings

    partial_frs = frontmatter.get("partially_implemented_frs", [])
    if not isinstance(partial_frs, list) or not partial_frs:
        return warnings

    fr_list = ", ".join(str(fr) for fr in partial_frs)
    warnings.append(
        f"PRD marked done but has partially implemented FRs: {fr_list}. "
        "Consider leaving status as 'implemented' until all FRs are complete."
    )

    return warnings


# ---------------------------------------------------------------------------
# Sprint Deferral Detection (R-03)
# ---------------------------------------------------------------------------

# Deferral indicator phrases — matched case-insensitively on lines
# that also contain the PRD ID.
_DEFERRAL_PHRASES: tuple[str, ...] = (
    "deferred",
    "not in scope",
    "phase 2",
    "phase 3",
    "explicitly not",
    "out of scope",
)


def _check_sprint_deferral(
    frontmatter: dict[str, object],
    *,
    project_root: Path | None = None,
) -> list[str]:
    """Warn when a 'done' PRD is referenced with deferral language in sprint docs.

    R-03: Scans sprint doc directories for markdown files that mention both
    the PRD ID and a deferral indicator phrase on the same line.  Only runs
    when ``status`` is ``done``.

    Fail-open: any I/O or parsing error returns an empty list.

    Args:
        frontmatter: Parsed YAML frontmatter dict.
        project_root: Project root directory. When ``None``, the check is
            skipped (cannot locate sprint docs).

    Returns:
        List of warning strings (empty when not applicable or on error).
    """
    warnings: list[str] = []

    try:
        fm_status = str(frontmatter.get("status", "")).lower()
        if fm_status != "done":
            return warnings

        prd_id = str(frontmatter.get("id", "")).strip()
        if not prd_id:
            return warnings

        if project_root is None:
            return warnings

        # Directories where sprint docs may live
        sprint_dirs = [
            project_root / "docs" / "requirements-aare-f" / "sprints",
            project_root / "docs" / "requirements-aare-f" / "archive" / "sprints",
        ]

        prd_id_lower = prd_id.lower()

        for sprint_dir in sprint_dirs:
            if not sprint_dir.is_dir():
                continue
            for md_file in sprint_dir.glob("*.md"):
                try:
                    content = md_file.read_text(encoding="utf-8")
                except OSError:
                    continue

                for line in content.splitlines():
                    line_lower = line.lower()
                    if prd_id_lower not in line_lower:
                        continue
                    for phrase in _DEFERRAL_PHRASES:
                        if phrase in line_lower:
                            warnings.append(
                                f"Sprint doc '{md_file.name}' contains deferral language "
                                f"for {prd_id}: '{line.strip()}'. Verify all FRs are "
                                f"implemented or update PRD status to 'partial'."
                            )
                            break  # one warning per line is sufficient
    except Exception:  # justified: fail-open — sprint deferral scan must never raise
        logger.warning("sprint_deferral_check_failed", exc_info=True)

    return warnings


# ---------------------------------------------------------------------------
# V1 Failure Coercion
# ---------------------------------------------------------------------------


def _coerce_v1_failures(raw: object) -> list[ValidationFailure]:
    """Coerce a V1 failures list from a dict into typed ValidationFailure objects.

    Handles both pre-typed ValidationFailure instances and raw dicts.

    Args:
        raw: Failures value from a v1_result dict (may be list or other).

    Returns:
        List of ValidationFailure instances.
    """
    if not isinstance(raw, list):
        return []
    result: list[ValidationFailure] = []
    for item in raw:
        if isinstance(item, ValidationFailure):
            result.append(item)
        elif isinstance(item, dict):
            result.append(
                ValidationFailure(
                    field=str(item.get("field", "")),
                    rule=str(item.get("rule", "")),
                    message=str(item.get("message", "")),
                    severity=str(item.get("severity", "warning")),
                )
            )
    return result
