"""Tests for PRD-CORE-005: BDD scenario generation pipeline."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.models.bdd import (
    ExtractedAC,
    GherkinFeature,
    GherkinScenario,
    GherkinStep,
)
from trw_mcp.state.bdd import (
    UNSTRUCTURED_CONFIDENCE_CAP,
    classify_acs,
    extract_frs_and_acs,
    generate_scenarios,
    parse_prd_for_bdd,
    render_feature_text,
    run_bdd_pipeline,
    validate_feature,
)

SAMPLE_PRD = """\
---
prd:
  id: PRD-CORE-099
  title: Test Feature
  status: approved
---

## 1. Problem Statement

We need a test feature.

## 2. Solution Overview

Build it.

## 3. Acceptance Criteria

- **Given** the system is running
  **When** the user clicks submit
  **Then** the form is saved successfully

- **Given** the user is authenticated
  **When** they access the dashboard
  **Then** they see their profile data

- The system shall process requests within 200ms

## 4. Functional Requirements

### PRD-CORE-099-FR01: Form Submission

When the user submits a form, the system shall validate all fields
and persist the data to the database.

### PRD-CORE-099-FR02: Dashboard Access

While the user is authenticated, the system shall display
the personalized dashboard with real-time data.

## 5. Non-Functional Requirements

Performance targets apply.
"""

EMPTY_PRD = """\
---
prd:
  id: PRD-CORE-EMPTY
  title: Empty Feature
  status: draft
---

## 1. Problem Statement

Nothing here yet.

## 2. Solution Overview

