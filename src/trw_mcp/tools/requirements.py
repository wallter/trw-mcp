"""TRW AARE-F requirements tools — prd_create, prd_validate.

These 2 tools codify the AARE-F Framework v1.1.0 requirements engineering
process as executable MCP tools.
"""

from __future__ import annotations

import re
from datetime import date
from io import StringIO
from pathlib import Path

import structlog
from fastmcp import FastMCP
from ruamel.yaml import YAML

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import (
    EvidenceLevel,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDQualityGates,
    PRDTraceability,
    Priority,
    RiskLevel,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateWriter, model_to_dict
from trw_mcp.state.prd_utils import (
    _FRONTMATTER_RE,
    extract_prd_refs,
    extract_sections as _extract_sections,
    next_prd_sequence,
)
from trw_mcp.state.validation import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTIONS,
    validate_prd_quality_v2,
)

logger = structlog.get_logger()

_config = get_config()
_writer = FileStateWriter()

# Priority → base confidence score mapping
_PRIORITY_CONFIDENCE: dict[str, float] = {
    "P0": 0.9, "P1": 0.7, "P2": 0.6, "P3": 0.5,
}


def register_requirements_tools(server: FastMCP) -> None:
    """Register AARE-F requirements tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    def trw_prd_create(
        input_text: str,
        category: str = "CORE",
        priority: str = "P1",
        title: str = "",
        sequence: int = 1,
        risk_level: str = "",
    ) -> dict[str, object]:
        """Generate an AARE-F compliant PRD from a feature request or requirements text.

        Args:
            input_text: Feature request, requirements, or description to base the PRD on.
            category: PRD category (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX).
            priority: Priority level (P0, P1, P2, P3).
            title: PRD title. Auto-generated from input if not provided.
            sequence: Sequence number for PRD ID. Auto-increments from existing PRDs when default (1).
            risk_level: Optional risk level (critical, high, medium, low). Derived from priority if not set.
        """
        # Validate priority
        try:
            prd_priority = Priority(priority)
        except ValueError:
            valid = [p.value for p in Priority]
            raise ValidationError(
                f"Invalid priority: {priority!r}. Valid: {valid}",
                priority=priority,
            )

        # Auto-increment sequence when using default value (1)
        if sequence == 1:
            prds_dir_for_seq = resolve_project_root() / _config.prds_relative_path
            sequence = next_prd_sequence(prds_dir_for_seq, category.upper())

        prd_id = f"PRD-{category.upper()}-{sequence:03d}"

        if not title:
            first_line = input_text.strip().split("\n")[0]
            title = first_line[:60].rstrip(".")

        base_confidence = _PRIORITY_CONFIDENCE.get(priority, 0.7)

        # Generate PRD body from template (populates version cache)
        body = _generate_prd_body(
            prd_id, title, input_text, category,
            priority=priority, confidence=base_confidence,
        )

        # Extract SLOs from prefill for frontmatter
        prefill_slos = _extract_prefill(input_text).get("slos", [])

        # PRD-QUAL-013: Validate and set risk_level if provided
        prd_risk: RiskLevel | None = None
        if risk_level:
            try:
                prd_risk = RiskLevel(risk_level.lower())
            except ValueError:
                valid_risks = [r.value for r in RiskLevel]
                raise ValidationError(
                    f"Invalid risk_level: {risk_level!r}. Valid: {valid_risks}",
                    risk_level=risk_level,
                )

        # Build frontmatter
        frontmatter = PRDFrontmatter(
            id=prd_id,
            title=title,
            version="1.0",
            priority=prd_priority,
            category=category.upper(),
            risk_level=prd_risk,
            confidence=PRDConfidence(
                implementation_feasibility=base_confidence,
                requirement_clarity=base_confidence,
                estimate_confidence=max(base_confidence - 0.1, 0.4),
                test_coverage_target=0.85,
            ),
            evidence=PRDEvidence(
                level=EvidenceLevel.MODERATE,
                sources=["Input text analysis"],
            ),
            traceability=PRDTraceability(),
            quality_gates=PRDQualityGates(
                ambiguity_rate_max=_config.ambiguity_rate_max,
                completeness_min=_config.completeness_min,
                traceability_coverage_min=_config.traceability_coverage_min,
            ),
            dates=PRDDates(
                created=date.today(),
                updated=date.today(),
            ),
            template_version=_CACHED_TEMPLATE_VERSION,
            wave_source=None,
            slos=prefill_slos,
        )

        frontmatter_dict = model_to_dict(frontmatter)
        prd_content = _render_prd(frontmatter_dict, body)

        output_path = ""
        project_root = resolve_project_root()
        prds_dir = project_root / _config.prds_relative_path
        if prds_dir.exists() or (project_root / _config.trw_dir).exists():
            _writer.ensure_dir(prds_dir)
            prd_file = prds_dir / f"{prd_id}.md"
            _writer.write_text(prd_file, prd_content)
            output_path = str(prd_file)

        # Auto-sync INDEX.md/ROADMAP.md so catalogue stays current
        index_synced = False
        if output_path and _config.index_auto_sync_on_status_change:
            index_synced = _auto_sync_index()

        logger.info(
            "trw_prd_created",
            prd_id=prd_id,
            category=category,
            priority=priority,
        )

        return {
            "prd_id": prd_id,
            "title": title,
            "category": category.upper(),
            "priority": priority,
            "output_path": output_path,
            "content": prd_content,
            "sections_generated": len(_EXPECTED_SECTIONS),
            "index_synced": index_synced,
        }

    @server.tool()
    def trw_prd_validate(
        prd_path: str,
    ) -> dict[str, object]:
        """Validate a PRD against AARE-F quality gates — reports failures and scores.

        Single V2 execution path (PRD-FIX-011): V2 subsumes V1 checks.
        Exposes rich diagnostics: smell findings, EARS classifications,
        readability metrics, and section-level density scores.

        Args:
            prd_path: Path to the PRD markdown file to validate.
        """
        path = Path(prd_path).resolve()
        if not path.exists():
            raise StateError(f"PRD file not found: {path}", path=str(path))

        content = path.read_text(encoding="utf-8")

        # Single V2 validation call — subsumes all V1 checks (PRD-FIX-011)
        v2_result = validate_prd_quality_v2(content, _config)

        sections = _extract_sections(content)

        logger.info(
            "trw_prd_validated",
            path=str(path),
            valid=v2_result.valid,
            total_score=v2_result.total_score,
            quality_tier=v2_result.quality_tier,
            failures=len(v2_result.failures),
        )

        return {
            # V1 fields (backward compatible, from V2 inline computation)
            "path": str(path),
            "valid": v2_result.valid,
            "completeness_score": v2_result.completeness_score,
            "traceability_coverage": v2_result.traceability_coverage,
            "ambiguity_rate": v2_result.ambiguity_rate,
            "sections_found": sections,
            "sections_expected": _EXPECTED_SECTIONS,
            "failures": [
                {
                    "field": f.field, "rule": f.rule,
                    "message": f.message, "severity": f.severity,
                }
                for f in v2_result.failures
            ],
            # V2 fields (PRD-CORE-008)
            "total_score": v2_result.total_score,
            "quality_tier": v2_result.quality_tier,
            "grade": v2_result.grade,
            "dimensions": [
                {"name": d.name, "score": d.score, "max_score": d.max_score}
                for d in v2_result.dimensions
            ],
            "improvement_suggestions": [
                {
                    "dimension": s.dimension, "priority": s.priority,
                    "message": s.message, "current_score": s.current_score,
                    "potential_gain": s.potential_gain,
                }
                for s in v2_result.improvement_suggestions[:5]
            ],
            # Rich diagnostics (PRD-FIX-011: previously discarded)
            "smell_findings": [
                {
                    "category": sf.category, "matched_text": sf.matched_text,
                    "line_number": sf.line_number, "severity": sf.severity,
                    "suggestion": sf.suggestion,
                }
                for sf in v2_result.smell_findings
            ],
            "ears_classifications": v2_result.ears_classifications,
            "readability": v2_result.readability,
            "section_scores": [
                {"section_name": ss.section_name, "density": ss.density, "substantive_lines": ss.substantive_lines}
                for ss in v2_result.section_scores
            ],
            # Risk scaling metadata (PRD-QUAL-013)
            "effective_risk_level": v2_result.effective_risk_level,
            "risk_scaled": v2_result.risk_scaled,
        }


# --- Private helpers ---

_CACHED_TEMPLATE_BODY: str | None = None
_CACHED_TEMPLATE_VERSION: str | None = None

_TEMPLATE_VERSION_RE = re.compile(r"\*Template version:\s*([\d.]+)")
_FILE_REF_RE = re.compile(r"[\w/]+\.py")
_GOAL_KW_RE = re.compile(r"\b(goal|objective|achieve|deliver)\b", re.IGNORECASE)
_SLO_KW_RE = re.compile(r"\b(slo|latency|availability|throughput)\b", re.IGNORECASE)


def _load_template_body() -> str:
    """Load PRD template body from data/prd_template.md, cached.

    Strips YAML frontmatter (everything between the first ``---`` pair)
    and caches both the body and the extracted template version.

    Returns:
        Template body as a string (markdown after frontmatter).
    """
    global _CACHED_TEMPLATE_BODY, _CACHED_TEMPLATE_VERSION  # noqa: PLW0603

    if _CACHED_TEMPLATE_BODY is not None:
        return _CACHED_TEMPLATE_BODY

    template_path = Path(__file__).parent.parent / "data" / "prd_template.md"

    if not template_path.exists():
        logger.warning("prd_template_not_found", path=str(template_path))
        _CACHED_TEMPLATE_BODY = _FALLBACK_BODY
        _CACHED_TEMPLATE_VERSION = None
        return _CACHED_TEMPLATE_BODY

    raw = template_path.read_text(encoding="utf-8")

    # Strip YAML frontmatter (between first --- pair)
    fm_match = _FRONTMATTER_RE.match(raw)
    if fm_match:
        body = raw[fm_match.end():].lstrip("\n")
    else:
        body = raw

    # Extract template version from footer
    ver_match = _TEMPLATE_VERSION_RE.search(body)
    _CACHED_TEMPLATE_VERSION = ver_match.group(1) if ver_match else None

    _CACHED_TEMPLATE_BODY = body
    return _CACHED_TEMPLATE_BODY


def _substitute_template(
    body: str,
    prd_id: str,
    title: str,
    category: str,
    sequence: int,
    priority: str,
    confidence: float,
) -> str:
    """Replace template variables with actual values.

    Uses explicit ``str.replace()`` for the known variables to avoid
    false positives on prose ``{...}`` placeholders in the template.

    Args:
        body: Raw template body.
        prd_id: Full PRD identifier (e.g. ``PRD-CORE-007``).
        title: PRD title.
        category: PRD category (e.g. ``CORE``).
        sequence: Sequence number.
        priority: Priority string (e.g. ``P1``).
        confidence: Base confidence score.

    Returns:
        Template body with variables substituted.
    """
    seq_str = f"{sequence:03d}"
    result = body
    result = result.replace("{CATEGORY}", category)
    result = result.replace("{SEQUENCE}", seq_str)
    result = result.replace("{CAT}", category)
    result = result.replace("{SEQ}", seq_str)
    result = result.replace("{Title}", title)

    # Set dynamic Quick Reference values
    result = result.replace(
        "- **Status**: Draft | Review | Approved | Implemented",
        "- **Status**: Draft",
    )
    result = result.replace(
        "- **Priority**: P0 | P1 | P2 | P3",
        f"- **Priority**: {priority}",
    )
    result = result.replace(
        "- **Evidence**: Strong | Moderate | Limited | Theoretical",
        "- **Evidence**: Moderate",
    )
    result = result.replace(
        "- **Implementation Confidence**: 0.8",
        f"- **Implementation Confidence**: {confidence}",
    )

    return result


def _extract_prefill(input_text: str) -> dict[str, list[str]]:
    """Extract structured prefill data from input text.

    Best-effort extraction — never raises on failures.

    Args:
        input_text: User-supplied feature request / requirements text.

    Returns:
        Dict with keys ``file_refs``, ``prd_deps``, ``goals``, ``slos``.
    """
    prefill: dict[str, list[str]] = {
        "file_refs": [],
        "prd_deps": [],
        "goals": [],
        "slos": [],
    }

    try:
        prefill["file_refs"] = sorted(set(_FILE_REF_RE.findall(input_text)))
    except (re.error, TypeError):
        pass

    try:
        prefill["prd_deps"] = extract_prd_refs(input_text)
    except (re.error, TypeError, ValueError):
        pass

    # Extract goal-like and SLO sentences
    try:
        for sentence in re.split(r"[.\n]", input_text):
            stripped = sentence.strip()
            if not stripped:
                continue
            if _GOAL_KW_RE.search(stripped):
                prefill["goals"].append(stripped)
            if _SLO_KW_RE.search(stripped):
                prefill["slos"].append(stripped)
    except (re.error, TypeError):
        pass

    return prefill


def _apply_prefill(
    body: str,
    prefill: dict[str, list[str]],
    input_text: str,
) -> str:
    """Apply prefill data into the template body.

    Best-effort insertion — no exceptions on failures.

    Args:
        body: Template body (after variable substitution).
        prefill: Extracted prefill data from :func:`_extract_prefill`.
        input_text: Original input text for background section.

    Returns:
        Template body with prefill content inserted.
    """
    # Insert input_text into Background section
    body = body.replace(
        "{Brief context explaining why this feature/fix is needed}",
        input_text,
    )

    if prefill.get("file_refs"):
        body = body.replace(
            "| `path/to/file.py` | {Description of changes} |",
            "\n".join(
                f"| `{f}` | <!-- changes needed --> |"
                for f in prefill["file_refs"]
            ),
        )

    if prefill.get("prd_deps"):
        body = body.replace(
            "| DEP-001 | {Dependency} | Resolved/Pending | Yes/No |",
            "\n".join(
                f"| DEP-{i:03d} | {dep} | Pending | Yes |"
                for i, dep in enumerate(prefill["prd_deps"], 1)
            ),
        )

    return body


def _generate_prd_body(
    prd_id: str,
    title: str,
    input_text: str,
    category: str,
    priority: str = "P1",
    confidence: float = 0.7,
) -> str:
    """Generate PRD body content from template + input text.

    Loads the canonical template from ``data/prd_template.md``, substitutes
    variables, and prefills content from the input text.

    Args:
        prd_id: PRD identifier (e.g. ``PRD-CORE-007``).
        title: PRD title.
        input_text: Source text for the PRD.
        category: PRD category.
        priority: Priority level (P0-P3).
        confidence: Base confidence score derived from priority.

    Returns:
        Markdown body content with all template sections.
    """
    body = _load_template_body()
    body = _substitute_template(
        body, prd_id, title, category,
        int(prd_id.split("-")[-1]), priority, confidence,
    )
    body = _apply_prefill(body, _extract_prefill(input_text), input_text)
    return body


# Fallback body used when data/prd_template.md is missing
_FALLBACK_BODY = """# PRD-CATEGORY-SEQ: Title

