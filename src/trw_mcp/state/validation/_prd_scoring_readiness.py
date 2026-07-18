"""PRD scoring — implementation-readiness dimension scorer.

Belongs to the ``_prd_scoring.py`` facade. Re-exported there for back-compat.

Variant-aware scorer rewarding concrete planning evidence (control points,
behavior switches, key files, verification tests, completion/migration
semantics). Variant branches: feature/infrastructure, fix, research,
plus a content_docs profile path. Filesystem-grounding penalty
(PRD-QUAL-063) caps the final score when content references hallucinated
paths.

Extracted as DIST-243 batch 63.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from trw_mcp.models.config import get_config
from trw_mcp.models.requirements import DimensionScore
from trw_mcp.state.validation._prd_scoring_counts import (
    _count_impl_refs,
    _count_planned_requirements,
    _count_test_refs,
    _count_verification_commands,
)
from trw_mcp.state.validation._prd_scoring_fr import (
    _extract_fr_sections,
    _score_assertion_coverage,
)
from trw_mcp.state.validation._prd_scoring_grounding import compute_grounding_penalty
from trw_mcp.state.validation._prd_scoring_parsing import (
    _extract_subheadings,
    _validation_profile,
)
from trw_mcp.state.validation._prd_scoring_traceability import _count_table_rows
from trw_mcp.state.validation.template_variants import get_variant_for_category

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig


def score_implementation_readiness(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
    project_root: Path | None = None,
) -> DimensionScore:
    """Score execution-readiness signals distinct from raw prose density."""
    _config = config or get_config()
    max_score = _config.validation_implementation_readiness_weight
    if not content:
        return DimensionScore(
            name="implementation_readiness",
            score=0.0,
            max_score=max_score,
            details={"variant": "feature"},
        )

    category = str(frontmatter.get("category", ""))
    variant = get_variant_for_category(category)
    fr_sections = _extract_fr_sections(content)
    fr_count = _count_planned_requirements(content, fr_sections)
    impl_refs = _count_impl_refs(content)
    test_refs = _count_test_refs(content)
    verification_commands = _count_verification_commands(content)
    present_subheadings = _extract_subheadings(content)

    completion_ratio = (
        sum(
            1
            for heading in (
                "Completion Evidence (Definition of Done)",
                "Migration / Backward Compatibility",
            )
            if heading in present_subheadings
        )
        / 2
    )

    details: dict[str, object] = {
        "variant": variant,
        "fr_count": fr_count,
        "implementation_refs": impl_refs,
        "test_refs": test_refs,
        "verification_commands": verification_commands,
        "completion_ratio": round(completion_ratio, 4),
    }

    profile = _validation_profile(frontmatter)
    if profile:
        details["validation_profile"] = profile

    # Verification design is method-neutral for every PRD variant. Test-shaped
    # references remain diagnostics but never earn unique readiness credit.
    try:
        from trw_mcp.state.validation._verification_mappings import validate_verification_mappings

        _, verification_design_ratio = validate_verification_mappings(
            frontmatter,
            content,
            effective_risk_level="medium",
        )
    except Exception:  # justified: scoring remains fail-open and bounded
        verification_design_ratio = 0.0
    details["verification_design_ratio"] = round(verification_design_ratio, 4)
    details["verification_design_semantics"] = "planned_method_neutral"

    if profile == "content_docs":
        file_path_ratio = min(impl_refs / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        assertion_ratio = _score_assertion_coverage(content, fr_sections)
        verification_ratio = min(verification_commands / fr_count, 1.0)
        rollout_ratio = (
            1.0 if "Rollout Plan" in content and "Rollback" in content else 0.5 if "Rollout Plan" in content else 0.0
        )
        completion_ratio = (
            1.0 if "Success Metrics" in content and "Traceability Matrix" in content else completion_ratio
        )
        composite = (
            file_path_ratio * 0.30
            + assertion_ratio * 0.25
            + verification_design_ratio * 0.25
            + rollout_ratio * 0.10
            + completion_ratio * 0.10
        )
        details.update(
            {
                "file_path_ratio": round(file_path_ratio, 4),
                "test_ref_ratio": round(test_ref_ratio, 4),
                "assertion_ratio": round(assertion_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
                "rollout_ratio": round(rollout_ratio, 4),
                "completion_ratio": round(completion_ratio, 4),
            }
        )
    elif variant in {"feature", "infrastructure"}:
        control_point_rows = _count_table_rows(content, "Primary Control Points")
        behavior_switch_rows = _count_table_rows(content, "Behavior Switch Matrix")
        key_files_rows = _count_table_rows(content, "Key Files")
        test_subsections = (
            "Unit Tests",
            "Integration Tests",
            "Acceptance Tests",
            "Regression Tests",
            "Negative / Fallback Tests",
        )
        test_subsection_ratio = sum(1 for heading in test_subsections if heading in present_subheadings) / len(
            test_subsections
        )
        control_ratio = min(control_point_rows / fr_count, 1.0)
        behavior_switch_ratio = min(behavior_switch_rows / fr_count, 1.0)
        file_map_ratio = min(max(key_files_rows, impl_refs) / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        verification_ratio = min(verification_commands / fr_count, 1.0)
        test_plan_ratio = (test_subsection_ratio * 0.5) + (test_ref_ratio * 0.3) + (verification_ratio * 0.2)
        composite = (
            control_ratio * 0.20
            + behavior_switch_ratio * 0.20
            + file_map_ratio * 0.20
            + verification_design_ratio * 0.25
            + completion_ratio * 0.15
        )
        details.update(
            {
                "control_point_rows": control_point_rows,
                "behavior_switch_rows": behavior_switch_rows,
                "key_files_rows": key_files_rows,
                "control_ratio": round(control_ratio, 4),
                "behavior_switch_ratio": round(behavior_switch_ratio, 4),
                "file_map_ratio": round(file_map_ratio, 4),
                "test_subsection_ratio": round(test_subsection_ratio, 4),
                "test_ref_ratio": round(test_ref_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
                "test_plan_ratio": round(test_plan_ratio, 4),
            }
        )
    elif variant == "fix":
        root_cause_ratio = (
            sum(
                1
                for heading in ("Root Cause", "Contributing Factors", "Fix Verification")
                if heading in present_subheadings
            )
            / 3
        )
        regression_ratio = (
            sum(1 for heading in ("Regression Tests", "Negative / Fallback Tests") if heading in present_subheadings)
            / 2
        )
        file_map_ratio = min(max(impl_refs, 1 if "Key Files" in present_subheadings else 0) / fr_count, 1.0)
        test_ref_ratio = min(test_refs / fr_count, 1.0)
        verification_ratio = min(max(test_ref_ratio, verification_commands / fr_count), 1.0)
        completion_ratio = (completion_ratio * 0.8) + (verification_design_ratio * 0.2)
        composite = (
            root_cause_ratio * 0.30
            + regression_ratio * 0.20
            + file_map_ratio * 0.20
            + verification_design_ratio * 0.15
            + completion_ratio * 0.15
        )
        details.update(
            {
                "root_cause_ratio": round(root_cause_ratio, 4),
                "regression_ratio": round(regression_ratio, 4),
                "file_map_ratio": round(file_map_ratio, 4),
                "verification_ratio": round(verification_ratio, 4),
            }
        )
    else:
        present_subheadings_lower = {sub.lower() for sub in present_subheadings}
        research_ratio = (
            sum(
                1
                for heading in ("Approach", "Data Sources", "Evaluation Criteria")
                if any(heading.lower() in sub for sub in present_subheadings_lower)
            )
            / 3
        )
        evidence_ratio = verification_design_ratio
        composite = (research_ratio * 0.65) + (evidence_ratio * 0.20) + (completion_ratio * 0.15)
        details.update(
            {
                "research_ratio": round(research_ratio, 4),
                "evidence_ratio": round(evidence_ratio, 4),
            }
        )

    score = composite * max_score

    if project_root is not None:
        penalty_mult, hallucinated = compute_grounding_penalty(content, project_root)
        if hallucinated:
            score *= penalty_mult
            details["grounding_penalty_mult"] = round(penalty_mult, 4)
            details["hallucinated_paths"] = len(hallucinated)
            suggestions: list[str] = details.get("suggestions", [])  # type: ignore[assignment]
            suggestions.append(
                f"Remove or fix {len(hallucinated)} non-existent file paths (e.g. {hallucinated[0]}) to improve technical grounding."
            )
            details["suggestions"] = suggestions

    return DimensionScore(
        name="implementation_readiness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details=details,
    )
