"""BDD scenario generation pipeline — 6-stage PRD to Gherkin converter.

PRD-CORE-005: Parses PRDs, extracts FRs/ACs, classifies EARS patterns,
generates Gherkin scenarios, validates structure, and renders .feature files.

All functions are pure or file-scoped — no MCP tool registration side effects.
"""

from __future__ import annotations

import re
from pathlib import Path

from trw_mcp.models.bdd import (
    BDDGenerationResult,
    ConfidenceLevel,
    ExtractedAC,
    ExtractedFR,
    GherkinFeature,
    GherkinScenario,
    GherkinStep,
)
from trw_mcp.state.ears_classifier import classify_requirement
from trw_mcp.state.prd_utils import parse_frontmatter

# Compiled regex patterns
_FR_HEADING_RE = re.compile(
    r"^###\s+(PRD-[A-Z]+-\d{3}-FR\d+):\s*(.+)$",
    re.MULTILINE,
)
_AC_GWT_RE = re.compile(
    r"[-*]\s*\*\*Given\*\*\s+(.+?)\n\s*(?:[-*]\s*)?\*\*When\*\*\s+(.+?)\n\s*(?:[-*]\s*)?\*\*Then\*\*\s+(.+?)(?=\n\s*[-*]\s*\*\*Given\*\*|\n\s*###|\n\s*##|\n\s*$|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_AC_PLAIN_GWT_RE = re.compile(
    r"[-*]\s*Given\s+(.+?)\n\s*(?:[-*]\s*)?When\s+(.+?)\n\s*(?:[-*]\s*)?Then\s+(.+?)(?=\n\s*[-*]\s*Given|\n\s*###|\n\s*##|\n\s*$|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_CONFIDENCE_TAG_RE = re.compile(r"\[confidence:\s*([\d.]+)\]", re.IGNORECASE)
_FR_SECTION_RE = re.compile(
    r"##\s+\d+\.\s+Functional Requirements\s*\n(.*?)(?=\n##\s+\d+\.|\Z)",
    re.DOTALL,
)
_AC_SECTION_RE = re.compile(
    r"##\s+\d+\.\s+Acceptance Criteria\s*\n(.*?)(?=\n##\s+\d+\.|\Z)",
    re.DOTALL,
)
_AC_BULLET_RE = re.compile(
    r"^\s*[-*]\s+(.+)$",
    re.MULTILINE,
)

# --- Named constants (formerly magic numbers) ---
MIN_BULLET_LENGTH = 10
"""Minimum character length for an unstructured AC bullet to be extracted."""

UNSTRUCTURED_CONFIDENCE_CAP = 0.6
"""Maximum confidence score assigned to scenarios generated from unstructured ACs."""

MEDIUM_CONFIDENCE_THRESHOLD = 0.6
"""Score at or above which confidence is classified as MEDIUM."""

HIGH_CONFIDENCE_THRESHOLD = 0.85
"""Score at or above which confidence is classified as HIGH."""

UNSTRUCTURED_SCENARIO_NAME_MAX_LEN = 60
"""Maximum character length for scenario names derived from unstructured ACs."""

STRUCTURED_SCENARIO_NAME_MAX_LEN = 80
"""Maximum character length for scenario names derived from structured ACs."""

STEP_TEXT_MAX_LEN = 120
"""Maximum character length for individual Gherkin step text."""

_GWT_BULLET_PREFIXES = ("**given**", "**when**", "**then**", "given ", "when ", "then ")
"""Lowercase prefixes that identify a bullet as a Given/When/Then line (skip for unstructured extraction)."""


# Stage 1: Parse PRD
def parse_prd_for_bdd(content: str) -> dict[str, str]:
    """Extract prd_id and title from PRD frontmatter.

    Args:
        content: Full PRD markdown content.

    Returns:
        Dict with 'prd_id' and 'title' keys.
    """
    fm = parse_frontmatter(content)
    prd_id = str(fm.get("id", ""))
    title = str(fm.get("title", ""))
    return {"prd_id": prd_id, "title": title}


# Stage 2: Extract FRs and ACs
def extract_frs_and_acs(
    content: str, prd_id: str = "",
) -> tuple[list[ExtractedFR], list[ExtractedAC]]:
    """Extract functional requirements and acceptance criteria from PRD.

    Args:
        content: Full PRD markdown content.
        prd_id: PRD identifier for FR ID matching.

    Returns:
        Tuple of (extracted_frs, extracted_acs).
    """
    frs: list[ExtractedFR] = []
    acs: list[ExtractedAC] = []

    # Extract FRs from Section 4 (Functional Requirements)
    fr_section_match = _FR_SECTION_RE.search(content)
    if fr_section_match:
        fr_body = fr_section_match.group(1)

        # Find FR headings: ### PRD-XXX-NNN-FRN: Title
        for fr_match in _FR_HEADING_RE.finditer(fr_body):
            fr_id = fr_match.group(1)
            fr_title = fr_match.group(2).strip()

            # Get the text block after this heading until next heading
            start = fr_match.end()
            next_heading = _FR_HEADING_RE.search(fr_body, start)
            end = next_heading.start() if next_heading else len(fr_body)
            fr_text = fr_body[start:end].strip()

            frs.append(ExtractedFR(
                id=fr_id,
                title=fr_title,
                text=fr_text,
            ))

    # Extract ACs from Section 3 (Acceptance Criteria) or inline with FRs
    ac_section_match = _AC_SECTION_RE.search(content)
    ac_body = ac_section_match.group(1) if ac_section_match else ""

    # Also check for ACs in FR section body
    fr_body_for_acs = fr_section_match.group(1) if fr_section_match else ""
    combined_ac_body = ac_body + "\n" + fr_body_for_acs

    # Try structured Given/When/Then (bold markdown style)
    for gwt_match in _AC_GWT_RE.finditer(combined_ac_body):
        given = _clean_step(gwt_match.group(1))
        when = _clean_step(gwt_match.group(2) or "")
        then = _clean_step(gwt_match.group(3) or "")

        if given and when and then:
            confidence = _extract_confidence(gwt_match.group(0))
            fr_id = _match_fr_for_ac(gwt_match.start(), combined_ac_body, frs)
            acs.append(ExtractedAC(
                fr_id=fr_id,
                text=f"Given {given} When {when} Then {then}",
                given=given,
                when=when,
                then=then,
                confidence=confidence,
                is_structured=True,
            ))

    # Try plain-text Given/When/Then
    for gwt_match in _AC_PLAIN_GWT_RE.finditer(combined_ac_body):
        given = _clean_step(gwt_match.group(1))
        when = _clean_step(gwt_match.group(2) or "")
        then = _clean_step(gwt_match.group(3) or "")

        if given and when and then:
            # Check not already captured by bold pattern
            text = f"Given {given} When {when} Then {then}"
            if not any(ac.text == text for ac in acs):
                confidence = _extract_confidence(gwt_match.group(0))
                fr_id = _match_fr_for_ac(gwt_match.start(), combined_ac_body, frs)
                acs.append(ExtractedAC(
                    fr_id=fr_id,
                    text=text,
                    given=given,
                    when=when,
                    then=then,
                    confidence=confidence,
                    is_structured=True,
                ))

    # Extract unstructured ACs (bullet points without Given/When/Then)
    if ac_body:
        for bullet_match in _AC_BULLET_RE.finditer(ac_body):
            bullet_text = bullet_match.group(1).strip()
            # Skip if it's a GWT line
            lower = bullet_text.lower()
            if lower.startswith(_GWT_BULLET_PREFIXES):
                continue
            if len(bullet_text) > MIN_BULLET_LENGTH and not any(ac.text == bullet_text for ac in acs):
                confidence = _extract_confidence(bullet_text)
                acs.append(ExtractedAC(
                    fr_id="",
                    text=bullet_text,
                    confidence=confidence,
                    is_structured=False,
                ))

    return frs, acs


# Stage 3: Classify ACs with EARS patterns
def classify_acs(
    frs: list[ExtractedFR],
    acs: list[ExtractedAC],
) -> tuple[list[ExtractedFR], list[ExtractedAC]]:
    """Classify FRs and propagate EARS patterns to ACs.

    Args:
        frs: Extracted functional requirements.
        acs: Extracted acceptance criteria.

    Returns:
        Updated (frs, acs) with EARS classification.
    """
    # Classify each FR
    for fr in frs:
        if fr.text:
            result = classify_requirement(fr.text)
            fr.ears_pattern = str(result.get("pattern", ""))
            fr.ears_confidence = float(str(result.get("confidence", 0.0)))

    # Propagate EARS patterns to ACs (via parent FR).
    # Note: patterns are not stored on ACs directly -- they are applied
    # at scenario generation time via fr_map lookup in generate_scenarios().
    fr_map = {fr.id: fr for fr in frs}
    for ac in acs:
        if ac.fr_id and ac.fr_id in fr_map and ac.text:
            ac_result = classify_requirement(ac.text)
            ac_pattern = str(ac_result.get("pattern", ""))
            if ac_pattern != "unclassified":
                continue  # AC has its own classification; no FR inheritance needed

    return frs, acs


# Stage 4: Generate scenarios
def generate_scenarios(
    frs: list[ExtractedFR],
    acs: list[ExtractedAC],
    prd_id: str = "",
    include_background: bool = False,
) -> GherkinFeature:
    """Generate Gherkin scenarios from extracted FRs and ACs.

    Structured ACs map directly to scenarios.
    Unstructured ACs use EARS-template-based generation with confidence downgrade.

    Args:
        frs: Extracted FRs with EARS classification.
        acs: Extracted ACs.
        prd_id: PRD identifier for feature tags.
        include_background: Extract shared Given steps to Background section.

    Returns:
        GherkinFeature with generated scenarios.
    """
    fr_map = {fr.id: fr for fr in frs}
    scenarios: list[GherkinScenario] = []

    for ac in acs:
        if ac.is_structured:
            # Direct mapping from structured AC
            steps: list[GherkinStep] = []
            if ac.given:
                steps.append(GherkinStep(keyword="Given", text=ac.given))
            if ac.when:
                steps.append(GherkinStep(keyword="When", text=ac.when))
            if ac.then:
                steps.append(GherkinStep(keyword="Then", text=ac.then))

            tags = []
            if prd_id:
                tags.append(f"@{prd_id}")
            confidence_level = _score_to_level(ac.confidence)
            tags.append(f"@confidence:{confidence_level.value}")

            # Add EARS tag from parent FR
            ears_pattern = ""
            if ac.fr_id and ac.fr_id in fr_map:
                ears_pattern = fr_map[ac.fr_id].ears_pattern
                if ears_pattern and ears_pattern != "unclassified":
                    tags.append(f"@ears:{ears_pattern}")

            scenario_name = _derive_scenario_name(ac)
            scenarios.append(GherkinScenario(
                name=scenario_name,
                steps=steps,
                tags=tags,
                fr_id=ac.fr_id,
                ears_pattern=ears_pattern,
                confidence=ac.confidence,
                confidence_level=confidence_level,
                source="structured",
            ))
        else:
            # EARS-template-based generation with confidence downgrade
            steps = _generate_from_unstructured(ac.text)
            downgraded_confidence = min(ac.confidence, UNSTRUCTURED_CONFIDENCE_CAP)

            tags = []
            if prd_id:
                tags.append(f"@{prd_id}")
            confidence_level = _score_to_level(downgraded_confidence)
            tags.append(f"@confidence:{confidence_level.value}")

            scenarios.append(GherkinScenario(
                name=f"Verify: {_truncate(ac.text, UNSTRUCTURED_SCENARIO_NAME_MAX_LEN)}",
                steps=steps,
                tags=tags,
                fr_id=ac.fr_id,
                confidence=downgraded_confidence,
                confidence_level=confidence_level,
                source="unstructured",
            ))

    feature = GherkinFeature(
        name=prd_id or "Unknown",
        prd_id=prd_id,
        scenarios=scenarios,
    )

    # Extract background if requested
    if include_background and len(scenarios) > 1:
        feature = _extract_background(feature)

    return feature


# Stage 5: Validate feature
def validate_feature(feature: GherkinFeature) -> list[str]:
    """Validate Gherkin feature structure.

    Checks:
    - Every scenario has at least Given + When + Then
    - No duplicate scenario names
    - Non-empty step text

    Args:
        feature: GherkinFeature to validate.

    Returns:
        List of validation error messages (empty if valid).
    """
    errors: list[str] = []

    if not feature.scenarios:
        return errors  # Empty feature is valid but will produce warning

    seen_names: set[str] = set()
    for scenario in feature.scenarios:
        # Check for duplicates
        if scenario.name in seen_names:
            errors.append(f"Duplicate scenario name: '{scenario.name}'")
        seen_names.add(scenario.name)

        # Check step keywords
        keywords = [s.keyword for s in scenario.steps]
        if "Given" not in keywords:
            errors.append(f"Scenario '{scenario.name}' missing Given step")
        if "When" not in keywords:
            errors.append(f"Scenario '{scenario.name}' missing When step")
        if "Then" not in keywords:
            errors.append(f"Scenario '{scenario.name}' missing Then step")

        # Check empty step text
        for step in scenario.steps:
            if not step.text.strip():
                errors.append(
                    f"Scenario '{scenario.name}' has empty {step.keyword} step"
                )

    return errors


# Stage 6: Render feature text
def render_feature_text(feature: GherkinFeature) -> str:
    """Render GherkinFeature to standard .feature file format.

    Args:
        feature: GherkinFeature to render.

    Returns:
        Gherkin feature file content as string.
    """
    lines: list[str] = []

    # Feature-level tags
    if feature.tags:
        lines.append(" ".join(feature.tags))
    if feature.prd_id:
        prd_tag = f"@{feature.prd_id}"
        if not feature.tags or prd_tag not in feature.tags:
            if feature.tags:
                lines[-1] += f" {prd_tag}"
            else:
                lines.append(prd_tag)

    lines.append(f"Feature: {feature.name}")
    if feature.description:
        for desc_line in feature.description.split("\n"):
            lines.append(f"  {desc_line}")
    lines.append("")

    # Background section
    if feature.background:
        lines.append("  Background:")
        for step in feature.background:
            lines.append(f"    {step.keyword} {step.text}")
        lines.append("")

    # Scenarios
    for scenario in feature.scenarios:
        if scenario.tags:
            lines.append(f"  {' '.join(scenario.tags)}")
        lines.append(f"  Scenario: {scenario.name}")
        for step in scenario.steps:
            lines.append(f"    {step.keyword} {step.text}")
        lines.append("")

    return "\n".join(lines)


# Full pipeline
def run_bdd_pipeline(
    prd_path: str,
    output_dir: str | None = None,
    include_background: bool = False,
    confidence_threshold: float = 0.0,
) -> BDDGenerationResult:
    """Run the complete 6-stage BDD generation pipeline.

    Args:
        prd_path: Path to PRD markdown file.
        output_dir: Directory for .feature output (default: same as PRD).
        include_background: Extract shared Given steps to Background.
        confidence_threshold: Minimum confidence to include scenario.

    Returns:
        BDDGenerationResult with pipeline output.
    """
    path = Path(prd_path).resolve()
    if not path.exists():
        return BDDGenerationResult(
            warnings=[f"PRD file not found: {prd_path}"],
        )

    content = path.read_text(encoding="utf-8")

    # Stage 1: Parse
    prd_info = parse_prd_for_bdd(content)
    prd_id = prd_info["prd_id"]
    prd_title = prd_info["title"]

    # Stage 2: Extract
    frs, acs = extract_frs_and_acs(content, prd_id)

    # Stage 3: Classify
    frs, acs = classify_acs(frs, acs)

    # Stage 4: Generate
    feature = generate_scenarios(frs, acs, prd_id, include_background)

    # Filter by confidence threshold
    if confidence_threshold > 0.0:
        feature.scenarios = [
            s for s in feature.scenarios
            if s.confidence >= confidence_threshold
        ]

    # Stage 5: Validate
    validation_errors = validate_feature(feature)

    # Stage 6: Render
    feature_text = render_feature_text(feature)

    # Determine output path
    out_dir = Path(output_dir) if output_dir else path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    feature_filename = f"{prd_id or path.stem}.feature"
    feature_path = out_dir / feature_filename
    feature_path.write_text(feature_text, encoding="utf-8")

    # Build result
    warnings: list[str] = []
    if not feature.scenarios:
        warnings.append("No scenarios generated — PRD may lack structured acceptance criteria")
    if not frs:
        warnings.append("No functional requirements found in PRD")

    structured_count = sum(1 for ac in acs if ac.is_structured)

    return BDDGenerationResult(
        prd_id=prd_id,
        prd_title=prd_title,
        feature_file=str(feature_path),
        scenarios_generated=len(feature.scenarios),
        frs_extracted=len(frs),
        acs_extracted=len(acs),
        structured_acs=structured_count,
        unstructured_acs=len(acs) - structured_count,
        validation_errors=validation_errors,
        warnings=warnings,
    )


# --- Private helpers ---

def _clean_step(text: str) -> str:
    """Clean a step text: strip whitespace and confidence tags."""
    return _CONFIDENCE_TAG_RE.sub("", text).strip()


def _extract_confidence(text: str) -> float:
    """Extract confidence score from text, defaulting to 1.0."""
    match = _CONFIDENCE_TAG_RE.search(text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 1.0


def _match_fr_for_ac(
    ac_pos: int,
    body: str,
    frs: list[ExtractedFR],
) -> str:
    """Find the nearest preceding FR heading for an AC position."""
    best_fr = ""
    best_pos = -1
    for fr in frs:
        fr_match = re.search(rf"###\s+{re.escape(fr.id)}", body)
        if fr_match and fr_match.start() < ac_pos and fr_match.start() > best_pos:
            best_pos = fr_match.start()
            best_fr = fr.id
    return best_fr


def _score_to_level(score: float) -> ConfidenceLevel:
    """Map numeric score to confidence level."""
    if score >= HIGH_CONFIDENCE_THRESHOLD:
        return ConfidenceLevel.HIGH
    if score >= MEDIUM_CONFIDENCE_THRESHOLD:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def _derive_scenario_name(ac: ExtractedAC) -> str:
    """Derive a scenario name from an AC."""
    if ac.then:
        return _truncate(ac.then, STRUCTURED_SCENARIO_NAME_MAX_LEN)
    if ac.when:
        return _truncate(f"When {ac.when}", STRUCTURED_SCENARIO_NAME_MAX_LEN)
    return _truncate(ac.text, STRUCTURED_SCENARIO_NAME_MAX_LEN)


def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# EARS pattern -> (given_template, when_template, then_template)
# The placeholder "{text}" is replaced with the truncated AC text.
_EARS_STEP_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "event_driven": (
        "the system is in a ready state",
        "{text}",
        "the expected behavior occurs",
    ),
    "state_driven": (
        "{text}",
        "the condition is active",
        "the system maintains the state behavior",
    ),
    "unwanted_behavior": (
        "an error condition exists",
        "{text}",
        "the system handles the error gracefully",
    ),
}

_EARS_DEFAULT_TEMPLATE: tuple[str, str, str] = (
    "the system is operational",
    "the user interacts with the feature",
    "{text}",
)


def _generate_from_unstructured(text: str) -> list[GherkinStep]:
    """Generate Gherkin steps from unstructured AC text using EARS patterns."""
    ears = classify_requirement(text)
    pattern = str(ears.get("pattern", ""))

    given_tmpl, when_tmpl, then_tmpl = _EARS_STEP_TEMPLATES.get(
        pattern, _EARS_DEFAULT_TEMPLATE,
    )
    truncated = _truncate(text, STEP_TEXT_MAX_LEN)

    return [
        GherkinStep(keyword="Given", text=given_tmpl.replace("{text}", truncated)),
        GherkinStep(keyword="When", text=when_tmpl.replace("{text}", truncated)),
        GherkinStep(keyword="Then", text=then_tmpl.replace("{text}", truncated)),
    ]


def _extract_background(feature: GherkinFeature) -> GherkinFeature:
    """Extract common Given steps into Background section.

    Finds Given steps shared across all scenarios and moves them to Background.
    """
    if len(feature.scenarios) < 2:
        return feature

    # Find Given steps common to all scenarios
    common_givens = {
        s.text for s in feature.scenarios[0].steps if s.keyword == "Given"
    }
    for scenario in feature.scenarios[1:]:
        scenario_givens = {
            s.text for s in scenario.steps if s.keyword == "Given"
        }
        common_givens &= scenario_givens

    if not common_givens:
        return feature

    # Move common Given steps to background
    background = [
        GherkinStep(keyword="Given", text=text)
        for text in sorted(common_givens)
    ]

    # Remove common Given steps from scenarios
    updated_scenarios: list[GherkinScenario] = []
    for scenario in feature.scenarios:
        remaining_steps = [
            s for s in scenario.steps
            if not (s.keyword == "Given" and s.text in common_givens)
        ]
        updated_scenarios.append(scenario.model_copy(update={"steps": remaining_steps}))

    return feature.model_copy(update={
        "background": background,
        "scenarios": updated_scenarios,
    })
