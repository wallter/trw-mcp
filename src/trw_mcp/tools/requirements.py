"""TRW AARE-F requirements: ``create_prd`` + the ``trw_prd_validate`` tool.

``trw_prd_validate`` codifies the AARE-F Framework requirements engineering
process as an executable MCP tool. PRD creation moved to ``trw-mcp prd
create`` (PRD-CORE-300-FR07, slice S5): :func:`create_prd` is the plain
function the CLI (``trw_mcp.tools._prd_cli``) calls; it is no longer an MCP
tool because it is a rare, deliberate authoring action, not a per-session
default.

Template processing helpers live in ``_prd_template_helpers.py`` and are
re-exported here for backward-compatible test imports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

import structlog
from fastmcp import FastMCP

# Re-export all template helpers for backward-compatible test imports
# (e.g. ``from trw_mcp.tools.requirements import _load_template_body``).
import trw_mcp.tools._prd_template_helpers as _helpers
from trw_mcp.exceptions import ValidationError
from trw_mcp.models.config import get_config as get_config
from trw_mcp.models.requirements import (
    EvidenceLevel,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDQualityGates,
    PRDTraceability,
    PRDVerification,
    Priority,
    RiskLevel,
    VerificationMapping,
)
from trw_mcp.models.typed_dicts import (
    PrdCreateResultDict,
    PrdFrontmatterDict,
)
from trw_mcp.state._paths import resolve_project_root as resolve_project_root
from trw_mcp.state.persistence import FileStateWriter, model_to_dict
from trw_mcp.state.prd_utils import (
    _FRONTMATTER_RE as _FRONTMATTER_RE,
)
from trw_mcp.state.prd_utils import (
    find_identity_collisions,
    next_prd_sequence,
)
from trw_mcp.state.validation import (
    validate_prd_quality_v2 as validate_prd_quality_v2,
)
from trw_mcp.state.validation.template_variants import get_required_sections

# Backward-compatible re-exports for test imports (assignments are
# formatter-stable; the isort hook re-splits aliased import blocks).
_CACHED_TEMPLATE_BODY = _helpers._CACHED_TEMPLATE_BODY
_CACHED_TEMPLATE_VERSION = _helpers._CACHED_TEMPLATE_VERSION
_apply_prefill = _helpers._apply_prefill
_extract_prefill = _helpers._extract_prefill
_filter_sections_for_category = _helpers._filter_sections_for_category
_generate_prd_body = _helpers._generate_prd_body
_load_template_body = _helpers._load_template_body
_render_prd = _helpers._render_prd
_strip_deprecated_fields = _helpers._strip_deprecated_fields
_substitute_template = _helpers._substitute_template
reset_template_cache = _helpers.reset_template_cache

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
    _register_prd_validate_tool(server)


def create_prd(
    input_text: str,
    category: str = "CORE",
    priority: str = "P1",
    title: str = "",
    sequence: int = 1,
    risk_level: str = "",
    verification_mappings: list[dict[str, object]] | None = None,
) -> PrdCreateResultDict:
    """Generate an AARE-F PRD from a feature description and write it to disk.

    Use when a feature request needs a structured PRD before coding a
    P0-P2 feature or risky behavioral change. Called by ``trw-mcp prd
    create`` (PRD-CORE-300-FR07) — this is a plain function, not an MCP
    tool, so it carries no ``@server.tool`` decoration.

    Allocates a PRD ID and frontmatter; syncs INDEX.md/ROADMAP.md.
    category: CORE|QUAL|INFRA|LOCAL|EXPLR|RESEARCH|FIX (extendable via
    config). priority: P0-P3. risk_level: critical|high|medium|low.
    sequence auto-increments from 1.

    Output: prd_id, output_path, sections_generated, index_synced.

    See Also: trw_prd_validate

    Args:
        input_text: feature request/description; becomes the Problem Statement.
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
    prds_dir_for_seq = resolve_project_root() / config.prds_relative_path
    if sequence == 1:
        sequence = next_prd_sequence(prds_dir_for_seq, category.upper())

    prd_id = f"PRD-{category.upper()}-{sequence:03d}"

    # PRD-QUAL-121-FR02: allocation shares the root collision rule — an
    # active or archived file already owning this identifier blocks the
    # write entirely and the failure names every conflicting path.
    collisions = find_identity_collisions(prds_dir_for_seq, prd_id)
    if collisions:
        intended = str(prds_dir_for_seq / f"{prd_id}.md")
        raise ValidationError(
            f"PRD identifier collision: {prd_id} is already owned by "
            f"{', '.join(collisions)}; refusing to write {intended}. "
            "A collision blocks allocation until the conflicting record is migrated.",
            prd_id=prd_id,
            conflicting_paths=", ".join(collisions),
            intended_path=intended,
        )

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

    typed_verification_mappings: list[VerificationMapping] = []
    for index, raw_mapping in enumerate(verification_mappings or []):
        try:
            typed_verification_mappings.append(VerificationMapping.model_validate(raw_mapping, strict=False))
        except Exception as err:
            raise ValidationError(
                f"Invalid verification_mappings[{index}]: {err}",
                mapping_index=index,
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
            test_coverage_target=None,
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
        verification=PRDVerification(mappings=typed_verification_mappings),
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
    not_written_reason = ""
    project_root = resolve_project_root()
    prds_dir = project_root / config.prds_relative_path
    if prds_dir.exists() or (project_root / config.trw_dir).exists():
        writer.ensure_dir(prds_dir)
        prd_file = prds_dir / f"{prd_id}.md"
        writer.write_text(prd_file, prd_content)
        output_path = str(prd_file)
    else:
        # Nothing was persisted. An empty output_path was the only signal,
        # and a caller reading prd_id/content would reasonably believe the
        # PRD exists on disk. Name the reason so the failure is actionable.
        not_written_reason = (
            f"neither the PRD directory ({prds_dir}) nor {config.trw_dir} exists under {project_root}; "
            "the rendered PRD is returned in `content` but was NOT written to disk"
        )
        logger.warning("prd_create_not_written", prd_id=prd_id, prds_dir=str(prds_dir))

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
        "sections_generated": len(get_required_sections(category)),
        "index_synced": index_synced,
    }
    if not_written_reason:
        prd_result["not_written_reason"] = not_written_reason

    return prd_result


def _register_prd_validate_tool(server: FastMCP) -> None:
    """Register ``trw_prd_validate``. Implementation lives in the
    ``_prd_validate_tool`` sibling (extracted for the 350 eLOC gate)."""
    from trw_mcp.tools._prd_validate_tool import _register_prd_validate_tool as _impl

    _impl(server)


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