TBD.
"""


class TestStage1ParsePrd:
    """Test Stage 1: Parse PRD frontmatter."""

    def test_extract_prd_id_and_title(self) -> None:
        result = parse_prd_for_bdd(SAMPLE_PRD)
        assert result["prd_id"] == "PRD-CORE-099"
        assert result["title"] == "Test Feature"

    def test_empty_frontmatter(self) -> None:
        result = parse_prd_for_bdd("No frontmatter here")
        assert result["prd_id"] == ""
        assert result["title"] == ""


class TestStage2ExtractFRsAndACs:
    """Test Stage 2: FR and AC extraction."""

    def test_fr_extraction(self) -> None:
        frs, _ = extract_frs_and_acs(SAMPLE_PRD, "PRD-CORE-099")
        assert len(frs) == 2
        assert frs[0].id == "PRD-CORE-099-FR01"
        assert frs[0].title == "Form Submission"
        assert frs[1].id == "PRD-CORE-099-FR02"

    def test_structured_ac_extraction(self) -> None:
        _, acs = extract_frs_and_acs(SAMPLE_PRD, "PRD-CORE-099")
        structured = [ac for ac in acs if ac.is_structured]
        assert len(structured) >= 2
        first = structured[0]
        assert first.given
        assert first.when
        assert first.then

    def test_unstructured_ac_extraction(self) -> None:
        _, acs = extract_frs_and_acs(SAMPLE_PRD, "PRD-CORE-099")
        unstructured = [ac for ac in acs if not ac.is_structured]
        assert len(unstructured) >= 1
        assert "200ms" in unstructured[0].text


class TestStage3Classification:
    """Test Stage 3: EARS classification propagation."""

    def test_ears_classification_on_frs(self) -> None:
        frs, acs = extract_frs_and_acs(SAMPLE_PRD, "PRD-CORE-099")
        frs, acs = classify_acs(frs, acs)

        # FR01 has "When ... shall" -> event_driven
        assert frs[0].ears_pattern == "event_driven"
        # FR02 has "While ... shall" -> state_driven
        assert frs[1].ears_pattern == "state_driven"


class TestStage4GenerateScenarios:
    """Test Stage 4: Gherkin scenario generation."""

    def test_structured_ac_to_scenario(self) -> None:
        ac = ExtractedAC(
            fr_id="FR01",
            text="Given x When y Then z",
            given="the user is logged in",
            when="they click save",
            then="the data is persisted",
            confidence=0.9,
            is_structured=True,
        )
        feature = generate_scenarios([], [ac], prd_id="PRD-TEST-001")
        assert len(feature.scenarios) == 1
        scenario = feature.scenarios[0]
        assert len(scenario.steps) == 3
        assert scenario.steps[0].keyword == "Given"
        assert scenario.steps[1].keyword == "When"
        assert scenario.steps[2].keyword == "Then"
        assert scenario.source == "structured"

    def test_unstructured_ac_to_scenario(self) -> None:
        ac = ExtractedAC(
            fr_id="",
            text="The system shall respond within 100ms",
            confidence=0.8,
            is_structured=False,
        )
        feature = generate_scenarios([], [ac], prd_id="PRD-TEST-001")
        assert len(feature.scenarios) == 1
        scenario = feature.scenarios[0]
        assert scenario.source == "unstructured"
        assert scenario.confidence <= UNSTRUCTURED_CONFIDENCE_CAP
        # Unstructured scenarios still produce Given/When/Then steps
        keywords = [s.keyword for s in scenario.steps]
        assert "Given" in keywords
        assert "When" in keywords
        assert "Then" in keywords

    def test_scenarios_have_varying_confidence(self) -> None:
        """Scenarios from mixed ACs produce varying confidence levels."""
        frs, acs = extract_frs_and_acs(SAMPLE_PRD, "PRD-CORE-099")
        frs, acs = classify_acs(frs, acs)
        feature = generate_scenarios(frs, acs, "PRD-CORE-099")

        assert len(feature.scenarios) > 0
        high_confidence = [s for s in feature.scenarios if s.confidence >= 0.9]
        assert len(high_confidence) < len(feature.scenarios)


def _make_steps(*keyword_text_pairs: tuple[str, str]) -> list[GherkinStep]:
    """Build a list of GherkinStep from (keyword, text) pairs."""
    return [GherkinStep(keyword=kw, text=txt) for kw, txt in keyword_text_pairs]


VALID_GWT_STEPS = _make_steps(
    ("Given", "a precondition"),
    ("When", "an action"),
    ("Then", "an outcome"),
)


class TestStage5Validation:
    """Test Stage 5: Feature validation."""

    def test_valid_feature_passes(self) -> None:
        feature = GherkinFeature(
            name="Test",
            scenarios=[GherkinScenario(name="Valid scenario", steps=VALID_GWT_STEPS)],
        )
        errors = validate_feature(feature)
        assert errors == []

    def test_missing_steps_fail_validation(self) -> None:
        feature = GherkinFeature(
            name="Test",
            scenarios=[
                GherkinScenario(
                    name="Incomplete scenario",
                    steps=_make_steps(("Given", "only a given")),
                ),
            ],
        )
        errors = validate_feature(feature)
        assert len(errors) >= 2  # Missing When and Then

    def test_duplicate_names_fail_validation(self) -> None:
        scenario = GherkinScenario(name="Same name", steps=VALID_GWT_STEPS)
        feature = GherkinFeature(
            name="Test",
            scenarios=[scenario, scenario.model_copy()],
        )
        errors = validate_feature(feature)
        assert any("Duplicate" in e for e in errors)


class TestStage6Rendering:
    """Test Stage 6: Feature text rendering."""

    def test_feature_text_format(self) -> None:
        feature = GherkinFeature(
            name="My Feature",
            prd_id="PRD-TEST-001",
            scenarios=[
                GherkinScenario(
                    name="Test scenario",
                    tags=["@PRD-TEST-001", "@confidence:high"],
                    steps=_make_steps(
                        ("Given", "a state"),
                        ("When", "an action"),
                        ("Then", "an outcome"),
                    ),
                ),
            ],
        )
        text = render_feature_text(feature)
        assert "Feature: My Feature" in text
        assert "Scenario: Test scenario" in text
        assert "Given a state" in text
        assert "When an action" in text
        assert "Then an outcome" in text
        assert "@PRD-TEST-001" in text

    def test_background_rendering(self) -> None:
        feature = GherkinFeature(
            name="With Background",
            background=_make_steps(("Given", "a shared precondition")),
            scenarios=[
                GherkinScenario(
                    name="Test",
                    steps=_make_steps(("When", "action"), ("Then", "result")),
                ),
            ],
        )
        text = render_feature_text(feature)
        assert "Background:" in text
        assert "Given a shared precondition" in text


class TestFullPipeline:
    """Test full BDD generation pipeline."""

    def test_full_pipeline_integration(self, tmp_path: Path) -> None:
        prd_path = tmp_path / "PRD-CORE-099.md"
        prd_path.write_text(SAMPLE_PRD, encoding="utf-8")

        result = run_bdd_pipeline(str(prd_path), str(tmp_path))

        assert result.prd_id == "PRD-CORE-099"
        assert result.scenarios_generated > 0
        assert result.frs_extracted == 2
        assert result.acs_extracted > 0

        # Verify .feature file exists
        feature_path = Path(result.feature_file)
        assert feature_path.exists()
        feature_content = feature_path.read_text(encoding="utf-8")
        assert "Feature:" in feature_content
        assert "Scenario:" in feature_content

    def test_empty_prd_produces_warnings(self, tmp_path: Path) -> None:
        prd_path = tmp_path / "PRD-CORE-EMPTY.md"
        prd_path.write_text(EMPTY_PRD, encoding="utf-8")

        result = run_bdd_pipeline(str(prd_path), str(tmp_path))

        assert result.scenarios_generated == 0
        assert result.frs_extracted == 0
        assert len(result.warnings) > 0

    def test_nonexistent_prd_returns_warning(self) -> None:
        result = run_bdd_pipeline("/nonexistent/path.md")
        assert len(result.warnings) > 0
        assert "not found" in result.warnings[0]

    def test_pipeline_with_background(self, tmp_path: Path) -> None:
        prd_path = tmp_path / "PRD-CORE-099.md"
        prd_path.write_text(SAMPLE_PRD, encoding="utf-8")

        result = run_bdd_pipeline(
            str(prd_path), str(tmp_path), include_background=True,
        )
        assert result.scenarios_generated > 0
