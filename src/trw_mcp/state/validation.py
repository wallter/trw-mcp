"""Schema validation and output contract checking.

Validates shard output contracts, phase exit criteria,
PRD quality gates, and multi-dimensional semantic validation (PRD-CORE-008).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

import structlog

from trw_mcp.exceptions import StateError, ValidationError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import (
    DimensionScore,
    ImprovementSuggestion,
    PRDQualityGates,
    PRDStatus,
    QualityTier,
    SectionScore,
    SmellFinding,
    ValidationFailure,
    ValidationResult,
    ValidationResultV2,
)
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


def _check_prd_enforcement(
    run_path: Path,
    config: TRWConfig,
    required_status: PRDStatus,
    phase_name: str,
) -> list[ValidationFailure]:
    """Check PRD readiness for a phase gate.

    Discovers governing PRDs, checks their status against the required
    minimum, and returns failures with severity based on the enforcement level.

    Args:
        run_path: Path to the run directory.
        config: Framework configuration.
        required_status: Minimum PRD status required for this phase.
        phase_name: Phase name for error messages.

    Returns:
        List of ValidationFailure entries (may be empty).
    """
    from trw_mcp.state.prd_utils import discover_governing_prds, parse_frontmatter
    from trw_mcp.state._paths import resolve_project_root

    enforcement = config.phase_gate_enforcement

    # Skip if enforcement is off
    if enforcement == "off":
        return []

    # Check run_type — research runs skip PRD enforcement
    run_yaml = run_path / "meta" / "run.yaml"
    if run_yaml.exists():
        try:
            from trw_mcp.state.persistence import FileStateReader
            reader = FileStateReader()
            state = reader.read_yaml(run_yaml)
            if state.get("run_type") == "research":
                return []
        except (StateError, ValueError, TypeError) as exc:
            logger.debug("run_type_read_failed", path=str(run_yaml), error=str(exc))

    severity = "error" if enforcement == "strict" else "warning"
    failures: list[ValidationFailure] = []

    # Discover governing PRDs
    prd_ids = discover_governing_prds(run_path, config)

    if not prd_ids:
        failures.append(
            ValidationFailure(
                field="prd_scope",
                rule="prd_discovery",
                message=(
                    "No governing PRDs associated with this run. "
                    "Consider adding prd_scope to run.yaml."
                ),
                severity="warning",  # Advisory — always warning, never error
            )
        )
        return failures

    # Status ordering for comparison (PRD-FIX-008: includes done/merged)
    _STATUS_ORDER: dict[str, int] = {
        "draft": 0,
        "review": 1,
        "approved": 2,
        "implemented": 3,
        "done": 4,
        "merged": 4,
        "deprecated": 5,
    }
    required_order = _STATUS_ORDER.get(required_status.value, 0)

    # Check each PRD's status
    project_root = resolve_project_root()
    prds_dir = project_root / Path(config.prds_relative_path)

    for prd_id in prd_ids:
        prd_file = prds_dir / f"{prd_id}.md"
        if not prd_file.exists():
            failures.append(
                ValidationFailure(
                    field=f"prd:{prd_id}",
                    rule="prd_exists",
                    message=f"PRD file not found: {prd_id}",
                    severity=severity,
                )
            )
            continue

        try:
            content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            current_status = str(fm.get("status", "draft")).lower()
            current_order = _STATUS_ORDER.get(current_status, 0)

            if current_order < required_order:
                failures.append(
                    ValidationFailure(
                        field=f"prd:{prd_id}",
                        rule="prd_status",
                        message=(
                            f"{prd_id} status is '{current_status}' but "
                            f"'{required_status.value}' is required for {phase_name} phase"
                        ),
                        severity=severity,
                    )
                )
        except (OSError, StateError, ValueError, TypeError) as exc:
            logger.warning("prd_read_failed", prd_id=prd_id, error=str(exc))
            failures.append(
                ValidationFailure(
                    field=f"prd:{prd_id}",
                    rule="prd_readable",
                    message=f"Could not read/parse PRD: {prd_id}",
                    severity=severity,
                )
            )

    return failures


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

        # PRD enforcement: verify PRDs exist and are at least DRAFT (FR04)
        prd_failures = _check_prd_enforcement(
            run_path, config, PRDStatus.DRAFT, "plan",
        )
        failures.extend(prd_failures)

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

        # PRD enforcement: verify PRDs meet required status for implement (FR05)
        required_status_str = config.prd_required_status_for_implement
        try:
            required_status = PRDStatus(required_status_str)
        except ValueError:
            required_status = PRDStatus.APPROVED
        prd_failures = _check_prd_enforcement(
            run_path, config, required_status, "implement",
        )
        failures.extend(prd_failures)

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


# ---------------------------------------------------------------------------
# V2 Semantic Validation (PRD-CORE-008)
# ---------------------------------------------------------------------------

# Placeholder patterns for content density (common template defaults)
_PLACEHOLDER_RE = re.compile(
    r"^\s*<!--.*?-->\s*$"
    r"|^\s*\{[^}]+\}\s*$"
    r"|^\s*\[.*TODO.*\]\s*$",
    re.IGNORECASE,
)

# Section heading pattern (## N. Title)
_SECTION_SPLIT_RE = re.compile(r"^##\s+\d+\.\s+", re.MULTILINE)

# Section headings expected in an AARE-F compliant PRD
_EXPECTED_SECTION_NAMES: list[str] = [
    "Problem Statement",
    "Goals & Non-Goals",
    "User Stories",
    "Functional Requirements",
    "Non-Functional Requirements",
    "Technical Approach",
    "Test Strategy",
    "Rollout Plan",
    "Success Metrics",
    "Dependencies & Risks",
    "Open Questions",
    "Traceability Matrix",
]

# Sections with higher weight in density scoring
_HIGH_WEIGHT_SECTIONS: dict[str, float] = {
    "Problem Statement": 2.0,
    "Functional Requirements": 2.0,
    "Traceability Matrix": 1.5,
}


def _parse_section_content(content: str) -> list[tuple[str, str]]:
    """Split PRD content into (section_name, section_body) pairs.

    Args:
        content: Full PRD markdown content.

    Returns:
        List of (section_name, section_body) tuples.
    """
    # Strip frontmatter
    from trw_mcp.state.prd_utils import _FRONTMATTER_RE

    fm_match = _FRONTMATTER_RE.match(content)
    body = content[fm_match.end():] if fm_match else content

    sections: list[tuple[str, str]] = []
    heading_re = re.compile(r"^##\s+\d+\.\s+(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(body))

    for i, m in enumerate(matches):
        name = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections.append((name, body[start:end]))

    return sections


def _is_substantive_line(line: str) -> bool:
    """Check if a line is substantive (not blank, comment, heading, or placeholder).

    Args:
        line: Single line of text.

    Returns:
        True if the line contains substantive content.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return False
    if _PLACEHOLDER_RE.match(line):
        return False
    # Single-line HTML comment
    if stripped.startswith("<!--") and stripped.endswith("-->"):
        return False
    # Table separator rows (|---|---|)
    if re.match(r"^\s*\|[\s\-:|]+\|\s*$", line):
        return False
    # Horizontal rules
    if re.match(r"^\s*---\s*$", line):
        return False
    return True


