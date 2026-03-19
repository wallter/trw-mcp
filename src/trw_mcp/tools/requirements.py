"""TRW AARE-F requirements tools — prd_create, prd_validate.

These 2 tools codify the AARE-F Framework v1.1.0 requirements engineering
process as executable MCP tools.
"""

from __future__ import annotations

import contextlib
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import cast

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
from trw_mcp.models.typed_dicts import PrdCreateResultDict, PrdFrontmatterDict, ValidateResultDict
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateWriter, model_to_dict
from trw_mcp.state.prd_utils import (
    _FRONTMATTER_RE,
    extract_prd_refs,
    next_prd_sequence,
)
from trw_mcp.state.prd_utils import (
    extract_sections as _extract_sections,
)
from trw_mcp.state.validation import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTIONS,
)
from trw_mcp.state.validation import (
    validate_prd_quality_v2,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def __getattr__(name: str) -> object:
    """Backward-compat shim for removed module-level singletons (FIX-044)."""
    from trw_mcp.state._helpers import _compat_getattr

    return _compat_getattr(name)


# Priority → base confidence score mapping
_PRIORITY_CONFIDENCE: dict[str, float] = {
    "P0": 0.9,
    "P1": 0.7,
    "P2": 0.6,
    "P3": 0.5,
}


def register_requirements_tools(server: FastMCP) -> None:
    """Register AARE-F requirements tools on the MCP server.

    Args:
        server: FastMCP server instance to register tools on.
    """

    @server.tool()
    @log_tool_call
    def trw_prd_create(
        input_text: str,
        category: str = "CORE",
        priority: str = "P1",
        title: str = "",
        sequence: int = 1,
        risk_level: str = "",
    ) -> PrdCreateResultDict:
        """Turn a feature request into a structured PRD — ensures requirements are traceable, testable, and complete.

        Generates an AARE-F compliant PRD with YAML frontmatter, 12 standard sections,
        confidence scores, and traceability links. Auto-increments the PRD ID from
        the existing catalogue and updates INDEX.md after creation.

        Args:
            input_text: Feature request, requirements, or description — becomes the Problem Statement and Background.
            category: PRD category (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX).
            priority: Priority level (P0, P1, P2, P3). Determines base confidence scores.
            title: PRD title. Auto-generated from input if not provided.
            sequence: Sequence number for PRD ID. Auto-increments from existing PRDs when default (1).
            risk_level: Optional risk level (critical, high, medium, low). Scales validation strictness.
        """
        config = get_config()
        writer = FileStateWriter()

        # Input validation (PRD-QUAL-042-FR03): category enum
        valid_categories = {"CORE", "QUAL", "INFRA", "LOCAL", "EXPLR", "RESEARCH", "FIX"}
        if category.upper() not in valid_categories:
            raise ValidationError(
                f"Invalid category: {category!r}. Must be one of {sorted(valid_categories)}",
            )
        category = category.upper()

        # Validate priority
        try:
            prd_priority = Priority(priority)
        except ValueError as err:
            valid = [p.value for p in Priority]
            raise ValidationError(
                f"Invalid priority: {priority!r}. Valid: {valid}",
                priority=priority,
            ) from err

        # Auto-increment sequence when using default value (1)
        if sequence == 1:
            prds_dir_for_seq = resolve_project_root() / config.prds_relative_path
            sequence = next_prd_sequence(prds_dir_for_seq, category.upper())

        prd_id = f"PRD-{category.upper()}-{sequence:03d}"

        if not title:
            first_line = input_text.strip().split("\n")[0]
            title = first_line[:60].rstrip(".")

        base_confidence = _PRIORITY_CONFIDENCE.get(priority, 0.7)

        # Generate PRD body from template (populates version cache)
        body = _generate_prd_body(
            prd_id,
            title,
            input_text,
            category,
            priority=priority,
            confidence=base_confidence,
        )

        # Extract SLOs from prefill for frontmatter
        prefill_slos = _extract_prefill(input_text).get("slos", [])

        # PRD-QUAL-013: Validate and set risk_level if provided
        prd_risk: RiskLevel | None = None
        if risk_level:
            try:
                prd_risk = RiskLevel(risk_level.lower())
            except ValueError as err:
                valid_risks = [r.value for r in RiskLevel]
                raise ValidationError(
                    f"Invalid risk_level: {risk_level!r}. Valid: {valid_risks}",
                    risk_level=risk_level,
                ) from err

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
                ambiguity_rate_max=config.ambiguity_rate_max,
                completeness_min=config.completeness_min,
                traceability_coverage_min=config.traceability_coverage_min,
            ),
            dates=PRDDates(
                created=datetime.now(tz=timezone.utc).date(),
                updated=datetime.now(tz=timezone.utc).date(),
            ),
            template_version=_CACHED_TEMPLATE_VERSION,
            wave_source=None,
            slos=prefill_slos,
        )

        frontmatter_dict = cast("PrdFrontmatterDict", model_to_dict(frontmatter))
        prd_content = _render_prd(frontmatter_dict, body)

        output_path = ""
        project_root = resolve_project_root()
        prds_dir = project_root / config.prds_relative_path
        if prds_dir.exists() or (project_root / config.trw_dir).exists():
            writer.ensure_dir(prds_dir)
            prd_file = prds_dir / f"{prd_id}.md"
            writer.write_text(prd_file, prd_content)
            output_path = str(prd_file)

        # Auto-sync INDEX.md/ROADMAP.md so catalogue stays current
        index_synced = False
        if output_path and config.index_auto_sync_on_status_change:
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
    @log_tool_call
    def trw_prd_validate(
        prd_path: str,
    ) -> ValidateResultDict:
        """Check your PRD quality before implementation — catches ambiguity, missing sections, and weak requirements early.

        Runs the full V2 validation suite: structure compliance, content quality,
        AARE-F compliance, and ambiguity analysis. Returns a total score (0-100),
        quality tier, grade, and actionable improvement suggestions. Catching
        issues here prevents rework during implementation.

        Args:
            prd_path: Path to the PRD markdown file to validate.
        """
        path = Path(prd_path).resolve()

        # QUAL-042-FR03: Path containment — prevent reading files outside project
        from trw_mcp.state._paths import resolve_project_root

        project_root = resolve_project_root()
        if not path.is_relative_to(project_root):
            raise StateError(
                f"PRD path escapes project root: {path}",
                path=str(path),
            )

        if not path.exists():
            raise StateError(f"PRD file not found: {path}", path=str(path))

        content = path.read_text(encoding="utf-8")

        # Single V2 validation call — subsumes all V1 checks (PRD-FIX-011)
        config = get_config()
        v2_result = validate_prd_quality_v2(
            content, config, project_root=str(project_root)
        )

        sections = _extract_sections(content)

        # Auto-update phase to PLAN
        from trw_mcp.models.run import Phase
        from trw_mcp.state._paths import find_active_run
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(find_active_run(), Phase.PLAN)

        logger.info(
            "trw_prd_validated",
            path=str(path),
            valid=v2_result.valid,
            total_score=v2_result.total_score,
            quality_tier=v2_result.quality_tier,
            failures=len(v2_result.failures),
        )

        validate_result: ValidateResultDict = {
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
                    "field": f.field,
                    "rule": f.rule,
                    "message": f.message,
                    "severity": f.severity,
                }
                for f in v2_result.failures
            ],
            # V2 fields (PRD-CORE-008)
            "total_score": v2_result.total_score,
            "quality_tier": v2_result.quality_tier,
            "grade": v2_result.grade,
            "dimensions": [{"name": d.name, "score": d.score, "max_score": d.max_score} for d in v2_result.dimensions],
            "improvement_suggestions": [
                {
                    "dimension": s.dimension,
                    "priority": s.priority,
                    "message": s.message,
                    "current_score": s.current_score,
                    "potential_gain": s.potential_gain,
                }
                for s in v2_result.improvement_suggestions[:5]
            ],
            # Rich diagnostics (PRD-FIX-011: previously discarded)
            "smell_findings": [
                {
                    "category": sf.category,
                    "matched_text": sf.matched_text,
                    "line_number": sf.line_number,
                    "severity": sf.severity,
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
        return validate_result


# --- Private helpers ---

_CACHED_TEMPLATE_BODY: str | None = None
_CACHED_TEMPLATE_VERSION: str | None = None

_TEMPLATE_VERSION_RE = re.compile(r"\*Template version:\s*([\d.]+)")
_FILE_REF_RE = re.compile(r"[\w/]+\.\w+")
_GOAL_KW_RE = re.compile(r"\b(goal|objective|achieve|deliver)\b", re.IGNORECASE)
_SLO_KW_RE = re.compile(r"\b(slo|latency|availability|throughput)\b", re.IGNORECASE)


def _load_template_body() -> str:
    """Load PRD template body from data/prd_template.md, cached.

    Strips YAML frontmatter (everything between the first ``---`` pair)
    and caches both the body and the extracted template version.

    Returns:
        Template body as a string (markdown after frontmatter).
    """
    global _CACHED_TEMPLATE_BODY, _CACHED_TEMPLATE_VERSION

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
        body = raw[fm_match.end() :].lstrip("\n")
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

    # FR04 (PRD-FIX-056): Inject **Status**: active after **Priority**: lines
    # inside FR blocks (### *-FRN: headers).  Applies only to lines that are
    # the FR-level Priority field (bold, not inside a table or list).
    # The replacement inserts "**Status**: active\n" after each such line,
    # but only when there is no **Status**: line already present in that block.
    result = re.sub(
        r"(\*\*Priority\*\*: Must Have \| Should Have \| Nice to Have)",
        r"\1\n**Status**: active",
        result,
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

    with contextlib.suppress(re.error, TypeError):
        prefill["file_refs"] = sorted(set(_FILE_REF_RE.findall(input_text)))

    with contextlib.suppress(re.error, TypeError, ValueError):
        prefill["prd_deps"] = extract_prd_refs(input_text)

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
        logger.debug("prefill_extraction_failed", exc_info=True)

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
            "\n".join(f"| `{f}` | <!-- changes needed --> |" for f in prefill["file_refs"]),
        )

    if prefill.get("prd_deps"):
        body = body.replace(
            "| DEP-001 | {Dependency} | Resolved/Pending | Yes/No |",
            "\n".join(f"| DEP-{i:03d} | {dep} | Pending | Yes |" for i, dep in enumerate(prefill["prd_deps"], 1)),
        )

    return body


def _filter_sections_for_category(body: str, category: str) -> str:
    """Remove numbered sections not required by the category variant.

    Splits the body on ``## N. Section Name`` boundaries and retains only
    those section blocks whose heading name appears in the required list for
    the given category. Non-numbered blocks (the preamble, Appendix, Quality
    Checklist) are always retained.

    Section numbers are re-assigned sequentially after filtering so the
    output is always numbered 1, 2, 3, … without gaps.

    Args:
        body: Template body (post variable-substitution).
        category: PRD category (e.g. ``"FIX"``, ``"CORE"``).

    Returns:
        Body with only the required section blocks, renumbered.
    """
    from trw_mcp.state.validation.template_variants import get_required_sections

    required = set(get_required_sections(category))

    # Regex: split on lines that start a numbered section "## N. Heading"
    _NUMBERED_SECTION_RE = re.compile(r"^(## \d+\. .+)$", re.MULTILINE)

    # Split into (preamble | section_header | section_body) triples
    parts = _NUMBERED_SECTION_RE.split(body)
    # parts[0] is preamble (before first ## N. heading)
    # parts[1], parts[2], parts[3], parts[4], ... alternate header, body pairs

    preamble = parts[0]
    kept_sections: list[tuple[str, str]] = []  # (header_line, body_text)

    for i in range(1, len(parts) - 1, 2):
        header_line = parts[i]  # e.g. "## 3. User Stories"
        section_body = parts[i + 1] if i + 1 < len(parts) else ""

        # Extract section name: strip "## N. " prefix
        name_match = re.match(r"## \d+\. (.+)", header_line)
        if name_match:
            section_name = name_match.group(1).strip()
            if section_name in required:
                kept_sections.append((section_name, section_body))
        else:
            # Non-standard header — keep as-is (shouldn't occur in practice)
            kept_sections.append((header_line, section_body))

    # Extract trailing non-numbered content (Appendix, Quality Checklist)
    # from the last numbered section's body.  Non-numbered ## headings
    # (e.g. "## Appendix") are always preserved regardless of category.
    trailing = ""
    if kept_sections or parts:
        # The last element in parts is the body after the final numbered heading.
        # It may contain non-numbered sections like ## Appendix.
        last_body = parts[-1] if len(parts) > 1 else ""
        # Find the first non-numbered ## heading in the last body
        _NON_NUMBERED_RE = re.compile(r"^(## (?!\d+\.).+)$", re.MULTILINE)
        m = _NON_NUMBERED_RE.search(last_body)
        if m:
            trailing = last_body[m.start() :]
            # Trim trailing from the last section body if it was kept
            if kept_sections:
                last_name, last_body_text = kept_sections[-1]
                cut_m = _NON_NUMBERED_RE.search(last_body_text)
                if cut_m:
                    kept_sections[-1] = (last_name, last_body_text[: cut_m.start()])

    # Rebuild with renumbered headings
    result_parts = [preamble]
    for idx, (section_name, section_body) in enumerate(kept_sections, start=1):
        result_parts.append(f"## {idx}. {section_name}{section_body}")

    if trailing:
        result_parts.append(trailing)

    return "".join(result_parts)


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
    variables, prefills content from the input text, and filters sections
    to only those required by the category variant (PRD-CORE-080-FR01).

    Args:
        prd_id: PRD identifier (e.g. ``PRD-CORE-007``).
        title: PRD title.
        input_text: Source text for the PRD.
        category: PRD category.
        priority: Priority level (P0-P3).
        confidence: Base confidence score derived from priority.

    Returns:
        Markdown body content with only the category-appropriate sections.
    """
    body = _load_template_body()
    body = _substitute_template(
        body,
        prd_id,
        title,
        category,
        int(prd_id.split("-")[-1]),
        priority,
        confidence,
    )
    body = _apply_prefill(body, _extract_prefill(input_text), input_text)
    # PRD-CORE-080-FR01: filter to only sections required by category variant
    body = _filter_sections_for_category(body, category)
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


def _strip_deprecated_fields(frontmatter: PrdFrontmatterDict) -> PrdFrontmatterDict:
    """Remove deprecated / null frontmatter fields before YAML output.

    Strips ``None``-valued top-level keys and explicitly removes decorative
    fields (``aaref_components``, ``conflicts_with``) that have 0% usage and
    add no value to new PRDs (PRD-CORE-080-FR07).

    Also removes ``conflicts_with`` from the nested ``traceability`` dict
    if present, since it is always empty and decorative.

    Existing PRDs that contain these fields remain parseable — the Pydantic
    model accepts them as ``Optional`` for backward compatibility.

    Args:
        frontmatter: Raw frontmatter dict from ``model_to_dict``.

    Returns:
        Cleaned dict with deprecated/null fields removed.
    """
    _DEPRECATED_TOP_KEYS = frozenset({"aaref_components"})
    result: dict[str, object] = {
        k: v for k, v in frontmatter.items() if v is not None and k not in _DEPRECATED_TOP_KEYS
    }
    # Strip conflicts_with from nested traceability dict
    traceability = result.get("traceability")
    if isinstance(traceability, dict) and "conflicts_with" in traceability:
        result["traceability"] = {k: v for k, v in traceability.items() if k != "conflicts_with"}
    return cast("PrdFrontmatterDict", result)


def _render_prd(frontmatter: PrdFrontmatterDict, body: str) -> str:
    """Render complete PRD with YAML frontmatter and markdown body.

    Strips deprecated fields (``aaref_components``, ``conflicts_with``) and
    ``None``-valued keys from frontmatter before YAML serialization so that
    newly generated PRDs are clean and minimal (PRD-CORE-080-FR07).

    Args:
        frontmatter: Frontmatter dictionary to serialize as YAML.
        body: Markdown body content.

    Returns:
        Complete PRD document as a string.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({"prd": _strip_deprecated_fields(frontmatter)}, stream)
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

        config = get_config()
        writer = FileStateWriter()
        project_root = resolve_project_root()
        prds_dir = project_root / config.prds_relative_path
        aare_dir = prds_dir.parent

        sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=writer)
        sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=writer)

        logger.debug("auto_index_sync_complete")
        return True
    except Exception as exc:  # justified: fail-open, index sync is best-effort after PRD changes
        logger.warning("auto_index_sync_failed", error=str(exc))
        return False
