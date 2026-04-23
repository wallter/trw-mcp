"""TRW AARE-F requirements tools --- prd_create, prd_validate.

These 2 tools codify the AARE-F Framework v1.1.0 requirements engineering
process as executable MCP tools.

Template processing helpers live in ``_prd_template_helpers.py`` and are
re-exported here for backward-compatible test imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import Context, FastMCP

# Re-export all template helpers for backward-compatible test imports
# (e.g. ``from trw_mcp.tools.requirements import _load_template_body``).
import trw_mcp.tools._prd_template_helpers as _helpers
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
from trw_mcp.models.typed_dicts import (
    PrdCreateResultDict,
    PrdFrontmatterDict,
    ValidateResultDict,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateWriter, model_to_dict
from trw_mcp.state.prd_utils import (
    _FRONTMATTER_RE as _FRONTMATTER_RE,
)
from trw_mcp.state.prd_utils import (
    extract_sections as _extract_sections,
)
from trw_mcp.state.prd_utils import (
    next_prd_sequence,
)
from trw_mcp.state.validation import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTIONS,
)
from trw_mcp.state.validation import (
    validate_prd_quality_v2,
)
from trw_mcp.tools._prd_template_helpers import (
    _CACHED_TEMPLATE_BODY as _CACHED_TEMPLATE_BODY,
)
from trw_mcp.tools._prd_template_helpers import (
    _CACHED_TEMPLATE_VERSION as _CACHED_TEMPLATE_VERSION,
)
from trw_mcp.tools._prd_template_helpers import (
    _apply_prefill as _apply_prefill,
)
from trw_mcp.tools._prd_template_helpers import (
    _extract_prefill as _extract_prefill,
)
from trw_mcp.tools._prd_template_helpers import (
    _filter_sections_for_category as _filter_sections_for_category,
)
from trw_mcp.tools._prd_template_helpers import (
    _generate_prd_body as _generate_prd_body,
)
from trw_mcp.tools._prd_template_helpers import (
    _load_template_body as _load_template_body,
)
from trw_mcp.tools._prd_template_helpers import (
    _render_prd as _render_prd,
)
from trw_mcp.tools._prd_template_helpers import (
    _strip_deprecated_fields as _strip_deprecated_fields,
)
from trw_mcp.tools._prd_template_helpers import (
    _substitute_template as _substitute_template,
)
from trw_mcp.tools._prd_template_helpers import (
    reset_template_cache as reset_template_cache,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)

# Priority -> base confidence score mapping
_PRIORITY_CONFIDENCE: dict[str, float] = {
    "P0": 0.9,
    "P1": 0.7,
    "P2": 0.6,
    "P3": 0.5,
}


def register_requirements_tools(server: FastMCP) -> None:
    """Register AARE-F requirements tools on the MCP server."""
    _register_prd_create_tool(server)
    _register_prd_validate_tool(server)


def _register_prd_create_tool(server: FastMCP) -> None:
    """Register the PRD creation tool."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_prd_create(
        input_text: str,
        category: str = "CORE",
        priority: str = "P1",
        title: str = "",
        sequence: int = 1,
        risk_level: str = "",
    ) -> PrdCreateResultDict:
        """Generate an AARE-F compliant PRD from a feature description.

        Use when:
        - You have a feature request or requirements and need a structured PRD.
        - You want auto-incremented PRD ID, YAML frontmatter, and catalogue sync.

        Produces 12 standard sections, confidence scores, and traceability links.
        Updates INDEX.md/ROADMAP.md when ``index_auto_sync_on_status_change`` is on.

        Input:
        - input_text: feature request or description (becomes Problem Statement + Background).
        - category: one of CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX (plus any
          values added to ``.trw/config.yaml::extra_prd_categories``).
        - priority: P0, P1, P2, or P3 — drives base confidence scores.
        - title: auto-generated from input_text when empty.
        - sequence: auto-increments from existing catalogue when default (1).
        - risk_level: optional critical|high|medium|low — scales validation strictness.

        Output: PrdCreateResultDict with fields
        {prd_id: str, title: str, category: str, priority: str, output_path: str,
         content: str, sections_generated: int, index_synced: bool}.

        Example:
            trw_prd_create(input_text="Add rate limiting to public API",
                           category="CORE", priority="P1")
            → {"prd_id": "PRD-CORE-001", "output_path": "docs/requirements-aare-f/prds/PRD-CORE-001.md",
               "sections_generated": 12, "index_synced": true, ...}

        See Also: trw_prd_validate
        """
        config = get_config()
        writer = FileStateWriter()

        # Input validation (PRD-QUAL-042-FR03): category enum
        # Built-in generic categories shipped with trw-mcp.
        # Projects extend via `.trw/config.yaml` field `extra_prd_categories`.
        # See trw_mcp.state.validation.prd_integrity.allowed_prd_categories.
        from trw_mcp.state.validation.prd_integrity import allowed_prd_categories

        valid_categories = set(allowed_prd_categories())
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
            template_version=_helpers._CACHED_TEMPLATE_VERSION,
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
        logger.debug(
            "prd_create_detail",
            title=title,
            prd_scope=prd_id,
        )

        prd_result: PrdCreateResultDict = {
            "prd_id": prd_id,
            "title": title,
            "category": category.upper(),
            "priority": priority,
            "output_path": output_path,
            "content": prd_content,
            "sections_generated": len(_EXPECTED_SECTIONS),
            "index_synced": index_synced,
        }

        # Inject ceremony progress summary.
        try:
            from trw_mcp.state._paths import resolve_trw_dir
            from trw_mcp.tools._ceremony_status import append_ceremony_status

            append_ceremony_status(cast("dict[str, object]", prd_result), resolve_trw_dir())
        except Exception:  # justified: fail-open — ceremony status must not break prd_create
            logger.debug("prd_create_ceremony_status_skipped", exc_info=True)

        return prd_result