**Quick Reference**:
- **Status**: Draft
- **Priority**: P1
- **Evidence**: Moderate
- **Implementation Confidence**: 0.7

---

## 1. Problem Statement
## 2. Goals & Non-Goals
## 3. User Stories
## 4. Functional Requirements
## 5. Non-Functional Requirements
## 6. Technical Approach
## 7. Test Strategy
## 8. Rollout Plan
## 9. Success Metrics
## 10. Dependencies & Risks
## 11. Open Questions
## 12. Traceability Matrix
"""


def _render_prd(frontmatter: dict[str, object], body: str) -> str:
    """Render complete PRD with YAML frontmatter and markdown body.

    Args:
        frontmatter: Frontmatter dictionary to serialize as YAML.
        body: Markdown body content.

    Returns:
        Complete PRD document as a string.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({"prd": frontmatter}, stream)
    yaml_str = stream.getvalue()

    return f"---\n{yaml_str}---\n\n{body}\n"


def _auto_sync_index() -> bool:
    """Auto-sync INDEX.md and ROADMAP.md after PRD changes.

    Best-effort sync triggered by prd_status_update and prd_create.
    Never raises -- logs warning on failure.

    Returns:
        True if sync succeeded, False otherwise.
    """
    try:
        from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md

        project_root = resolve_project_root()
        prds_dir = project_root / _config.prds_relative_path
        aare_dir = prds_dir.parent

        sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=_writer)
        sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=_writer)

        logger.debug("auto_index_sync_complete")
        return True
    except Exception as exc:
        logger.warning("auto_index_sync_failed", error=str(exc))
        return False
