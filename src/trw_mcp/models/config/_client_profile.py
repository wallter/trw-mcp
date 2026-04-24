"""Client profile models for per-client behavioral configuration.

CeremonyWeights, ScoringDimensionWeights, WriteTargets, and ClientProfile
provide a single dispatch point for all client-specific tuning — replacing
the multi-layer detect_ide / resolve_ide_targets / _determine_write_targets
taxonomy with frozen, validated Pydantic models.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

from trw_mcp.models.run import Phase

ModelTier = Literal["cloud-opus", "cloud-sonnet", "local-30b", "local-8b"]

_VALID_PHASES: frozenset[str] = frozenset(p.value for p in Phase)

__all__ = [
    "CeremonyWeights",
    "ClientProfile",
    "ModelTier",
    "NudgePoolWeights",
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


class NudgePoolWeights(BaseModel):
    """Per-pool weights for nudge selection (must sum to 100).

    PRD-CORE-129: Controls relative probability of each nudge pool
    being selected via weighted random. Pools with weight=0 are
    never selected (e.g., light profiles disable ceremony nudges).
    """

    model_config = ConfigDict(frozen=True)

    workflow: int = Field(default=40, ge=0)
    learnings: int = Field(default=30, ge=0)
    ceremony: int = Field(default=20, ge=0)
    context: int = Field(default=10, ge=0)

    @model_validator(mode="after")
    def _check_sum(self) -> NudgePoolWeights:
        total = self.workflow + self.learnings + self.ceremony + self.context
        if total != 100:
            msg = f"NudgePoolWeights must sum to 100, got {total}"
            raise ValueError(msg)
        return self


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
    agents_md_primary: bool = False  # primary write target (CLI profiles, e.g. cursor-cli)
    cli_config: bool = False  # .cursor/cli.json managed (cursor-cli only)
    cursor_rules: bool = False
    copilot_instructions: bool = False
    gemini_md: bool = False
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
    nudge_pool_weights: NudgePoolWeights = Field(default_factory=NudgePoolWeights)
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

    # -- Surface control flags (PRD-CORE-125) --
    nudge_enabled: bool = True
    # PRD-CORE-146 FR04: per-profile nudge density default. ``None`` leaves
    # density unset so TRWConfig.effective_nudge_density falls back through
    # to the module default. No profile opts in by default today.
    nudge_density: Literal["low", "medium", "high"] | None = None
    tool_exposure_mode: Literal["all", "core", "minimal", "standard", "custom"] = "all"
    learning_recall_enabled: bool = True
    mcp_instructions_enabled: bool = True
    skills_enabled: bool = True

    # -- Tool namespace rendering (PRD-FIX-078) --
    # Prepended to bare ``trw_*`` tool names in rendered instructional text.
    # claude-code exposes MCP tools under ``mcp__{server}__{tool}`` — set to
    # ``"mcp__trw__"`` for claude-code; bare names for all other clients.
    tool_namespace_prefix: str = ""

    @computed_field  # type: ignore[prop-decorator]  # Pydantic v2 supports @computed_field on @property; the decorator-order lint is a known Pyright limitation.
    @property
    def config_dir(self) -> str:
        """PRD-CORE-149 FR06: client config directory.

        Defaults to the parent directory of ``write_targets.instruction_path``
        (e.g., ``.claude`` for ``.claude/INSTRUCTIONS.md``). Returns ``.trw``
        when no instruction path is configured -- the TRW state directory is
        the universal fallback.

        Exposed as a Pydantic v2 ``@computed_field`` so it appears in
        ``model_dump()`` output and is recognized by static type checkers as
        an attribute of ``ClientProfile`` (resolves the Pyright false-positive
        on ``_nudge_messages.py`` accessing ``profile.config_dir``).
        """
        path = self.write_targets.instruction_path
        if not path:
            return ".trw"
        parent = PurePosixPath(path).parent
        # PurePosixPath("AGENTS.md").parent == PurePosixPath("."); map to .trw
        parent_str = str(parent)
        if parent_str in (".", ""):
            return ".trw"
        return parent_str

    @field_validator("tool_namespace_prefix")
    @classmethod
    def _validate_namespace_prefix(cls, v: str) -> str:
        """PRD-FIX-078 NFR03: reject whitespace / shell metacharacters."""
        if v == "":
            return v
        # Allow only alphanumerics + underscores (MCP namespace convention)
        if not all(c.isalnum() or c == "_" for c in v):
            msg = f"tool_namespace_prefix must contain only alphanumerics and underscores, got {v!r}"
            raise ValueError(msg)
        return v
