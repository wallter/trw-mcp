"""Schema validation and output contract checking.

Validates shard output contracts, phase exit criteria,
and PRD quality gates against framework specifications.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import structlog

from trw_mcp.exceptions import ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDQualityGates, ValidationFailure, ValidationResult
from trw_mcp.models.run import (
    OutputContract,
    Phase,
    ShardCard,
    ShardStatus,
    WaveEntry,
    WaveStatus,
)

logger = structlog.get_logger()

# Phase exit criteria descriptions (from FRAMEWORK.md §PHASES)
PHASE_EXIT_CRITERIA: dict[str, list[str]] = {
    "research": [
        "All research shards complete or partial with findings",
        "Research synthesis produced",
        "Open questions documented",
    ],
    "plan": [
        "Plan document drafted (plan.md)",
        "Wave manifest defined",
        "Shard cards specified with output contracts",
    ],
    "implement": [
        "All implementation shards complete",
        "Output contracts validated",
        "No failed shards without recovery plan",
    ],
    "validate": [
        "Tests pass with required coverage",
        "Output contracts verified",
        "Risk register updated",
    ],
    "review": [
        "All findings reviewed",
        "Confidence levels assessed",
        "Final report drafted",
        "Reflection completed (reflection event in events.jsonl)",
    ],
    "deliver": [
        "Final report complete",
        "Artifacts organized",
        "Run state marked complete",
        "CLAUDE.md synced (claude_md_sync event in events.jsonl)",
    ],
}


class ContractValidator(Protocol):
    """Validate output contracts for shards."""

    def validate_contract(
        self,
        contract: OutputContract,
        base_path: Path,
    ) -> list[ValidationFailure]: ...


class FileContractValidator:
    """File-based output contract validator.

    Checks that declared output files exist and contain required keys.
    """

    def validate_contract(
        self,
        contract: OutputContract,
        base_path: Path,
    ) -> list[ValidationFailure]:
        """Validate a single output contract against the filesystem.

        Args:
            contract: Output contract to validate.
            base_path: Base directory to resolve relative file paths.

        Returns:
            List of validation failures (empty if valid).
        """
        failures: list[ValidationFailure] = []
        file_path = base_path / contract.file

        if not file_path.exists():
            if contract.required:
                failures.append(
                    ValidationFailure(
                        field=contract.file,
                        rule="file_exists",
                        message=f"Required output file missing: {contract.file}",
                        severity="error",
                    )
                )
            return failures

        # Check schema keys if specified
        if contract.schema_keys:
            from trw_mcp.state.persistence import FileStateReader

            reader = FileStateReader()
            try:
                data = reader.read_yaml(file_path)
                for key in contract.schema_keys:
                    if key not in data:
                        failures.append(
                            ValidationFailure(
                                field=f"{contract.file}:{key}",
                                rule="required_key",
                                message=f"Required key missing in {contract.file}: {key}",
                                severity="error",
                            )
                        )
            except Exception as exc:
                failures.append(
                    ValidationFailure(
                        field=contract.file,
                        rule="parseable",
                        message=f"Failed to parse {contract.file}: {exc}",
                        severity="error",
                    )
                )

        logger.debug(
            "contract_validated",
            file=contract.file,
            failures=len(failures),
        )
        return failures


def validate_wave_contracts(
    wave: WaveEntry,
    shards: list[ShardCard],
    base_path: Path,
    validator: ContractValidator | None = None,
) -> list[ValidationFailure]:
    """Validate all output contracts for shards in a wave.

    Args:
        wave: Wave entry to validate.
        shards: Shard cards belonging to this wave.
        base_path: Base directory to resolve file paths.
        validator: Contract validator to use. Defaults to FileContractValidator.

    Returns:
        List of all validation failures across all shards.

    Raises:
        ValidationError: If wave has no shards to validate.
    """
    if not shards:
        raise ValidationError(
            "No shards to validate for wave",
            wave=wave.wave,
        )

    _validator = validator or FileContractValidator()
    all_failures: list[ValidationFailure] = []

    for shard in shards:
        if shard.wave != wave.wave:
            continue
        status_val = shard.status if isinstance(shard.status, str) else shard.status.value
        if status_val not in (ShardStatus.COMPLETE.value, ShardStatus.PARTIAL.value):
            all_failures.append(
                ValidationFailure(
                    field=shard.id,
                    rule="shard_complete",
                    message=f"Shard {shard.id} not complete (status: {status_val})",
                    severity="error" if status_val == ShardStatus.FAILED.value else "warning",
                )
            )
            continue
        if shard.output_contract is not None:
            failures = _validator.validate_contract(shard.output_contract, base_path)
            all_failures.extend(failures)

    logger.info(
        "wave_validated",
        wave=wave.wave,
        shards_checked=len(shards),
        failures=len(all_failures),
    )
    return all_failures


def check_phase_exit(
    phase: Phase,
    run_path: Path,
    config: TRWConfig,
) -> ValidationResult:
    """Check exit criteria for a framework phase.

    Args:
        phase: Phase to validate exit criteria for.
        run_path: Path to the run directory.
        config: Framework configuration.

    Returns:
        ValidationResult with pass/fail and any failures.
    """
    failures: list[ValidationFailure] = []
    phase_name = phase.value
    criteria = PHASE_EXIT_CRITERIA.get(phase_name, [])

    meta_path = run_path / "meta"
    reports_path = run_path / "reports"
    scratch_path = run_path / "scratch"

    if phase_name == "research":
        # Check for research synthesis
        synthesis_path = scratch_path / "_orchestrator" / "research_synthesis.md"
        if not synthesis_path.exists():
            # Also check for alternative locations
            alt_path = reports_path / "research_synthesis.md"
            if not alt_path.exists():
                failures.append(
                    ValidationFailure(
                        field="research_synthesis",
                        rule="synthesis_exists",
                        message="Research synthesis document not found",
                        severity="warning",
                    )
                )

    elif phase_name == "plan":
        plan_path = reports_path / "plan.md"
        if not plan_path.exists():
            failures.append(
                ValidationFailure(
                    field="plan.md",
                    rule="plan_exists",
                    message="Plan document not found in reports/",
                    severity="error",
                )
            )

    elif phase_name == "implement":
        # Check that shards directory has content
        shards_path = run_path / "shards"
        if shards_path.exists():
            manifest = shards_path / "manifest.yaml"
            if not manifest.exists():
                failures.append(
                    ValidationFailure(
                        field="shards/manifest.yaml",
                        rule="manifest_exists",
                        message="Shard manifest not found",
                        severity="warning",
                    )
                )

    elif phase_name == "validate":
        validation_path = run_path / "validation"
        risk_register = validation_path / "risk-register.yaml"
        if not risk_register.exists():
            failures.append(
                ValidationFailure(
                    field="risk-register.yaml",
                    rule="risk_register_exists",
                    message="Risk register not found in validation/",
                    severity="warning",
                )
            )

    elif phase_name == "review":
        final_report = reports_path / "final.md"
        if not final_report.exists():
            failures.append(
                ValidationFailure(
                    field="final.md",
                    rule="final_report_exists",
                    message="Final report not found in reports/",
                    severity="warning",
                )
            )

        # Check for reflection event in events.jsonl
        events_path = meta_path / "events.jsonl"
        if events_path.exists():
            from trw_mcp.state.persistence import FileStateReader

            reader = FileStateReader()
            events = reader.read_jsonl(events_path)
            has_reflection = any(
                e.get("event") in ("reflection_complete", "trw_reflect_complete")
                for e in events
            )
            if not has_reflection:
                failures.append(
                    ValidationFailure(
                        field="reflection",
                        rule="reflection_required",
                        message="Reflection not completed — call trw_reflect() before advancing past REVIEW",
                        severity="warning",
                    )
                )
        else:
            failures.append(
                ValidationFailure(
                    field="reflection",
                    rule="reflection_required",
                    message="No events.jsonl found — reflection status unknown",
                    severity="warning",
                )
            )

    elif phase_name == "deliver":
        # Check run.yaml status
        run_yaml = meta_path / "run.yaml"
        if run_yaml.exists():
            from trw_mcp.state.persistence import FileStateReader

            reader = FileStateReader()
            try:
                state = reader.read_yaml(run_yaml)
                if state.get("status") != "complete":
                    failures.append(
                        ValidationFailure(
                            field="run.yaml:status",
                            rule="status_complete",
                            message="Run status not marked as complete",
                            severity="warning",
                        )
                    )
            except Exception:
                pass

        # Check for CLAUDE.md sync event
        events_path = meta_path / "events.jsonl"
        if events_path.exists():
            from trw_mcp.state.persistence import FileStateReader as _Reader

            sync_reader = _Reader()
            events = sync_reader.read_jsonl(events_path)
            has_sync = any(
                e.get("event") in ("claude_md_sync", "claude_md_synced")
                for e in events
            )
            if not has_sync:
                failures.append(
                    ValidationFailure(
                        field="claude_md_sync",
                        rule="sync_required",
                        message="CLAUDE.md not synced — call trw_claude_md_sync() before DELIVER",
                        severity="warning",
                    )
                )

    is_valid = not any(f.severity == "error" for f in failures)

    result = ValidationResult(
        valid=is_valid,
        failures=failures,
        completeness_score=1.0 - (len(failures) / max(len(criteria), 1)),
    )

    logger.info(
        "phase_exit_checked",
        phase=phase_name,
        valid=is_valid,
        failures=len(failures),
    )
    return result


def validate_prd_quality(
    frontmatter: dict[str, object],
    sections: list[str],
    gates: PRDQualityGates | None = None,
) -> ValidationResult:
    """Validate a PRD against AARE-F quality gates.

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
    for field in required_fields:
        if field not in frontmatter or not frontmatter[field]:
            failures.append(
                ValidationFailure(
                    field=f"frontmatter:{field}",
                    rule="required_field",
                    message=f"Required frontmatter field missing: {field}",
                    severity="error",
                )
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
        for field in confidence_fields:
            if field not in confidence:
                failures.append(
                    ValidationFailure(
                        field=f"confidence:{field}",
                        rule="confidence_present",
                        message=f"Missing confidence score: {field}",
                        severity="warning",
                    )
                )

    # Check traceability
    traceability = frontmatter.get("traceability", {})
    has_traces = False
    if isinstance(traceability, dict):
        for key in ("implements", "depends_on", "enables"):
            val = traceability.get(key, [])
            if isinstance(val, list) and len(val) > 0:
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
