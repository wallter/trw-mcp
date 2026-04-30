"""Build verification model — PRD-CORE-023.

Structured report for build verification gate results.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class BuildStatus(BaseModel):
    """Build verification result cached to .trw/context/build-status.yaml.

    Captures project-native validation results for phase gate consumption.
    Phase gates read this from disk — they never run subprocesses directly.

    ``mypy_clean`` remains as a legacy compatibility field for older clients.
    New integrations should prefer ``static_checks_clean`` for language-
    appropriate static analysis, type checking, linting, schema checks, or
    equivalent configured quality gates.
    """

    model_config = ConfigDict(strict=True)

    tests_passed: bool = Field(
        default=False,
        description="True if the project-native test/check suite passed.",
    )
    static_checks_clean: bool | None = Field(
        default=None,
        description=(
            "True if configured project-native static/type/lint/schema checks passed. "
            "When omitted, legacy mypy_clean is used for backward compatibility."
        ),
    )
    mypy_clean: bool = Field(
        default=False,
        description="Legacy compatibility alias for Python mypy/static-check status; prefer static_checks_clean.",
    )
    timed_out: bool = Field(
        default=False,
        description="True if the build check timed out before completing.",
    )
    coverage_pct: float = Field(
        ge=0.0,
        le=100.0,
        default=0.0,
        description="Coverage percentage from the configured coverage reporter, when available.",
    )
    test_count: int = Field(
        ge=0,
        default=0,
        description="Total number of checks/tests collected or executed.",
    )
    failure_count: int = Field(
        ge=0,
        default=0,
        description="Number of failed tests.",
    )
    failures: list[str] = Field(
        default_factory=list,
        description="First 10 failure descriptions for diagnostics.",
    )
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 timestamp of when the build check ran.",
    )
    scope: str = Field(
        default="full",
        description="Build check scope: 'full', 'quick', a tool name, or a project-native command label.",
    )
    duration_secs: float = Field(
        ge=0.0,
        default=0.0,
        description="Total wall-clock duration of the build check.",
    )
