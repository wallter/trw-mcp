"""AARE-F 3.2 typed requirement-to-verification contract validation."""

from __future__ import annotations

import re

from trw_mcp.models.requirements import ValidationFailure, VerificationMapping

_REQUIREMENT_ID_RE = re.compile(
    r"^###\s+((?:[A-Za-z0-9][A-Za-z0-9_-]*-)?(?:FR|NFR)\d+):(?:\s|$)",
    re.MULTILINE,
)


def _uses_aaref_32_verification_contract(frontmatter: dict[str, object]) -> bool:
    """Return whether a PRD opts into the enforceable AARE-F 3.2 template."""
    raw = str(frontmatter.get("template_version", "") or "")
    match = re.match(r"^(\d+)(?:\.(\d+))?", raw)
    if match is None:
        return False
    return (int(match.group(1)), int(match.group(2) or 0)) >= (3, 2)


def validate_verification_mappings(
    frontmatter: dict[str, object],
    content: str,
    *,
    effective_risk_level: str,
) -> tuple[list[ValidationFailure], float]:
    """Validate AARE-F 3.2 AC/method/evidence/pass-condition mappings.

    Current-template Critical/High PRDs treat missing or malformed mappings as
    errors. Lower-risk and legacy PRDs receive migration warnings. Research
    variants without requirements have full coverage by definition. Requirement
    IDs are matched exactly for both functional and non-functional requirements.
    """
    requirement_ids = list(dict.fromkeys(_REQUIREMENT_ID_RE.findall(content)))
    if not requirement_ids:
        return [], 1.0

    current_contract = _uses_aaref_32_verification_contract(frontmatter)
    blocks = current_contract and effective_risk_level.lower() in {"critical", "high"}
    severity = "error" if blocks else "warning"
    failures: list[ValidationFailure] = []
    verification = frontmatter.get("verification", {})
    raw_mappings = verification.get("mappings", []) if isinstance(verification, dict) else []
    if not isinstance(raw_mappings, list):
        raw_mappings = []

    mappings: dict[str, VerificationMapping] = {}
    lifecycle_status = str(frontmatter.get("status", "") or "").lower()
    for index, raw in enumerate(raw_mappings):
        if not isinstance(raw, dict):
            failures.append(
                ValidationFailure(
                    field=f"verification.mappings[{index}]",
                    rule="verification_mapping_schema",
                    message=(
                        "Verification mapping must be an object with requirement_id, acceptance_criteria, "
                        "method, evidence_artifact, and pass_condition."
                    ),
                    severity=severity,
                )
            )
            continue
        try:
            mapping = VerificationMapping.model_validate(raw, strict=False)
        except Exception as exc:  # Pydantic supplies actionable field detail
            failures.append(
                ValidationFailure(
                    field=f"verification.mappings[{index}]",
                    rule="verification_mapping_schema",
                    message=f"Invalid verification mapping: {exc}",
                    severity=severity,
                )
            )
            continue
        if mapping.requirement_id in mappings:
            failures.append(
                ValidationFailure(
                    field=f"verification.mappings[{index}].requirement_id",
                    rule="verification_mapping_duplicate",
                    message=f"Duplicate verification mapping for {mapping.requirement_id}.",
                    severity=severity,
                )
            )
            continue
        mappings[mapping.requirement_id] = mapping

        if mapping.automated is False and not mapping.automation_infeasible_reason:
            failures.append(
                ValidationFailure(
                    field=f"verification:{mapping.requirement_id}",
                    rule="automation_exception_reason",
                    message=(
                        f"{mapping.requirement_id} declares automated=false without "
                        "automation_infeasible_reason. Record why another verification method is required."
                    ),
                    severity=severity,
                )
            )
        if (
            lifecycle_status in {"implemented", "done"}
            and mapping.automated is None
            and not mapping.automation_infeasible_reason
        ):
            failures.append(
                ValidationFailure(
                    field=f"verification:{mapping.requirement_id}",
                    rule="implemented_requirement_automation",
                    message=(
                        f"{mapping.requirement_id} is implemented but has neither automated behavioral evidence "
                        "nor an automation_infeasible_reason for its alternate verification method."
                    ),
                    severity=severity,
                )
            )

    missing = [requirement_id for requirement_id in requirement_ids if requirement_id not in mappings]
    for requirement_id in missing:
        migration = (
            "" if current_contract else " Legacy PRD: migrate to template_version 3.2 before blocking this gate."
        )
        failures.append(
            ValidationFailure(
                field=f"verification:{requirement_id}",
                rule="verification_mapping_required",
                message=(
                    f"{requirement_id} has no typed verification mapping (acceptance criteria, method, "
                    f"evidence artifact, and pass condition are required).{migration}"
                ),
                severity=severity,
            )
        )

    failures.extend(
        ValidationFailure(
            field=f"verification:{mapped_id}",
            rule="verification_mapping_orphan",
            message=f"Verification mapping references unknown requirement {mapped_id}.",
            severity="warning",
        )
        for mapped_id in sorted(set(mappings) - set(requirement_ids))
    )
    coverage = (len(requirement_ids) - len(missing)) / len(requirement_ids)
    return failures, coverage


__all__ = ["validate_verification_mappings"]
