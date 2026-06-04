"""Typed TaskProfile models shared by run metadata and resolver code."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskArchetype = Literal["bugfix", "feature", "docs", "refactor", "audit", "research", "unknown"]
ComplexityClassName = Literal["MINIMAL", "STANDARD", "COMPREHENSIVE"]
NudgePolicy = Literal["off", "sparse", "standard", "dense"]
TraceDepth = Literal["minimal", "standard", "causal"]
CeremonyDepth = Literal["light", "standard", "comprehensive"]
ToolExposurePreset = Literal["all", "core", "minimal", "standard", "custom"]

# PRD-CORE-184-FR01: canonical task-type taxonomy.
# Distinct from ``TaskArchetype`` (which names the git-commit intent) and
# orthogonal to ``ComplexityClassName`` (which gates ceremony depth).
# ``TaskType`` describes the runtime *behavioral regime* — which deliver-gate
# mode, nudge weighting, and recall policy apply. The detection function
# (``tools/_task_type_detection.detect_task_type``) is heuristic-only; an LLM
# classification step would re-introduce the iter-6 classification-as-priming
# harm (-24/-26pp on coding tasks). See PRD-CORE-184.
TaskType = Literal[
    "coding",
    "research",
    "docs",
    "eval",
    "rca",
    "planning",
    "unknown",
]

# PRD-CORE-184-FR06: per-task-type recall/retrieval policy hint. This is a
# specification surface — the retrieval implementation lives in trw-memory and
# may ignore unknown policies gracefully (fail-open).
RecallPolicy = Literal[
    "similarity",
    "failure_pattern",
    "breadth_first",
    "provenance",
    "structural",
]


# PRD-CORE-184-FR04: per-task-type nudge pool weights (workflow, learnings,
# ceremony, context — each tuple sums to 100). Applied before client-profile
# overrides. These are tunable defaults, NOT hard-coded truths — meta-tune
# campaigns recalibrate them once eval results are stratified by task type.
# Rationale: coding tasks need ceremony (build-gate/review); RCA needs
# learnings (pattern recall); research/docs suppress ceremony pressure
# (iter-7.1 mandate-language harm).
_TASK_TYPE_NUDGE_DEFAULTS: dict[TaskType, tuple[int, int, int, int]] = {
    # (workflow, learnings, ceremony, context)
    "coding": (35, 25, 30, 10),
    "rca": (20, 40, 20, 20),
    "research": (30, 45, 10, 15),
    "docs": (30, 40, 10, 20),
    "eval": (30, 35, 20, 15),
    "planning": (35, 30, 20, 15),
    "unknown": (40, 30, 20, 10),
}

# PRD-CORE-184-FR06: per-task-type recall policy hint table.
_TASK_TYPE_RECALL_POLICY: dict[TaskType, RecallPolicy] = {
    "coding": "similarity",
    "rca": "failure_pattern",
    "research": "breadth_first",
    "docs": "similarity",
    "eval": "provenance",
    "planning": "structural",
    "unknown": "similarity",
}


def task_type_recall_policy(task_type: TaskType) -> RecallPolicy:
    """Return the recall-policy hint for a task type (fail-open to similarity)."""
    return _TASK_TYPE_RECALL_POLICY.get(task_type, "similarity")


class TaskProfileOverrides(BaseModel):
    """Optional explicit overrides for task-profile resolution."""

    model_config = ConfigDict(frozen=True)

    ceremony_depth: CeremonyDepth | None = None
    mandatory_phases: tuple[str, ...] | None = None
    exposed_tool_preset: ToolExposurePreset | None = None
    nudge_policy: NudgePolicy | None = None
    trace_depth: TraceDepth | None = None
    instruction_budget_lines: int | None = Field(default=None, ge=1)
    context_window_tokens: int | None = Field(default=None, ge=1)


class TaskProfile(BaseModel):
    """Resolved operating profile for one concrete task/run."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    model_tier: str
    complexity_class: ComplexityClassName
    task_archetype: TaskArchetype = "unknown"
    # PRD-CORE-184: runtime behavioral regime + its derived policy surfaces.
    task_type: TaskType = "unknown"
    recall_policy: RecallPolicy = "similarity"
    # (workflow, learnings, ceremony, context) — task-type nudge weights.
    nudge_pool_weights: tuple[int, int, int, int] = (40, 30, 20, 10)
    ceremony_depth: CeremonyDepth
    mandatory_phases: tuple[str, ...] = Field(default_factory=tuple)
    exposed_tool_preset: ToolExposurePreset
    nudge_policy: NudgePolicy
    trace_depth: TraceDepth
    instruction_budget_lines: int
    context_window_tokens: int
    rationale: tuple[str, ...] = Field(default_factory=tuple)
    profile_hash: str

    @property
    def client_id(self) -> str:
        """Backward-compatible alias for the source client profile ID."""
        return self.profile_id
