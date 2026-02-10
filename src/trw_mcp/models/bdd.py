"""BDD (Behavior-Driven Development) models — Gherkin scenarios from PRD acceptance criteria.

PRD-CORE-005: BDD scenario generation pipeline models.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ConfidenceLevel(str, Enum):
    """Confidence level for generated BDD scenarios."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GherkinStep(BaseModel):
    """A single Gherkin step (Given/When/Then/And/But)."""

    model_config = ConfigDict(frozen=True)

    keyword: str
    text: str


class GherkinScenario(BaseModel):
    """A complete Gherkin scenario with steps and metadata."""

    model_config = ConfigDict(use_enum_values=True)

    name: str
    steps: list[GherkinStep] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    fr_id: str = ""
    ears_pattern: str = ""
    confidence: float = 1.0
    confidence_level: ConfidenceLevel = ConfidenceLevel.HIGH
    source: str = "structured"


class GherkinFeature(BaseModel):
    """A Gherkin feature file with scenarios and optional background."""

    model_config = ConfigDict(use_enum_values=True)

    name: str
    prd_id: str = ""
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    background: list[GherkinStep] = Field(default_factory=list)
    scenarios: list[GherkinScenario] = Field(default_factory=list)


class ExtractedFR(BaseModel):
    """A functional requirement extracted from a PRD."""

    id: str
    title: str = ""
    text: str = ""
    ears_pattern: str = ""
    ears_confidence: float = 0.0


class ExtractedAC(BaseModel):
    """An acceptance criterion extracted from a PRD."""

    fr_id: str = ""
    text: str = ""
    given: str = ""
    when: str = ""
    then: str = ""
    confidence: float = 1.0
    is_structured: bool = False


class BDDGenerationResult(BaseModel):
    """Result of BDD generation pipeline."""

    prd_id: str = ""
    prd_title: str = ""
    feature_file: str = ""
    scenarios_generated: int = 0
    frs_extracted: int = 0
    acs_extracted: int = 0
    structured_acs: int = 0
    unstructured_acs: int = 0
    validation_errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
