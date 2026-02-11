"""Build verification model — PRD-CORE-023.

Structured report for build verification gate results.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class BuildStatus(BaseModel):
    """Build verification result cached to .trw/context/build-status.yaml.

    Captures pytest and mypy results for phase gate consumption.
    Phase gates read this from disk — they never run subprocesses directly.
    """

    model_config = ConfigDict(strict=True)

    tests_passed: bool = Field(
        default=False,
        description="True if all pytest tests passed.",
    )
    mypy_clean: bool = Field(
        default=False,
        description="True if mypy exited with code 0 (no errors).",
    )
    coverage_pct: float = Field(
        ge=0.0, le=100.0, default=0.0,
        description="Coverage percentage from pytest --cov output.",
    )
    test_count: int = Field(
        ge=0, default=0,
        description="Total number of tests collected by pytest.",
    )
    failure_count: int = Field(
        ge=0, default=0,
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
        description="Build check scope: 'full', 'quick', 'pytest', 'mypy'.",
    )
    duration_secs: float = Field(
        ge=0.0, default=0.0,
        description="Total wall-clock duration of the build check.",
    )
