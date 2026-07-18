"""TaskProfile resolution for task/profile/complexity-aware TRW behavior."""

from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, ConfigDict

from trw_mcp.models.config import CapabilityTier, ClientProfile, ModelTier, normalize_capability_tier
from trw_mcp.models.run import ComplexityClass, ComplexitySignals
from trw_mcp.models.task_profile_types import (
    _TASK_TYPE_NUDGE_DEFAULTS,
    CeremonyDepth,
    EffortAdapterStatus,
    EffortSource,
    ExecutionEffort,
    NudgePolicy,
    RecallPolicy,
    TaskArchetype,
    TaskProfile,
    TaskProfileOverrides,
    TaskType,
    ToolExposurePreset,
    TraceDepth,
    task_type_recall_policy,
)
from trw_mcp.scoring import classify_complexity, get_ceremony_depth_contract


class _TaskProfileFingerprint(BaseModel):
    """Hash material for stable TaskProfile identity."""

    model_config = ConfigDict(frozen=True)

    profile_id: str
    capability_tier: CapabilityTier
    recommended_effort: ExecutionEffort
    effort_source: EffortSource
    effort_adapter_status: EffortAdapterStatus
    complexity_class: str
    task_archetype: str
    task_type: str
    recall_policy: str
    nudge_pool_weights: tuple[int, int, int, int]
    ceremony_depth: str
    mandatory_phases: tuple[str, ...]
    exposed_tool_preset: str
    nudge_policy: str
    trace_depth: str
    instruction_budget_lines: int
    context_window_tokens: int
    rationale: tuple[str, ...]


def _profile_hash(payload: _TaskProfileFingerprint) -> str:
    encoded = json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _resolve_complexity(
    complexity_class: ComplexityClass | str | None,
    complexity_signals: ComplexitySignals | None,
) -> tuple[ComplexityClass, tuple[str, ...]]:
    if complexity_class is not None:
        tier = (
            complexity_class
            if isinstance(complexity_class, ComplexityClass)
            else ComplexityClass(str(complexity_class).upper())
        )
        return tier, (f"complexity supplied as {tier.value}",)
    if complexity_signals is not None:
        tier, raw_score, override = classify_complexity(complexity_signals)
        rationale = [f"complexity classified as {tier.value} from raw score {raw_score}"]
        if override is not None:
            rationale.append(override.reason)
        return tier, tuple(rationale)
    return ComplexityClass.STANDARD, ("complexity defaulted to STANDARD because no signals were supplied",)


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


def _coerce_trace_depth(trace_depth: str) -> TraceDepth:
    if trace_depth == "minimal":
        return "minimal"
    if trace_depth == "causal":
        return "causal"
    return "standard"


def _recommended_effort_for_complexity(tier: ComplexityClass) -> ExecutionEffort:
    """Resolve a portable effort label without assuming a provider/model ID.

    Routine bounded work starts low, ordinary engineering work starts medium,
    and high-blast-radius work starts high. ``xhigh`` and ``max`` are never
    selected automatically; adapters or explicit evaluated overrides may use
    them when the active harness supports them.
    """
    if tier == ComplexityClass.MINIMAL:
        return "low"
    if tier == ComplexityClass.COMPREHENSIVE:
        return "high"
    return "medium"


def _extend_rationale(
    profile: ClientProfile, mandatory_phases: tuple[str, ...], base: tuple[str, ...]
) -> tuple[str, ...]:
    extra: list[str] = []
    if profile.ceremony_mode == "light" and "VALIDATE" in mandatory_phases:
        extra.append("light ceremony preserves VALIDATE as mandatory")
    if not profile.nudge_enabled:
        extra.append("profile disables nudges")
    return (*base, *extra)


def _apply_overrides(
    *,
    fingerprint: _TaskProfileFingerprint,
    overrides: TaskProfileOverrides | None,
) -> _TaskProfileFingerprint:
    if overrides is None:
        return fingerprint
    return fingerprint.model_copy(
        update={
            key: value
            for key, value in {
                "ceremony_depth": overrides.ceremony_depth,
                "mandatory_phases": overrides.mandatory_phases,
                "exposed_tool_preset": overrides.exposed_tool_preset,
                "nudge_policy": overrides.nudge_policy,
                "trace_depth": overrides.trace_depth,
                "instruction_budget_lines": overrides.instruction_budget_lines,
                "context_window_tokens": overrides.context_window_tokens,
                "recommended_effort": overrides.recommended_effort,
            }.items()
            if value is not None
        }
    )


def resolve_task_profile(
    *,
    client_profile: ClientProfile,
    model_tier: ModelTier | None = None,
    complexity_class: ComplexityClass | str | None = None,
    complexity_signals: ComplexitySignals | None = None,
    task_archetype: TaskArchetype = "unknown",
    task_type: TaskType = "unknown",
    tool_resolution_mode: ToolExposurePreset = "standard",
    config_overrides: TaskProfileOverrides | None = None,
) -> TaskProfile:
    """Resolve client profile + task complexity into a first-class TaskProfile.

    PRD-CORE-184: ``task_type`` (the runtime behavioral regime) drives the
    per-task-type nudge pool weights and the recall-policy hint. ``unknown``
    keeps the historical default weights — zero behavior change.
    """
    tier, complexity_rationale = _resolve_complexity(complexity_class, complexity_signals)
    contract = get_ceremony_depth_contract(tier)
    mandatory_phases = tuple(contract.mandatory_phases)
    rationale = _extend_rationale(client_profile, mandatory_phases, complexity_rationale)
    recommended_effort = _recommended_effort_for_complexity(tier)
    effort_source: EffortSource = "task_complexity"
    rationale = (*rationale, f"execution effort recommended as {recommended_effort} from task complexity")
    nudge_pool_weights = _TASK_TYPE_NUDGE_DEFAULTS.get(task_type, (40, 30, 20, 10))
    recall_policy: RecallPolicy = task_type_recall_policy(task_type)
    fingerprint = _TaskProfileFingerprint(
        profile_id=client_profile.client_id,
        capability_tier=normalize_capability_tier(model_tier or client_profile.default_model_tier),
        recommended_effort=recommended_effort,
        effort_source=effort_source,
        effort_adapter_status="advisory",
        complexity_class=tier.value,
        task_archetype=task_archetype,
        task_type=task_type,
        recall_policy=recall_policy,
        nudge_pool_weights=nudge_pool_weights,
        ceremony_depth=_resolve_ceremony_depth(client_profile, tier, contract.ceremony_depth),
        mandatory_phases=mandatory_phases,
        # PRD-CORE-218 FR04: exposure is the global tool_resolution_mode (the
        # kernel/pack authority), no longer a per-client-profile preset.
        exposed_tool_preset=tool_resolution_mode,
        nudge_policy=_resolve_nudge_policy(client_profile, tier, contract.nudge_policy),
        trace_depth=_coerce_trace_depth(contract.trace_depth),
        instruction_budget_lines=client_profile.instruction_max_lines,
        context_window_tokens=client_profile.context_window_tokens,
        rationale=rationale,
    )
    resolved = _apply_overrides(fingerprint=fingerprint, overrides=config_overrides)
    if config_overrides is not None and config_overrides.recommended_effort is not None:
        resolved = resolved.model_copy(
            update={
                "effort_source": "explicit_override",
                "rationale": (*resolved.rationale, "execution effort supplied by explicit override"),
            }
        )
    return TaskProfile(**resolved.model_dump(mode="python"), profile_hash=_profile_hash(resolved))
