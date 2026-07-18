"""TRW AARE-F requirements tools --- prd_create, prd_validate.

These 2 tools codify the AARE-F Framework requirements engineering
process as executable MCP tools.

Template processing helpers live in ``_prd_template_helpers.py`` and are
re-exported here for backward-compatible test imports.
"""

from __future__ import annotations

import time
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
    PRDVerification,
    Priority,
    RiskLevel,
    VerificationMapping,
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
    find_identity_collisions,
    next_prd_sequence,
    parse_frontmatter,
)
from trw_mcp.state.validation import (
    refresh_dynamic_prd_validation,
    validate_prd_quality_v2,
)
from trw_mcp.state.validation.template_variants import get_required_sections
from trw_mcp.tools._prd_validate_payload import build_validate_payload
from trw_mcp.tools._prd_validation_cache import (
    CacheBounds as _PRDValidationCacheBounds,
)
from trw_mcp.tools._prd_validation_cache import (
    cache_key as _prd_validation_cache_key,
)
from trw_mcp.tools._prd_validation_cache import (
    cache_metadata as _prd_validation_cache_metadata,
)
from trw_mcp.tools._prd_validation_cache import (
    cache_path as _prd_validation_cache_path,
)
from trw_mcp.tools._prd_validation_cache import (
    load_pure_result_with_reason,
    retire_legacy_cache,
    store_pure_result,
)
from trw_mcp.tools.telemetry import log_tool_call

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
        verification_mappings: list[dict[str, object]] | None = None,
    ) -> PrdCreateResultDict:
        """Generate an AARE-F compliant PRD from a feature description.

        Use when:
        - You have a feature request or requirements and need a structured PRD.
        - Before writing code for a P0/P1/P2 feature or risky behavioral change.
        - You want auto-incremented PRD ID, YAML frontmatter, and catalogue sync.

        Produces category-appropriate sections, confidence scores, traceability
        links, and typed AARE-F 3.2 verification mappings.
        Updates INDEX.md/ROADMAP.md when ``index_auto_sync_on_status_change`` is on.

        Input:
        - input_text: feature request or description (becomes Problem Statement + Background).
        - category: one of CORE, QUAL, INFRA, LOCAL, EXPLR, RESEARCH, FIX (plus any
          values added to ``.trw/config.yaml::extra_prd_categories``).
        - priority: P0, P1, P2, or P3 — drives base confidence scores.
        - title: auto-generated from input_text when empty.
        - sequence: auto-increments from existing catalogue when default (1).
        - risk_level: optional critical|high|medium|low — scales validation strictness.
        - verification_mappings: optional list of mappings with requirement_id,
          acceptance_criteria, method (test|analysis|inspection|demonstration),
          evidence_artifact, pass_condition, and optional automation fields.

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
            "sections_generated": len(get_required_sections(category)),
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
        fast: bool = False,
        verbose: bool = False,
    ) -> ValidateResultDict:
        """Score a PRD against the V2 validation suite before implementation.

        Use when:
        - A PRD just landed and you need a READY / NEEDS-WORK verdict before coding.
        - You want ambiguity / completeness / traceability gates checked in one call.

        Runs structure compliance, content quality, AARE-F compliance, and
        ambiguity analysis. Catches issues here that would otherwise cause rework.

        Input:
        - prd_path: path to the PRD markdown file (required).
        - fast: when True (PRD-FIX-112), skip the repo-grounded dynamic checks
          entirely and return a visibly PARTIAL result (``validation_partial=true``,
          ``checks_skipped`` naming every dynamic group). Use for a quick
          text-only score; re-run without ``fast`` for a fully-grounded verdict.
        - verbose: when True, return the full diagnostic payload (per-occurrence
          ``smell_findings``, per-line ``ears_classifications`` with text, the
          cache addressing hashes, and the un-deduped ``wiring_gate_warnings``).
          The default compact response groups/caps those diagnostics to cut token
          cost; scoring and gate verdicts are identical in both modes.

        Every call is bounded by ``prd_validate_budget_seconds`` (default 60s):
        if the dynamic checks exceed the budget the remaining groups are skipped
        and the result is flagged ``validation_partial`` rather than hanging.

        Output: ValidateResultDict with fields
        {total_score: float (0-100), quality_tier: str, grade: str,
         valid: bool, ambiguity_rate: float, completeness_score: float,
         traceability_coverage: float, measured_traceability_coverage: float,
         verification_mapping_coverage: float, prd_status: str,
         improvement_suggestions: list[ImprovementSuggestionDict],
         failures: list[ValidationFailureDict], dimensions: list[DimensionScoreDict],
         path: str, sections_found: list[str], sections_expected: list[str],
         smell_findings: list[dict] (grouped-by-category in compact mode),
         ears_classifications: dict (counts + actionable_lines) in compact mode,
         section_scores: list[SectionScoreDict],
         effective_risk_level: str, risk_scaled: bool, compact: bool,
         status_drift_warnings: list[str], integrity_warnings: list[str],
         validation_partial: bool, checks_skipped: list[str], cache: dict}.

        validation_partial is True (PRD-FIX-112) when fast mode was requested or
        the budget was exceeded mid-run; checks_skipped then names the skipped
        dynamic check groups and integrity_warnings carries a loud
        ``validation_partial:`` marker. A partial result is never a silent pass.

        quality_tier values: "skeleton" | "draft" | "review" | "approved"
        (QualityTier enum; no "PRODUCTION" tier exists).

        Example:
            trw_prd_validate(prd_path="docs/requirements-aare-f/prds/PRD-QUAL-074.md")
            → {"total_score": 87, "quality_tier": "approved", "grade": "A",
               "valid": true, "improvement_suggestions": []}
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

        config = get_config()
        cache_path = _prd_validation_cache_path(project_root)
        cache_bounds = _PRDValidationCacheBounds.from_config(config)
        # One-time retirement of the disposable legacy monolithic YAML cache.
        try:
            retire_legacy_cache(project_root)
        except Exception:  # justified: legacy retirement never blocks validation
            logger.debug("prd_validation_legacy_retire_failed", exc_info=True)
        cache_metadata = _prd_validation_cache_metadata(content, config)
        cache_key = _prd_validation_cache_key(content, config)
        try:
            pure_result, cache_miss_reason = load_pure_result_with_reason(
                cache_path, cache_key, max_entry_bytes=cache_bounds.max_entry_bytes
            )
        except Exception:  # justified: any cache-load fault degrades to a miss
            logger.debug("prd_validation_cache_load_failed", path=str(cache_path), exc_info=True)
            pure_result, cache_miss_reason = None, "corrupt"
        cache_hit = pure_result is not None

        if pure_result is None:
            pure_result = validate_prd_quality_v2(content, config, include_dynamic_checks=False)
            try:
                store_pure_result(cache_path, cache_key, pure_result, bounds=cache_bounds)
            except Exception:  # justified: cache failure degrades to fresh validation
                logger.debug("prd_validation_cache_write_failed", path=str(cache_path), exc_info=True)

        # Repository, wiring, duplicate, and seam-expiry truth is deliberately
        # recomputed on every call even when pure text scoring is a cache hit.
        # PRD-FIX-112: bound the dynamic portion by a monotonic-clock deadline so
        # a future slowdown can never re-train gate bypass; ``fast`` skips the
        # dynamic portion entirely. Both surface the SAME visibly-partial shape.
        budget_report: dict[str, object] = {}
        deadline = time.monotonic() + max(float(config.prd_validate_budget_seconds), 0.0)
        v2_result = refresh_dynamic_prd_validation(
            pure_result,
            content,
            config=config,
            project_root=str(project_root),
            deadline=deadline,
            fast=fast,
            budget_report=budget_report,
        )

        sections = _extract_sections(content)
        frontmatter = parse_frontmatter(content)
        sections_expected = get_required_sections(str(frontmatter.get("category", "") or ""))

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
        if v2_result.completeness_score < _min_threshold:
            logger.warning(
                "prd_validate_below_threshold",
                prd_id=_prd_id_str,
                score=v2_result.completeness_score,
                threshold=_min_threshold,
            )

        validate_result: ValidateResultDict = build_validate_payload(
            v2_result,
            path=path,
            sections=sections,
            sections_expected=sections_expected,
            frontmatter=frontmatter,
            cache_hit=cache_hit,
            cache_key=cache_key,
            cache_miss_reason=cache_miss_reason,
            cache_metadata=cache_metadata,
            verbose=verbose,
        )

        # PRD-FIX-112: surface the budget/fast partial markers on the wire dict.
        # The loud ``validation_partial:`` warning already rides in
        # integrity_warnings (copied by build_validate_payload); these two fields
        # are the machine-readable form. Default calls (no fast, generous budget)
        # yield validation_partial=False + checks_skipped=[] — byte-identical
        # otherwise to the pre-change payload.
        validate_result["validation_partial"] = bool(budget_report.get("validation_partial", False))
        _skipped = budget_report.get("checks_skipped", [])
        validate_result["checks_skipped"] = _skipped if isinstance(_skipped, list) else []

        # Substrate-First gate (PRD-DIST-218 FR-2). Heuristic check:
        # flag PRDs that propose module-level hardcoded vocabulary
        # collections without an acknowledged justification.
        try:
            from trw_mcp.tools._substrate_first_check import substrate_first_check

            substrate_result = substrate_first_check(content)
            validate_result["substrate_first"] = substrate_result.to_payload()
            if substrate_result.verdict == "fail":
                logger.warning(
                    "substrate_first_gate_fail",
                    prd_id=_prd_id_str,
                    flagged_count=len(substrate_result.flagged_collections),
                )
        except Exception:  # justified: gate must not break prd_validate
            logger.debug("substrate_first_check_skipped", exc_info=True)

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