def _register_prd_validate_tool(server: FastMCP) -> None:
    """Register the PRD validation tool."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_prd_validate(
        ctx: Context | None = None,
        prd_path: str = "",
    ) -> ValidateResultDict:
        """Score a PRD against the V2 validation suite before implementation.

        Use when:
        - A PRD just landed and you need a READY / NEEDS-WORK verdict before coding.
        - You want ambiguity / completeness / traceability gates checked in one call.

        Runs structure compliance, content quality, AARE-F compliance, and
        ambiguity analysis. Catches issues here that would otherwise cause rework.

        Input:
        - prd_path: path to the PRD markdown file (required).

        Output: ValidateResultDict with fields
        {total_score: int (0-100), tier: str, grade: str,
         gate_pass: bool, ambiguity_rate: float, completeness: float,
         traceability_coverage: float, suggestions: list[str]}.

        Example:
            trw_prd_validate(prd_path="docs/requirements-aare-f/prds/PRD-QUAL-074.md")
            → {"total_score": 87, "tier": "PRODUCTION", "grade": "A",
               "gate_pass": true, "suggestions": []}
        """
        # prd_path has an empty default so FastMCP can inject ctx as the first
        # typed kwarg (PRD-CORE-141 FR03); an empty path is still rejected.
        if not prd_path:
            raise StateError("prd_path is required", path="")
        path = Path(prd_path).resolve()

        # QUAL-042-FR03: Path containment --- prevent reading files outside project
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

        # Single V2 validation call --- subsumes all V1 checks (PRD-FIX-011)
        config = get_config()
        v2_result = validate_prd_quality_v2(content, config, project_root=str(project_root))

        sections = _extract_sections(content)

        # Auto-update phase to PLAN (PRD-CORE-141 FR03/FR05: ctx-aware
        # find_active_run suppresses mtime-scan hijack for fresh sessions).
        from trw_mcp.models.run import Phase
        from trw_mcp.state._paths import (
            TRWCallContext,
            find_active_run,
            resolve_pin_key,
        )
        from trw_mcp.state.phase import try_update_phase

        _pin_key = resolve_pin_key(ctx=ctx, explicit=None)
        _raw_session = getattr(ctx, "session_id", None) if ctx is not None else None
        _call_ctx = TRWCallContext(
            session_id=_pin_key,
            client_hint=None,
            explicit=False,
            fastmcp_session=_raw_session if isinstance(_raw_session, str) else None,
        )
        try_update_phase(find_active_run(context=_call_ctx), Phase.PLAN)

        _prd_id_str = str(path.stem)
        logger.info(
            "trw_prd_validated",
            path=str(path),
            valid=v2_result.valid,
            total_score=v2_result.total_score,
            quality_tier=v2_result.quality_tier,
            failures=len(v2_result.failures),
        )
        _min_threshold = config.completeness_min
        if v2_result.total_score < _min_threshold:
            logger.warning(
                "prd_validate_below_threshold",
                prd_id=_prd_id_str,
                score=v2_result.total_score,
                threshold=_min_threshold,
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
            "dimensions": [
                {
                    "name": d.name,
                    "score": d.score,
                    "max_score": d.max_score,
                    "details": d.details,
                }
                for d in v2_result.dimensions
            ],
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
                {
                    "section_name": ss.section_name,
                    "density": ss.density,
                    "substantive_lines": ss.substantive_lines,
                }
                for ss in v2_result.section_scores
            ],
            # Risk scaling metadata (PRD-QUAL-013)
            "effective_risk_level": v2_result.effective_risk_level,
            "risk_scaled": v2_result.risk_scaled,
            "status_drift_warnings": v2_result.status_drift_warnings,
            "integrity_warnings": v2_result.integrity_warnings,
        }

        # Inject ceremony progress summary.
        try:
            from trw_mcp.state._paths import resolve_trw_dir as _resolve_trw_dir
            from trw_mcp.tools._ceremony_status import append_ceremony_status

            append_ceremony_status(cast("dict[str, object]", validate_result), _resolve_trw_dir())
        except Exception:  # justified: fail-open — ceremony status must not break prd_validate
            logger.debug("prd_validate_ceremony_status_skipped", exc_info=True)

        return validate_result


def _auto_sync_index() -> bool:
    """Auto-sync INDEX.md and ROADMAP.md after PRD changes.

    Best-effort sync triggered by prd_status_update and prd_create.
    Never raises --- logs warning on failure.

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
    except Exception as exc:  # justified: fail-open, index sync is best-effort and must not block PRD tools
        logger.warning("auto_index_sync_failed", error=str(exc), exc_info=True)
        return False
