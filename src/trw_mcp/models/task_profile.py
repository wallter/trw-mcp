"""TaskProfile resolution for task/profile/complexity-aware TRW behavior."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.models.config import ClientProfile, ModelTier
from trw_mcp.models.run import ComplexityClass, ComplexitySignals
from trw_mcp.scoring import classify_complexity, get_ceremony_depth_contract

TaskArchetype = Literal["bugfix", "feature", "docs", "refactor", "audit", "research", "unknown"]
NudgePolicy = Literal["off", "sparse", "standard", "dense"]
TraceDepth = Literal["minimal", "standard", "causal"]
CeremonyDepth = Literal["light", "standard", "comprehensive"]


class TaskProfile(BaseModel):
    """Resolved operating profile for one concrete task/run."""

    model_config = ConfigDict(frozen=True, use_enum_values=True)

    client_id: str
    model_tier: ModelTier
    complexity_class: ComplexityClass
    task_archetype: TaskArchetype = "unknown"
    ceremony_depth: CeremonyDepth
    mandatory_phases: list[str] = Field(default_factory=list)
    exposed_tool_preset: str
    nudge_policy: NudgePolicy
    trace_depth: TraceDepth
    instruction_budget_lines: int
    context_window_tokens: int
    rationale: list[str] = Field(default_factory=list)
    profile_hash: str


def _profile_hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _resolve_complexity(
    complexity_class: ComplexityClass | str | None,
    complexity_signals: ComplexitySignals | None,
) -> tuple[ComplexityClass, list[str]]:
    rationale: list[str] = []
    if complexity_class is not None:
        tier = complexity_class if isinstance(complexity_class, ComplexityClass) else ComplexityClass(str(complexity_class).upper())
        rationale.append(f"complexity supplied as {tier.value}")
        return tier, rationale
    if complexity_signals is not None:
        tier, raw_score, override = classify_complexity(complexity_signals)
        rationale.append(f"complexity classified as {tier.value} from raw score {raw_score}")
        if override is not None:
            rationale.append(override.reason)
        return tier, rationale
    rationale.append("complexity defaulted to STANDARD because no signals were supplied")
    return ComplexityClass.STANDARD, rationale


def _resolve_ceremony_depth(profile: ClientProfile, tier: ComplexityClass, contract_depth: str) -> CeremonyDepth:
    if tier == ComplexityClass.COMPREHENSIVE:
        return "comprehensive"
    if profile.ceremony_mode == "light" or contract_depth == "light":
        return "light"
    return "standard"


def _resolve_nudge_policy(profile: ClientProfile, tier: ComplexityClass, contract_policy: str) -> NudgePolicy:
    if not profile.nudge_enabled:
        return "off"
    if profile.nudge_density == "low":
        return "sparse"
    if profile.nudge_density == "high" or tier == ComplexityClass.COMPREHENSIVE:
        return "dense"
    if profile.ceremony_mode == "light":
        return "sparse"
    if contract_policy == "sparse":
        return "sparse"
    if contract_policy == "dense":
        return "dense"
    return "standard"


def resolve_task_profile(
    *,
    client_profile: ClientProfile,
    model_tier: ModelTier | None = None,
    complexity_class: ComplexityClass | str | None = None,
    complexity_signals: ComplexitySignals | None = None,
    task_archetype: TaskArchetype = "unknown",
) -> TaskProfile:
    """Resolve client profile + task complexity into a first-class TaskProfile."""
    tier, rationale = _resolve_complexity(complexity_class, complexity_signals)
    contract = get_ceremony_depth_contract(tier)
    ceremony_depth = _resolve_ceremony_depth(client_profile, tier, contract.ceremony_depth)
    nudge_policy = _resolve_nudge_policy(client_profile, tier, contract.nudge_policy)
    trace_depth = cast("TraceDepth", contract.trace_depth)
    if client_profile.ceremony_mode == "light" and "VALIDATE" in contract.mandatory_phases:
        rationale.append("light ceremony preserves VALIDATE as mandatory")
    if not client_profile.nudge_enabled:
        rationale.append("profile disables nudges")

    resolved_model_tier = model_tier or client_profile.default_model_tier
    mandatory_phases = list(contract.mandatory_phases)
    hash_payload: dict[str, object] = {
        "client_id": client_profile.client_id,
        "model_tier": resolved_model_tier,
        "complexity_class": tier.value,
        "task_archetype": task_archetype,
        "ceremony_depth": ceremony_depth,
        "mandatory_phases": mandatory_phases,
        "exposed_tool_preset": client_profile.tool_exposure_mode,
        "nudge_policy": nudge_policy,
        "trace_depth": trace_depth,
        "instruction_budget_lines": client_profile.instruction_max_lines,
        "context_window_tokens": client_profile.context_window_tokens,
        "rationale": rationale,
    }
    return TaskProfile(
        client_id=client_profile.client_id,
        model_tier=resolved_model_tier,
        complexity_class=tier,
        task_archetype=task_archetype,
        ceremony_depth=ceremony_depth,
        mandatory_phases=mandatory_phases,
        exposed_tool_preset=client_profile.tool_exposure_mode,
        nudge_policy=nudge_policy,
        trace_depth=trace_depth,
        instruction_budget_lines=client_profile.instruction_max_lines,
        context_window_tokens=client_profile.context_window_tokens,
        rationale=rationale,
        profile_hash=_profile_hash(hash_payload),
    )
