"""Targeted testing models — test dependency map, resolution, classification.

PRD-QUAL-006: Pydantic v2 models for test dependency mapping, targeted
test resolution, and phase-appropriate test strategy selection.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TestType(str, Enum):
    """Test classification types."""

    __test__ = False  # Prevent pytest collection

    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"


class TestMapping(BaseModel):
    """Single source file to test file mapping."""

    __test__ = False  # Prevent pytest collection
    model_config = ConfigDict(use_enum_values=True)

    tests: list[str] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)


class TestDependencyMap(BaseModel):
    """Full test dependency map stored at .trw/test-map.yaml."""

    __test__ = False  # Prevent pytest collection
    model_config = ConfigDict(use_enum_values=True)

    version: int = 1
    generated_at: str = ""
    mappings: dict[str, TestMapping] = Field(default_factory=dict)


class TestResolution(BaseModel):
    """Result of resolving changed files to test targets."""

    __test__ = False  # Prevent pytest collection
    model_config = ConfigDict(use_enum_values=True)

    changed_files: list[str] = Field(default_factory=list)
    targeted_tests: list[str] = Field(default_factory=list)
    untested_files: list[str] = Field(default_factory=list)
    stale_entries: list[str] = Field(default_factory=list)
    fallback_used: bool = False
    warnings: list[str] = Field(default_factory=list)


class TestStrategy(BaseModel):
    """Phase-appropriate test strategy recommendation."""

    __test__ = False  # Prevent pytest collection
    model_config = ConfigDict(use_enum_values=True)

    phase: str = ""
    recommended_markers: list[str] = Field(default_factory=list)
    run_full_suite: bool = False
    run_coverage: bool = False
    run_mypy: bool = False
    description: str = ""


# Phase-to-strategy mapping (FRAMEWORK.md Testing Strategy)
PHASE_TEST_STRATEGIES: dict[str, TestStrategy] = {
    "research": TestStrategy(
        phase="research",
        description="No tests required during research phase.",
    ),
    "plan": TestStrategy(
        phase="plan",
        description="No tests required during planning phase.",
    ),
    "implement": TestStrategy(
        phase="implement",
        recommended_markers=["unit"],
        description="Run targeted unit tests on changed files after each change.",
    ),
    "validate": TestStrategy(
        phase="validate",
        recommended_markers=["unit", "integration"],
        run_coverage=True,
        run_mypy=True,
        description="Run unit + integration tests with coverage. Full mypy check.",
    ),
    "review": TestStrategy(
        phase="review",
        recommended_markers=["unit", "integration"],
        description="Tests should already pass from validate phase.",
    ),
    "deliver": TestStrategy(
        phase="deliver",
        recommended_markers=["unit", "integration", "e2e"],
        run_full_suite=True,
        run_coverage=True,
        run_mypy=True,
        description="Full test suite with coverage gates. Full mypy --strict.",
    ),
}
