"""Client profile models for per-client behavioral configuration.

CeremonyWeights, ScoringDimensionWeights, WriteTargets, and ClientProfile
provide a single dispatch point for all client-specific tuning — replacing
the multi-layer detect_ide / resolve_ide_targets / _determine_write_targets
taxonomy with frozen, validated Pydantic models.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trw_mcp.models.run import Phase

ModelTier = Literal["cloud-opus", "cloud-sonnet", "local-30b", "local-8b"]

_VALID_PHASES: frozenset[str] = frozenset(p.value for p in Phase)

__all__ = [
    "CeremonyWeights",
    "ClientProfile",
    "ModelTier",
    "ScoringDimensionWeights",
    "WriteTargets",
]


class CeremonyWeights(BaseModel):
    """Per-component weights for ceremony scoring (must sum to 100)."""

    model_config = ConfigDict(frozen=True)

    session_start: int = Field(default=25, ge=0)
    deliver: int = Field(default=25, ge=0)
    checkpoint: int = Field(default=20, ge=0)
    learn: int = Field(default=10, ge=0)
    build_check: int = Field(default=10, ge=0)
    review: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def _check_sum(self) -> CeremonyWeights:
        total = self.session_start + self.deliver + self.checkpoint + self.learn + self.build_check + self.review
        if total != 100:
            msg = f"CeremonyWeights must sum to 100, got {total}"
            raise ValueError(msg)
        return self

    def as_dict(self) -> dict[str, int]:
        """Dict form for backward compat with _CEREMONY_WEIGHTS consumers."""
        return self.model_dump()


class ScoringDimensionWeights(BaseModel):
    """Eval dimension weights (must sum to ~1.0)."""

    model_config = ConfigDict(frozen=True)

    outcome: float = Field(default=0.50, ge=0.0)
    plan_quality: float = Field(default=0.15, ge=0.0)
    implementation: float = Field(default=0.15, ge=0.0)
    ceremony: float = Field(default=0.10, ge=0.0)
    knowledge: float = Field(default=0.10, ge=0.0)

    @model_validator(mode="after")
    def _check_sum(self) -> ScoringDimensionWeights:
        total = self.outcome + self.plan_quality + self.implementation + self.ceremony + self.knowledge
        if abs(total - 1.0) > 0.01:
            msg = f"ScoringDimensionWeights must sum to ~1.0, got {total}"
            raise ValueError(msg)
        return self


class WriteTargets(BaseModel):
    """Instruction files this client writes during deliver/sync."""

    model_config = ConfigDict(frozen=True)

    claude_md: bool = False
    agents_md: bool = False
    cursor_rules: bool = False
    instruction_path: str = ""


class ClientProfile(BaseModel):
    """Per-client behavioral configuration -- single source of truth.

    Replaces the 4-layer taxonomy of detect_ide / resolve_ide_targets /
    _do_instruction_sync / _determine_write_targets with a single dispatch.
    """

    model_config = ConfigDict(frozen=True)

    # Identity
    client_id: str
    display_name: str

    # Instruction targets (F03 -- replaces bare tuple return)
    write_targets: WriteTargets = Field(default_factory=WriteTargets)
    instruction_max_lines: int = 500
    sub_instruction_max_lines: int = 50

    # Context budget
    context_window_tokens: int = 200_000

    # Ceremony tuning
    ceremony_mode: Literal["full", "light"] = "full"
    ceremony_weights: CeremonyWeights = Field(default_factory=CeremonyWeights)
    mandatory_phases: list[str] = Field(
        default_factory=lambda: [
            "RESEARCH",
            "PLAN",
            "IMPLEMENT",
            "VALIDATE",
            "REVIEW",
            "DELIVER",
        ]
    )

    @model_validator(mode="after")
    def _validate_phases(self) -> ClientProfile:
        normalized = [p.lower() for p in self.mandatory_phases]
        invalid = [p for p in normalized if p not in _VALID_PHASES]
        if invalid:
            msg = f"Invalid phase(s): {invalid}. Valid: {sorted(_VALID_PHASES)}"
            raise ValueError(msg)
        # Store normalized lowercase to match Phase enum values
        object.__setattr__(self, "mandatory_phases", normalized)
        return self

    # Scoring calibration (eval dimensions -- NOT PRD validation dimensions, F13)
    scoring_weights: ScoringDimensionWeights = Field(default_factory=ScoringDimensionWeights)

    # Model tier
    default_model_tier: ModelTier = "cloud-sonnet"

    # Response format (PRD-CORE-096)
    response_format: Literal["yaml", "json"] = "yaml"

    # Feature flags
    hooks_enabled: bool = True
    agents_md_enabled: bool = False
    review_md_enabled: bool = True
    include_framework_ref: bool = True
    include_agent_teams: bool = True
    include_delegation: bool = True