def score_section_density(
    section_name: str,
    section_body: str,
) -> SectionScore:
    """Score the content density of a single PRD section.

    Args:
        section_name: Name of the section.
        section_body: Raw markdown body of the section.

    Returns:
        SectionScore with density ratio and line counts.
    """
    lines = section_body.split("\n")
    total = len(lines)
    if total == 0:
        return SectionScore(section_name=section_name)

    substantive = 0
    placeholder = 0
    for line in lines:
        if _is_substantive_line(line):
            substantive += 1
        elif _PLACEHOLDER_RE.match(line) or (
            line.strip().startswith("<!--") and line.strip().endswith("-->")
        ):
            placeholder += 1

    density = substantive / max(total, 1)
    return SectionScore(
        section_name=section_name,
        density=density,
        substantive_lines=substantive,
        total_lines=total,
        placeholder_lines=placeholder,
    )


def score_content_density(
    content: str,
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Content Density dimension (25 points max).

    Computes per-section density and aggregates via weighted average.
    Problem Statement and Functional Requirements get 2x weight;
    Traceability Matrix gets 1.5x weight.

    Args:
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        DimensionScore for content density.
    """
    _config = config or TRWConfig()
    max_score = _config.validation_density_weight

    sections = _parse_section_content(content)
    if not sections:
        return DimensionScore(
            name="content_density",
            score=0.0,
            max_score=max_score,
            details={"section_count": 0},
        )

    section_scores: list[SectionScore] = []
    weighted_sum = 0.0
    weight_total = 0.0

    for name, body in sections:
        ss = score_section_density(name, body)
        section_scores.append(ss)
        weight = _HIGH_WEIGHT_SECTIONS.get(name, 1.0)
        weighted_sum += ss.density * weight
        weight_total += weight

    avg_density = weighted_sum / max(weight_total, 1.0)
    score = avg_density * max_score

    return DimensionScore(
        name="content_density",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "avg_density": round(avg_density, 4),
            "sections_scored": len(section_scores),
        },
    )


def score_structural_completeness(
    frontmatter: dict[str, object],
    sections: list[str],
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Structural Completeness dimension (15 points max).

    Checks: 12 sections present, required frontmatter fields,
    confidence scores present.

    Args:
        frontmatter: Parsed YAML frontmatter.
        sections: List of section heading names found.
        config: Optional config for weight override.

    Returns:
        DimensionScore for structural completeness.
    """
    _config = config or TRWConfig()
    max_score = _config.validation_structure_weight

    # Section coverage: how many of the 12 expected sections are present
    expected = 12
    found = min(len(sections), expected)
    section_ratio = found / expected

    # Frontmatter field coverage
    required_fm_fields = ["id", "title", "version", "status", "priority"]
    fm_present = sum(1 for f in required_fm_fields if f in frontmatter and frontmatter[f])
    fm_ratio = fm_present / len(required_fm_fields)

    # Confidence scores present
    confidence = frontmatter.get("confidence", {})
    confidence_fields = [
        "implementation_feasibility",
        "requirement_clarity",
        "estimate_confidence",
    ]
    conf_present = 0
    if isinstance(confidence, dict):
        conf_present = sum(1 for f in confidence_fields if f in confidence)
    conf_ratio = conf_present / len(confidence_fields)

    # Weighted: sections 50%, frontmatter 30%, confidence 20%
    composite = section_ratio * 0.5 + fm_ratio * 0.3 + conf_ratio * 0.2
    score = composite * max_score

    return DimensionScore(
        name="structural_completeness",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "sections_found": found,
            "sections_expected": expected,
            "frontmatter_fields": fm_present,
            "confidence_fields": conf_present,
        },
    )


