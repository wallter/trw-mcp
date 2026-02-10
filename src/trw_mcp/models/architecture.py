"""Architecture encoding models — style, dependency rules, conventions, fitness results."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ArchitectureStyle(str, Enum):
    """Recognized architecture styles for fitness function selection."""

    HEXAGONAL = "hexagonal"
    VERTICAL_SLICES = "vertical_slices"
    DDD = "ddd"
    HYBRID = "hybrid"
    CUSTOM = "custom"


class ConventionSeverity(str, Enum):
    """Severity level for convention violations."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class DependencyRule(BaseModel):
    """Import direction rule for a dependency layer."""

    model_config = ConfigDict(use_enum_values=True)

    layer: str
    may_import: list[str] = Field(default_factory=list)
    may_not_import: list[str] = Field(default_factory=list)


class Convention(BaseModel):
    """Architectural convention checked at a specific phase gate."""

    model_config = ConfigDict(use_enum_values=True)

    name: str
    gate: str = "implement"  # plan, implement, validate
    check_method: str = ""
    severity: ConventionSeverity = ConventionSeverity.WARNING


class BoundedContext(BaseModel):
    """Bounded context within the project architecture."""

    model_config = ConfigDict(use_enum_values=True)

    name: str
    path: str
    description: str = ""


class TestingLayerConfig(BaseModel):
    """Testing layer configuration for architecture fitness."""

    model_config = ConfigDict(use_enum_values=True)

    layer: str
    test_type: str = "unit"
    mocking_strategy: str = ""
    coverage_target: float = Field(ge=0.0, le=1.0, default=0.80)


class ImportViolation(BaseModel):
    """A detected import direction violation."""

    model_config = ConfigDict(use_enum_values=True)

    file: str
    line: int = 0
    importing_module: str = ""
    imported_module: str = ""
    from_layer: str = ""
    to_layer: str = ""


class ConventionViolation(BaseModel):
    """A detected convention violation."""

    model_config = ConfigDict(use_enum_values=True)

    file: str
    convention_name: str
    message: str = ""
    severity: ConventionSeverity = ConventionSeverity.WARNING


class ArchitectureFitnessResult(BaseModel):
    """Result of architecture fitness check for a phase."""

    model_config = ConfigDict(use_enum_values=True)

    phase: str
    checks_run: int = 0
    violations: list[ImportViolation | ConventionViolation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0, default=1.0)


class ArchitectureConfig(BaseModel):
    """Full architecture configuration loaded from .trw/config.yaml."""

    model_config = ConfigDict(use_enum_values=True)

    style: ArchitectureStyle = ArchitectureStyle.CUSTOM
    dependency_rules: list[DependencyRule] = Field(default_factory=list)
    bounded_contexts: list[BoundedContext] = Field(default_factory=list)
    conventions: list[Convention] = Field(default_factory=list)
    testing_layers: list[TestingLayerConfig] = Field(default_factory=list)


# Alias avoids pytest collection warnings from the "Test" prefix.
TestLayerConfig = TestingLayerConfig
