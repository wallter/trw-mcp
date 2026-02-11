"""TRW AARE-F requirements tools — prd_create, prd_validate, traceability_check, prd_status_update, index_sync, prd_groom.

These 6 tools codify the AARE-F Framework v1.1.0 requirements engineering
process as executable MCP tools.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import (
    EvidenceLevel,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDQualityGates,
    PRDStatus,
    PRDTraceability,
    Priority,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
)
from trw_mcp.state._paths import resolve_project_root
from trw_mcp.state.persistence import FileStateReader, FileStateWriter, model_to_dict
from trw_mcp.state.prd_utils import (
    check_transition_guards,
    is_valid_transition,
    next_prd_sequence,
    parse_frontmatter as _parse_frontmatter_impl,
    extract_sections as _extract_sections_impl,
    detect_ambiguity as _detect_ambiguity_impl,
    extract_prd_refs,
    update_frontmatter,
)
from trw_mcp.state.validation import (
    _EXPECTED_SECTION_NAMES as _EXPECTED_SECTIONS,
    validate_prd_quality_v2,
)
from trw_mcp.tools.findings import get_unlinked_findings

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()


def register_requirements_tools(server: FastMCP) -> None:
    """Register all 6 AARE-F requirements tools on the MCP server.

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
    ) -> dict[str, object]:
        """Generate an AARE-F compliant PRD from a feature request or requirements text.

        Args:
            input_text: Feature request, requirements, or description to base the PRD on.
            category: PRD category (CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX).
            priority: Priority level (P0, P1, P2, P3).
            title: PRD title. Auto-generated from input if not provided.
            sequence: Sequence number for PRD ID. Auto-increments from existing PRDs when default (1).
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
            prds_dir_for_seq = (
                resolve_project_root() / Path(_config.prds_relative_path)
            )
            sequence = next_prd_sequence(prds_dir_for_seq, category.upper())

        # Generate PRD ID
        prd_id = f"PRD-{category.upper()}-{sequence:03d}"

        # Auto-generate title if not provided
        if not title:
            # Use first sentence or first 60 chars of input
            first_line = input_text.strip().split("\n")[0]
            title = first_line[:60].rstrip(".")

        # Map priority → base confidence score
        _priority_confidence: dict[str, float] = {
            "P0": 0.9, "P1": 0.7, "P2": 0.6, "P3": 0.5,
        }
        base_confidence = _priority_confidence.get(priority, 0.7)

        # Generate PRD body from template (populates version cache)
        body = _generate_prd_body(
            prd_id, title, input_text, category,
            priority=priority, confidence=base_confidence,
        )

        # Extract SLOs from prefill for frontmatter
        prefill_slos = _extract_prefill(input_text).get("slos", [])

        # Build frontmatter
        frontmatter = PRDFrontmatter(
            id=prd_id,
            title=title,
            version="1.0",
            priority=prd_priority,
            category=category.upper(),
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

        # Combine frontmatter + body
        frontmatter_dict = model_to_dict(frontmatter)
        prd_content = _render_prd(frontmatter_dict, body)

        # Save to project if .trw/ exists
        output_path = ""
        project_root = resolve_project_root()
        prds_dir = project_root / Path(_config.prds_relative_path)
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

        # Check for ambiguous terms and update V2 result
        ambiguous_terms = _detect_ambiguity(content)
        if ambiguous_terms:
            total_words = len(content.split())
            ambiguity_rate = len(ambiguous_terms) / max(total_words, 1)
            v2_result.ambiguity_rate = ambiguity_rate
            if ambiguity_rate > _config.ambiguity_rate_max:
                v2_result.failures.append(
                    ValidationFailure(
                        field="content",
                        rule="ambiguity_rate",
                        message=f"Ambiguity rate {ambiguity_rate:.2%} exceeds {_config.ambiguity_rate_max:.0%} threshold",
                        severity="warning",
                    )
                )

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
            "ambiguous_terms": ambiguous_terms,
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
        }

    @server.tool()
    def trw_traceability_check(
        prd_path: str | None = None,
        source_dir: str | None = None,
    ) -> dict[str, object]:
        """Verify requirement traceability coverage across PRDs and source code.

        Args:
            prd_path: Path to specific PRD file, or None to scan all PRDs.
            source_dir: Source directory to check for implementations.
        """
        project_root = resolve_project_root()

        # Collect PRDs
        prd_files: list[Path] = []
        if prd_path:
            prd_files.append(Path(prd_path).resolve())
        else:
            prds_dir = project_root / Path(_config.prds_relative_path)
            if prds_dir.exists():
                prd_files = [
                    f for f in sorted(prds_dir.glob("*.md"))
                    if f.name != "TEMPLATE.md"
                ]

        if not prd_files:
            return {
                "total_requirements": 0,
                "traced_requirements": 0,
                "coverage": 0.0,
                "message": "No PRD files found to analyze",
            }

        # Extract requirements and their traces
        total_reqs = 0
        traced_reqs = 0
        untraced: list[str] = []

        for prd_file in prd_files:
            if not prd_file.exists():
                continue
            content = prd_file.read_text(encoding="utf-8")
            frontmatter = _parse_frontmatter(content)

            # Count requirements from frontmatter traceability
            trace_data = frontmatter.get("traceability", {})
            if isinstance(trace_data, dict):
                implements = trace_data.get("implements", [])
                if isinstance(implements, list) and implements:
                    traced_reqs += 1
                    total_reqs += 1
                else:
                    total_reqs += 1
                    prd_id = str(frontmatter.get("id", prd_file.stem))
                    untraced.append(prd_id)

            # Count FR requirements in body
            fr_pattern = r"###\s+\S+-FR\d+"
            fr_matches = re.findall(fr_pattern, content)
            total_reqs += len(fr_matches)

            # Check traceability matrix section
            if "Traceability Matrix" in content:
                # Count rows with implementation links
                matrix_section = content.split("Traceability Matrix")[-1]
                impl_refs = re.findall(r"`\w+\.py[:\w]*`", matrix_section)
                traced_reqs += min(len(impl_refs), len(fr_matches))

        total_reqs = max(total_reqs, 1)
        coverage = traced_reqs / total_reqs

        result = TraceabilityResult(
            total_requirements=total_reqs,
            traced_requirements=traced_reqs,
            untraced_requirements=untraced,
            coverage=coverage,
        )

        # FR09: Finding coverage analysis — flag prd_candidate
        # findings that have no target_prd linked yet.
        unlinked_findings = get_unlinked_findings()

        logger.info(
            "trw_traceability_checked",
            total=total_reqs,
            traced=traced_reqs,
            coverage=f"{coverage:.0%}",
            unlinked_findings=len(unlinked_findings),
        )

        return {
            "total_requirements": result.total_requirements,
            "traced_requirements": result.traced_requirements,
            "untraced_requirements": result.untraced_requirements,
            "coverage": result.coverage,
            "coverage_threshold": _config.traceability_coverage_min,
            "passes_gate": coverage >= _config.traceability_coverage_min,
            "prd_files_analyzed": len(prd_files),
            "unlinked_findings": unlinked_findings,
            "unlinked_findings_count": len(unlinked_findings),
        }

    @server.tool()
    def trw_prd_status_update(
        prd_id: str,
        target_status: str,
        force: bool = False,
        reason: str = "",
    ) -> dict[str, object]:
        """Update a PRD's lifecycle status with state machine validation and guard checks.

        Validates the transition against the PRD status state machine, runs
        applicable guard checks (content density for DRAFT->REVIEW, quality
        validation for REVIEW->APPROVED), and updates the PRD frontmatter.

        Args:
            prd_id: PRD identifier (e.g., "PRD-CORE-009").
            target_status: Target PRDStatus value (e.g., "review", "approved").
            force: Admin override that bypasses guard checks (not state machine).
            reason: Optional justification (required for backward transitions and force).
        """
        # Validate target status
        try:
            target = PRDStatus(target_status.lower())
        except ValueError:
            valid_statuses = [s.value for s in PRDStatus]
            raise ValidationError(
                f"Invalid target status: {target_status!r}. Valid: {valid_statuses}",
                target_status=target_status,
            )

        # Resolve PRD file path
        prd_path = _resolve_prd_path(prd_id)
        content = prd_path.read_text(encoding="utf-8")

        # Parse current status from frontmatter
        frontmatter = _parse_frontmatter(content)
        current_status_str = str(frontmatter.get("status", "draft")).lower()
        try:
            current = PRDStatus(current_status_str)
        except ValueError:
            current = PRDStatus.DRAFT

        # PRD-FIX-009-FR02: Require non-empty reason when force=True
        if force and not reason.strip():
            raise ValidationError(
                "reason is required when force=True",
                prd_id=prd_id,
                target_status=target_status,
            )

        # PRD-FIX-009: State machine is ALWAYS enforced — force only bypasses guards
        transition_valid = is_valid_transition(current, target)
        if not transition_valid:
            return {
                "prd_id": prd_id,
                "previous_status": current.value,
                "new_status": current.value,
                "transition_valid": False,
                "guard_passed": False,
                "force_used": force,
                "reason": f"Invalid transition: {current.value} -> {target.value}",
                "updated": False,
            }

        # Run guard checks (skip if force=True or identity transition)
        guard_passed = True
        guard_reason = ""
        guard_details: dict[str, object] = {}

        if current != target:
            if force:
                guard_passed = True
                guard_reason = f"Guard bypassed (force=True). Reason: {reason}"
            elif transition_valid:
                guard_result = check_transition_guards(current, target, content, _config)
                guard_passed = guard_result.allowed
                guard_reason = guard_result.reason
                guard_details = guard_result.guard_details

        if not guard_passed:
            return {
                "prd_id": prd_id,
                "previous_status": current.value,
                "new_status": current.value,
                "transition_valid": transition_valid,
                "guard_passed": False,
                "force_used": False,
                "reason": guard_reason,
                "guard_details": guard_details,
                "updated": False,
            }

        # Update frontmatter
        if current != target:
            update_frontmatter(prd_path, {
                "status": target.value,
                "dates": {"updated": str(date.today())},
            })

        # Log event to latest run's events.jsonl (best-effort)
        _log_status_change_event(
            prd_id=prd_id,
            previous_status=current.value,
            new_status=target.value,
            force_used=force,
            reason=reason,
        )

        # Auto-sync INDEX.md/ROADMAP.md so catalogue stays current
        index_synced = False
        if current != target and _config.index_auto_sync_on_status_change:
            index_synced = _auto_sync_index()

        logger.info(
            "trw_prd_status_updated",
            prd_id=prd_id,
            previous_status=current.value,
            new_status=target.value,
            force_used=force,
        )

        return {
            "prd_id": prd_id,
            "previous_status": current.value,
            "new_status": target.value,
            "transition_valid": transition_valid,
            "guard_passed": guard_passed,
            "force_used": force,
            "reason": guard_reason or reason,
            "guard_details": guard_details,
            "updated": current != target,
            "index_synced": index_synced,
        }

    @server.tool()
    def trw_index_sync(
        sync_roadmap: bool = True,
    ) -> dict[str, object]:
        """Sync INDEX.md and ROADMAP.md PRD catalogues from PRD frontmatter.

        Scans all PRD files, extracts status/priority/title from YAML
        frontmatter, and updates the catalogue sections using marker-based
        merge. Content outside markers is preserved.

        Args:
            sync_roadmap: Whether to also sync ROADMAP.md (default True).
        """
        from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md

        project_root = resolve_project_root()
        prds_dir = project_root / Path(_config.prds_relative_path)
        aare_dir = prds_dir.parent  # docs/requirements-aare-f/

        index_path = aare_dir / "INDEX.md"
        index_result = sync_index_md(index_path, prds_dir, writer=_writer)

        roadmap_result: dict[str, object] = {}
        if sync_roadmap:
            roadmap_path = aare_dir / "ROADMAP.md"
            roadmap_result = sync_roadmap_md(roadmap_path, prds_dir, writer=_writer)

        logger.info(
            "trw_index_synced",
            total_prds=index_result.get("total_prds", 0),
            sync_roadmap=sync_roadmap,
        )

        return {
            "index": index_result,
            "roadmap": roadmap_result if sync_roadmap else "skipped",
        }

    @server.tool()
    def trw_prd_groom(
        prd_path: str,
        research_scope: str = "full",
        max_iterations: int = 5,
        target_completeness: float = 0.85,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """Analyze a PRD and generate a structured grooming plan.

        Phase 1 (always): Parse PRD, identify placeholder sections, score
        quality, and generate a grooming plan with research topics.

        Phase 2 (dry_run=False): Returns the plan with status 'plan_ready',
        indicating the orchestrator should launch the prd-groomer agent.

        Args:
            prd_path: Absolute path to the PRD markdown file to groom.
            research_scope: Research depth: 'full', 'codebase', or 'minimal'.
            max_iterations: Maximum validation-fix iterations (1-10).
            target_completeness: Minimum completeness score to accept (0.0-1.0).
            dry_run: If True, return plan without signaling agent launch.
        """
        from trw_mcp.state.grooming import generate_grooming_plan

        path = Path(prd_path).resolve()
        if not path.exists():
            raise StateError(f"PRD file not found: {path}", path=str(path))

        # Validate parameters
        if max_iterations < 1 or max_iterations > 10:
            raise ValidationError(
                f"max_iterations must be 1-10, got {max_iterations}",
                max_iterations=max_iterations,
            )
        if target_completeness < 0.0 or target_completeness > 1.0:
            raise ValidationError(
                f"target_completeness must be 0.0-1.0, got {target_completeness}",
                target_completeness=target_completeness,
            )
        valid_scopes = ("full", "codebase", "minimal")
        if research_scope not in valid_scopes:
            raise ValidationError(
                f"Invalid research_scope: {research_scope!r}. Valid: {list(valid_scopes)}",
                research_scope=research_scope,
            )

        content = path.read_text(encoding="utf-8")

        # Phase 1: Generate grooming plan (pure function)
        plan = generate_grooming_plan(
            content=content,
            prd_path=str(path),
            config=_config,
            max_iterations=max_iterations,
            target_completeness=target_completeness,
            research_scope=research_scope,
        )

        # Get current quality via trw_prd_validate internals
        v2_result = validate_prd_quality_v2(content, _config)
        ambiguous_terms = _detect_ambiguity(content)
        sections = _extract_sections(content)

        current_quality: dict[str, object] = {
            "total_score": v2_result.total_score,
            "quality_tier": v2_result.quality_tier,
            "grade": v2_result.grade,
            "completeness_score": v2_result.completeness_score,
            "sections_found": sections,
            "improvement_suggestions": [
                {
                    "dimension": s.dimension,
                    "priority": s.priority,
                    "message": s.message,
                }
                for s in v2_result.improvement_suggestions[:5]
            ],
        }

        status = "plan_generated" if dry_run else "plan_ready"

        logger.info(
            "trw_prd_groom_complete",
            prd_id=plan.prd_id,
            status=status,
            sections_needing_work=len(plan.sections_needing_work),
            dry_run=dry_run,
        )

        return {
            "prd_id": plan.prd_id,
            "status": status,
            "grooming_plan": model_to_dict(plan),
            "current_quality": current_quality,
            "suggested_agent": "prd-groomer",
        }


# --- Private helpers ---


def _parse_frontmatter(content: str) -> dict[str, object]:
    """Parse YAML frontmatter from markdown content.

    Delegates to :func:`trw_mcp.state.prd_utils.parse_frontmatter`.
    """
    return _parse_frontmatter_impl(content)


def _extract_sections(content: str) -> list[str]:
    """Extract ## section headings from PRD markdown content.

    Delegates to :func:`trw_mcp.state.prd_utils.extract_sections`.
    """
    return _extract_sections_impl(content)


def _detect_ambiguity(content: str) -> list[str]:
    """Detect ambiguous terms in PRD content.

    Delegates to :func:`trw_mcp.state.prd_utils.detect_ambiguity`.
    """
    return _detect_ambiguity_impl(content)


_CACHED_TEMPLATE_BODY: str | None = None
_CACHED_TEMPLATE_VERSION: str | None = None

_TEMPLATE_VERSION_RE = re.compile(r"\*Template version:\s*([\d.]+)")
_FILE_REF_RE = re.compile(r"[\w/]+\.py")


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
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

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

    # Extract goal-like sentences
    _GOAL_KW = re.compile(
        r"\b(goal|objective|achieve|deliver)\b", re.IGNORECASE,
    )
    _SLO_KW = re.compile(
        r"\b(slo|latency|availability|throughput)\b", re.IGNORECASE,
    )
    try:
        for sentence in re.split(r"[.\n]", input_text):
            stripped = sentence.strip()
            if not stripped:
                continue
            if _GOAL_KW.search(stripped):
                prefill["goals"].append(stripped)
            if _SLO_KW.search(stripped):
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

    # Insert file refs into Key Files table
    file_refs = prefill.get("file_refs", [])
    if file_refs:
        file_rows = "\n".join(
            f"| `{f}` | <!-- changes needed --> |" for f in file_refs
        )
        body = body.replace(
            "| `path/to/file.py` | {Description of changes} |",
            file_rows,
        )

    # Insert PRD deps into Dependencies table
    prd_deps = prefill.get("prd_deps", [])
    if prd_deps:
        dep_rows = "\n".join(
            f"| DEP-{i:03d} | {dep} | Pending | Yes |"
            for i, dep in enumerate(prd_deps, 1)
        )
        body = body.replace(
            "| DEP-001 | {Dependency} | Resolved/Pending | Yes/No |",
            dep_rows,
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
    seq = int(prd_id.split("-")[-1])
    body = _substitute_template(body, prd_id, title, category, seq, priority, confidence)
    prefill = _extract_prefill(input_text)
    body = _apply_prefill(body, prefill, input_text)
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
    from io import StringIO
    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    stream = StringIO()
    yaml.dump({"prd": frontmatter}, stream)
    yaml_str = stream.getvalue()

    return f"---\n{yaml_str}---\n\n{body}\n"


def _resolve_prd_path(prd_id: str) -> Path:
    """Resolve PRD file path from a PRD ID.

    Scans ``docs/requirements-aare-f/prds/`` for a file matching the ID.

    Args:
        prd_id: PRD identifier (e.g. ``PRD-CORE-009``).

    Returns:
        Resolved path to the PRD markdown file.

    Raises:
        StateError: If the PRD file is not found.
    """
    project_root = resolve_project_root()
    prds_dir = project_root / Path(_config.prds_relative_path)
    prd_file = prds_dir / f"{prd_id}.md"
    if prd_file.exists():
        return prd_file
    raise StateError(f"PRD file not found: {prd_file}", path=str(prd_file))


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


def _log_status_change_event(
    prd_id: str,
    previous_status: str,
    new_status: str,
    force_used: bool,
    reason: str,
    force_override: bool = False,
) -> None:
    """Log a prd_status_change event to the latest run's events.jsonl.

    Best-effort — logs debug/warning on failure but never raises.

    Args:
        prd_id: PRD identifier.
        previous_status: Status before the transition.
        new_status: Status after the transition.
        force_used: Whether the force override was used.
        reason: Justification for the transition.
        force_override: Whether force was used on an invalid transition.
    """
    # PRD-FIX-014: Use shared run path resolution (docs/*/runs/) instead
    # of the non-existent .trw/runs/ path.
    try:
        from trw_mcp.state._paths import resolve_run_path
        from trw_mcp.state.persistence import FileEventLogger

        resolved_path = resolve_run_path(None)
        events_path = resolved_path / "meta" / _config.events_file
        event_data: dict[str, object] = {
            "prd_id": prd_id,
            "previous_status": previous_status,
            "new_status": new_status,
            "force_used": force_used,
            "reason": reason,
        }
        if force_override:
            event_data["force_override"] = True
        event_logger = FileEventLogger(_writer)
        event_logger.log_event(
            events_path,
            "prd_status_change",
            event_data,
        )
    except StateError:
        # PRD-FIX-014-FR03: No active run — valid scenario, debug-level only
        logger.debug(
            "no_active_run_for_event",
            prd_id=prd_id,
        )
    except Exception as exc:
        # PRD-FIX-014-FR02: Log warning instead of silently swallowing
        logger.warning(
            "status_change_event_failed",
            prd_id=prd_id,
            error=str(exc),
        )