def score_traceability_v2(
    frontmatter: dict[str, object],
    content: str,
    config: TRWConfig | None = None,
) -> DimensionScore:
    """Score the Traceability dimension (20 points max).

    Checks: traceability link population, traceability matrix row quality.

    Args:
        frontmatter: Parsed YAML frontmatter.
        content: Full PRD markdown content.
        config: Optional config for weight override.

    Returns:
        DimensionScore for traceability.
    """
    _config = config or TRWConfig()
    max_score = _config.validation_traceability_weight

    # Check traceability fields in frontmatter
    trace_data = frontmatter.get("traceability", {})
    trace_fields = ["implements", "depends_on", "enables"]
    populated_fields = 0
    if isinstance(trace_data, dict):
        for field in trace_fields:
            val = trace_data.get(field, [])
            if isinstance(val, list) and len(val) > 0:
                populated_fields += 1

    field_ratio = populated_fields / len(trace_fields)

    # Check traceability matrix content
    matrix_score = 0.0
    if "Traceability Matrix" in content:
        matrix_section = content.split("Traceability Matrix")[-1]
        # Count rows with implementation file references
        impl_refs = re.findall(r"`[\w/]+\.py[:\w]*`", matrix_section)
        test_refs = re.findall(r"`test_[\w]+\.py[:\w]*`", matrix_section)
        # Count FR references in matrix
        fr_refs = re.findall(r"FR\d+", matrix_section)

        ref_count = len(impl_refs) + len(test_refs)
        if fr_refs:
            matrix_score = min(ref_count / max(len(fr_refs), 1), 1.0)

    # Composite: field population 40%, matrix quality 60%
    composite = field_ratio * 0.4 + matrix_score * 0.6
    score = composite * max_score

    return DimensionScore(
        name="traceability",
        score=round(min(score, max_score), 2),
        max_score=max_score,
        details={
            "populated_fields": populated_fields,
            "field_ratio": round(field_ratio, 4),
            "matrix_score": round(matrix_score, 4),
        },
    )


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
    _config = config or TRWConfig()
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
    _grade_map: dict[QualityTier, str] = {
        QualityTier.APPROVED: "A",
        QualityTier.REVIEW: "B",
        QualityTier.DRAFT: "D",
        QualityTier.SKELETON: "F",
    }
    return _grade_map.get(tier, "F")


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
    _messages: dict[str, str] = {
        "content_density": "Add substantive content to sections — replace template placeholders with actual requirements and details.",
        "structural_completeness": "Complete missing sections and frontmatter fields — ensure all 12 AARE-F sections are present.",
        "traceability": "Add traceability links (implements, depends_on, enables) and populate the Traceability Matrix with implementation and test references.",
        "smell_score": "Fix requirement quality issues — remove vague terms, passive voice, and unbounded scope.",
        "readability": "Improve readability — aim for Flesch-Kincaid grade 8-12 for technical documentation.",
        "ears_coverage": "Classify functional requirements using EARS patterns — add trigger keywords (When/While/If/Where) to FR sections.",
    }

    suggestions: list[ImprovementSuggestion] = []
    for dim in dimensions:
        ratio = dim.score / dim.max_score if dim.max_score > 0 else 1.0
        if ratio < 0.7:
            potential_gain = dim.max_score - dim.score
            priority = "high" if ratio < 0.3 else "medium"
            suggestions.append(
                ImprovementSuggestion(
                    dimension=dim.name,
                    priority=priority,
                    message=_messages.get(dim.name, f"Improve {dim.name} score."),
                    current_score=round(dim.score, 2),
                    potential_gain=round(potential_gain, 2),
                )
            )

    suggestions.sort(key=lambda s: s.potential_gain, reverse=True)
    return suggestions[:max_suggestions]


def validate_prd_quality_v2(
    content: str,
    config: TRWConfig | None = None,
) -> ValidationResultV2:
    """Validate a PRD with full 6-dimension semantic scoring.

    Orchestrates all dimension scorers, computes total score,
    classifies quality tier, and generates improvement suggestions.
    Also populates V1-compatible fields for backward compatibility.

    Args:
        content: Full PRD markdown content.
        config: Optional TRWConfig for threshold/weight overrides.

    Returns:
        ValidationResultV2 with all dimension scores and metadata.
    """
    _config = config or TRWConfig()

    # Parse frontmatter and sections using shared utils
    from trw_mcp.state.prd_utils import parse_frontmatter, extract_sections

    frontmatter = parse_frontmatter(content)
    sections = extract_sections(content)

    # Score 3 active dimensions (Phase 2a)
    dimensions: list[DimensionScore] = []
    failures: list[ValidationFailure] = []

    # 1. Content Density (25 pts)
    try:
        density_dim = score_content_density(content, _config)
    except Exception:
        density_dim = DimensionScore(
            name="content_density", score=0.0, max_score=_config.validation_density_weight
        )
    dimensions.append(density_dim)

    # 2. Structural Completeness (15 pts)
    try:
        structure_dim = score_structural_completeness(frontmatter, sections, _config)
    except Exception:
        structure_dim = DimensionScore(
            name="structural_completeness", score=0.0, max_score=_config.validation_structure_weight
        )
    dimensions.append(structure_dim)

    # 3. Traceability (20 pts)
    try:
        trace_dim = score_traceability_v2(frontmatter, content, _config)
    except Exception:
        trace_dim = DimensionScore(
            name="traceability", score=0.0, max_score=_config.validation_traceability_weight
        )
    dimensions.append(trace_dim)

    # 4. Smell Score (15 pts) — Phase 2b
    from trw_mcp.state.smell_detection import score_smells

    try:
        smell_dim, smell_findings = score_smells(content, _config)
    except Exception:
        smell_dim = DimensionScore(
            name="smell_score", score=0.0, max_score=_config.validation_smell_weight
        )
        smell_findings = []
    dimensions.append(smell_dim)

    # 5. Readability (10 pts) — Phase 2b
    from trw_mcp.state.readability import score_readability

    try:
        readability_dim, readability_metrics = score_readability(content, _config)
    except Exception:
        readability_dim = DimensionScore(
            name="readability",
            score=_config.validation_readability_weight * 0.5,
            max_score=_config.validation_readability_weight,
        )
        readability_metrics = {}
    dimensions.append(readability_dim)

    # 6. EARS Coverage (15 pts) — Phase 2c
    from trw_mcp.state.ears_classifier import score_ears_coverage

    try:
        ears_dim, ears_classifications = score_ears_coverage(content, _config)
    except Exception:
        ears_dim = DimensionScore(
            name="ears_coverage", score=0.0, max_score=_config.validation_ears_weight
        )
        ears_classifications = []
    dimensions.append(ears_dim)

    # Compute total score
    total_score = sum(d.score for d in dimensions)
    total_score = round(min(total_score, 100.0), 2)

    # Classify tier and grade
    tier = classify_quality_tier(total_score, _config)
    grade = map_grade(tier)

    # Section scores
    section_scores = [
        score_section_density(name, body)
        for name, body in _parse_section_content(content)
    ]

    # Generate improvement suggestions
    suggestions = generate_improvement_suggestions(dimensions)

    # V1-compatible fields (inline, avoids redundant validate_prd_quality call — PRD-FIX-011)
    required_fields = ["id", "title", "version", "status", "priority"]
    v1_failures: list[ValidationFailure] = []
    for field in required_fields:
        if field not in frontmatter or not frontmatter[field]:
            v1_failures.append(ValidationFailure(
                field=f"frontmatter:{field}", rule="required_field",
                message=f"Required frontmatter field missing: {field}", severity="error",
            ))
    expected_section_count = 12
    if len(sections) < expected_section_count:
        v1_failures.append(ValidationFailure(
            field="sections", rule="section_count",
            message=f"PRD has {len(sections)} sections, expected {expected_section_count}",
            severity="error",
        ))
    confidence = frontmatter.get("confidence", {})
    if isinstance(confidence, dict):
        for cf in ("implementation_feasibility", "requirement_clarity", "estimate_confidence"):
            if cf not in confidence:
                v1_failures.append(ValidationFailure(
                    field=f"confidence:{cf}", rule="confidence_present",
                    message=f"Missing confidence score: {cf}", severity="warning",
                ))
    traceability = frontmatter.get("traceability", {})
    has_traces = False
    if isinstance(traceability, dict):
        for key in ("implements", "depends_on", "enables"):
            val = traceability.get(key, [])
            if isinstance(val, list) and len(val) > 0:
                has_traces = True
                break
    if not has_traces:
        v1_failures.append(ValidationFailure(
            field="traceability", rule="has_traces",
            message="PRD has no traceability links", severity="warning",
        ))
    v1_total_checks = len(required_fields) + 3
    v1_error_count = sum(1 for f in v1_failures if f.severity == "error")
    v1_completeness = 1.0 - (v1_error_count / max(v1_total_checks, 1))
    v1_trace_coverage = 1.0 if has_traces else 0.0
    is_valid = v1_completeness >= 0.85 and v1_trace_coverage >= 0.9 and v1_error_count == 0

    result = ValidationResultV2(
        # V1 fields (computed inline)
        valid=is_valid,
        failures=v1_failures,
        ambiguity_rate=0.0,
        completeness_score=v1_completeness,
        traceability_coverage=v1_trace_coverage,
        consistency_score=0.0,
        # V2 fields
        total_score=total_score,
        quality_tier=tier,
        grade=grade,
        dimensions=dimensions,
        section_scores=section_scores,
        smell_findings=smell_findings,
        ears_classifications=ears_classifications,
        readability=readability_metrics,
        improvement_suggestions=suggestions,
    )

    logger.info(
        "prd_validated_v2",
        total_score=total_score,
        quality_tier=tier.value,
        grade=grade,
        dimensions_scored=len(dimensions),
    )
    return result
