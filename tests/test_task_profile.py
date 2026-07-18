"""Tests for PRD-CORE-152 task profile resolution."""

from __future__ import annotations

import pytest

from trw_mcp.models.config import resolve_client_profile, resolve_effort_adapter
from trw_mcp.models.run import ComplexityClass, ComplexitySignals
from trw_mcp.models.task_profile import resolve_task_profile
from trw_mcp.models.task_profile_types import (
    EffortAdapterStatus,
    ExecutionEffort,
    TaskProfile,
    TaskProfileOverrides,
)


def test_resolve_task_profile_defaults_to_standard() -> None:
    profile = resolve_task_profile(client_profile=resolve_client_profile("claude-code"))

    assert profile.client_id == "claude-code"
    assert profile.complexity_class == "STANDARD"
    assert profile.ceremony_depth == "standard"
    assert profile.trace_depth == "standard"
    # PRD-CORE-218 FR04: exposure is the global tool_resolution_mode (default
    # 'standard'), no longer the per-client-profile preset (was 'all').
    assert profile.exposed_tool_preset == "standard"
    assert "VALIDATE" in profile.mandatory_phases
    assert len(profile.profile_hash) == 16


def test_light_client_keeps_validate_mandatory() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("codex"),
        complexity_class=ComplexityClass.MINIMAL,
        task_archetype="bugfix",
    )

    assert profile.client_id == "codex"
    assert profile.model_tier == "balanced"
    assert profile.ceremony_depth == "light"
    # WHY: commit 28a67775a (feat(codex): enable standard TRW nudges by default,
    # PRD-CORE-125) intentionally set codex nudge_enabled=True. With nudges on,
    # a light-ceremony profile resolves to "sparse" (not "off"); "off" only
    # applies when nudge_enabled is False. Sibling tests were updated in that
    # commit but this one was missed — this is the correct current contract.
    assert profile.nudge_policy == "sparse"
    assert profile.trace_depth == "minimal"
    assert "VALIDATE" in profile.mandatory_phases
    assert "light ceremony preserves VALIDATE" in " ".join(profile.rationale)


def test_codex_explicit_model_tier_overrides_balanced_default() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("codex"),
        model_tier="local-small",
        complexity_class=ComplexityClass.MINIMAL,
    )

    assert profile.model_tier == "local-small"


def test_tier_aliases_normalize_before_hash() -> None:
    client = resolve_client_profile("claude-code")
    legacy = resolve_task_profile(client_profile=client, model_tier="cloud-opus")
    canonical = resolve_task_profile(client_profile=client, model_tier="frontier")

    assert legacy.capability_tier == "frontier"
    assert legacy.model_tier == "frontier"
    assert legacy.profile_hash == canonical.profile_hash
    assert "model_tier" not in legacy.model_dump()


def test_execution_effort_validation() -> None:
    valid_values: tuple[ExecutionEffort, ...] = (
        "inherit",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )

    for value in valid_values:
        assert TaskProfileOverrides(recommended_effort=value).recommended_effort == value

    with pytest.raises(ValueError, match="recommended_effort"):
        TaskProfileOverrides.model_validate({"recommended_effort": "ultra"})


@pytest.mark.parametrize(
    ("complexity", "expected"),
    [
        (ComplexityClass.MINIMAL, "low"),
        (ComplexityClass.STANDARD, "medium"),
        (ComplexityClass.COMPREHENSIVE, "high"),
    ],
)
def test_effort_recommendation_by_complexity(complexity: ComplexityClass, expected: str) -> None:
    profile = resolve_task_profile(client_profile=resolve_client_profile("claude-code"), complexity_class=complexity)

    assert profile.recommended_effort == expected
    assert profile.effort_source == "task_complexity"
    assert profile.effort_adapter_status == "advisory"
    assert "recommended" in " ".join(profile.rationale)


def test_explicit_effort_override_wins_and_records_source() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("claude-code"),
        complexity_class=ComplexityClass.MINIMAL,
        config_overrides=TaskProfileOverrides(recommended_effort="xhigh"),
    )

    assert profile.recommended_effort == "xhigh"
    assert profile.effort_source == "explicit_override"
    assert profile.effort_adapter_status == "advisory"


def test_effort_does_not_change_phases() -> None:
    client = resolve_client_profile("claude-code")
    low = resolve_task_profile(
        client_profile=client,
        complexity_class=ComplexityClass.STANDARD,
        config_overrides=TaskProfileOverrides(recommended_effort="low"),
    )
    high = resolve_task_profile(
        client_profile=client,
        complexity_class=ComplexityClass.STANDARD,
        config_overrides=TaskProfileOverrides(recommended_effort="high"),
    )

    assert low.ceremony_depth == high.ceremony_depth
    assert low.mandatory_phases == high.mandatory_phases
    assert low.exposed_tool_preset == high.exposed_tool_preset
    assert low.profile_hash != high.profile_hash


def test_legacy_task_profile_fields_load_with_safe_effort_defaults() -> None:
    current = resolve_task_profile(client_profile=resolve_client_profile("claude-code"))
    payload = current.model_dump()
    payload.pop("capability_tier")
    payload["model_tier"] = "cloud-opus"
    payload.pop("recommended_effort")
    payload.pop("effort_source")
    payload.pop("effort_adapter_status")

    loaded = TaskProfile.model_validate(payload)

    assert loaded.capability_tier == "frontier"
    assert loaded.recommended_effort == "inherit"
    assert loaded.effort_source == "harness_default"
    assert loaded.effort_adapter_status == "inherited"
    assert set(loaded.model_dump()).isdisjoint({"model_tier", "reasoning_effort"})


def test_provisional_reasoning_effort_alias_serializes_canonically() -> None:
    current = resolve_task_profile(client_profile=resolve_client_profile("claude-code"))
    payload = current.model_dump()
    payload["reasoning_effort"] = payload.pop("recommended_effort")

    loaded = TaskProfile.model_validate(payload)

    assert loaded.recommended_effort == "medium"
    assert "reasoning_effort" not in loaded.model_dump()


@pytest.mark.parametrize(
    ("client_id", "recommended", "supported", "expected_value", "expected_status"),
    [
        ("codex", "minimal", None, "minimal", "mapped"),
        ("codex", "max", None, "high", "clamped"),
        ("codex", "max", frozenset({"low", "medium", "high", "xhigh"}), "xhigh", "clamped"),
        ("claude-code", "minimal", None, "low", "clamped"),
        ("claude-code", "high", None, "high", "mapped"),
        ("claude-code", "max", frozenset({"low", "medium", "high", "xhigh", "max"}), "max", "mapped"),
        ("opencode", "high", None, None, "unsupported"),
        ("codex", "high", frozenset(), None, "unsupported"),
        ("codex", "high", frozenset({"inherit"}), None, "unsupported"),
        ("opencode", "inherit", None, None, "inherited"),
    ],
)
def test_full_client_effort_matrix(
    client_id: str,
    recommended: ExecutionEffort,
    supported: frozenset[ExecutionEffort] | None,
    expected_value: str | None,
    expected_status: EffortAdapterStatus,
) -> None:
    decision = resolve_effort_adapter(
        client_id=client_id,
        recommended_effort=recommended,
        supported_efforts=supported,
    )

    assert decision.harness_value == expected_value
    assert decision.status == expected_status
    assert decision.recommended_effort == recommended
    assert decision.adapter_id.startswith(client_id)


def test_comprehensive_task_overrides_light_ceremony_depth() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("cursor-cli"),
        complexity_class=ComplexityClass.COMPREHENSIVE,
        task_archetype="feature",
    )

    assert profile.ceremony_depth == "comprehensive"
    assert profile.trace_depth == "causal"
    assert profile.nudge_policy == "dense"
    assert profile.exposed_tool_preset == "standard"


def test_task_profile_hash_changes_with_complexity() -> None:
    client = resolve_client_profile("claude-code")
    minimal = resolve_task_profile(client_profile=client, complexity_class=ComplexityClass.MINIMAL)
    standard = resolve_task_profile(client_profile=client, complexity_class=ComplexityClass.STANDARD)

    assert minimal.profile_hash != standard.profile_hash


def test_complexity_signals_are_classified() -> None:
    profile = resolve_task_profile(
        client_profile=resolve_client_profile("claude-code"),
        complexity_signals=ComplexitySignals(files_affected=8, architecture_change=True),
    )

    assert profile.complexity_class == "COMPREHENSIVE"
    assert profile.trace_depth == "causal"


def test_task_profile_exposes_profile_id_and_client_alias() -> None:
    profile = resolve_task_profile(client_profile=resolve_client_profile("claude-code"))

    assert profile.profile_id == "claude-code"
    assert profile.client_id == profile.profile_id


def test_task_profile_accepts_typed_config_overrides() -> None:
    from trw_mcp.models.task_profile_types import TaskProfileOverrides

    profile = resolve_task_profile(
        client_profile=resolve_client_profile("claude-code"),
        complexity_class=ComplexityClass.STANDARD,
        config_overrides=TaskProfileOverrides(
            ceremony_depth="light",
            mandatory_phases=("IMPLEMENT", "VALIDATE", "DELIVER"),
            exposed_tool_preset="all",
            nudge_policy="sparse",
            trace_depth="minimal",
            instruction_budget_lines=120,
        ),
    )

    assert profile.ceremony_depth == "light"
    assert profile.mandatory_phases == ("IMPLEMENT", "VALIDATE", "DELIVER")
    assert profile.exposed_tool_preset == "all"
    assert profile.nudge_policy == "sparse"
    assert profile.trace_depth == "minimal"
    assert profile.instruction_budget_lines == 120


def test_run_state_accepts_missing_task_profile() -> None:
    from trw_mcp.models.run import RunState

    state = RunState(run_id="r1", task="legacy")
    assert state.task_profile is None


def test_prd_core_218_fr03() -> None:
    """FR03: standard task resolution yields exact packs/counts + explanations,
    no unlisted pack, and provider-identity/vague-keyword can never grant a
    high-risk pack."""
    from trw_mcp.models.config._defaults import CAPABILITY_PACKS, HIGH_RISK_PACKS, KERNEL_TOOLS
    from trw_mcp.models.config._profiles import CapabilityResolution, resolve_capability_packs

    # Exact standard task->pack fixture over the REAL TaskType vocabulary (F2:
    # coding/research/docs/eval/rca/planning/unknown). Kernel is 9 tools. The
    # former 'audit' key was a tombstone (not a TaskType) — audit work classifies
    # as coding/rca and gets the same navigation+verification surface.
    expected: dict[str, tuple[tuple[str, ...], int]] = {
        "coding": (("kernel", "verification", "code_navigation"), 15),
        "research": (("kernel", "code_navigation", "memory_management"), 15),
        "docs": (("kernel", "requirements", "verification"), 14),
        "eval": (("kernel", "verification"), 11),
        "rca": (("kernel", "code_navigation", "verification"), 15),
        "planning": (("kernel", "requirements"), 12),
        "unknown": (("kernel",), 9),
    }
    known_packs = {"kernel", *CAPABILITY_PACKS}
    assert len(KERNEL_TOOLS) == 9

    for task, (packs, count) in expected.items():
        resolution = resolve_capability_packs(task)
        assert isinstance(resolution, CapabilityResolution)
        assert resolution.packs == packs, (task, resolution.packs)
        assert resolution.tool_count == count == len(resolution.tools), task

        # tools = kernel tools then each selected pack's tools, deduplicated.
        exp_tools = list(KERNEL_TOOLS)
        for pack in packs[1:]:
            exp_tools += [t for t in CAPABILITY_PACKS[pack] if t not in exp_tools]
        assert resolution.tools == tuple(exp_tools), task
        assert set(KERNEL_TOOLS) <= set(resolution.tools)

        # Every capability carries a non-empty explanation; keys == tools.
        assert set(resolution.explanations) == set(resolution.tools), task
        assert all(resolution.explanations[t] for t in resolution.tools), task

        # No unlisted pack ever appears.
        assert set(resolution.packs) <= known_packs, task

    # kernel-only resolution is exactly the nine-tool kernel.
    assert resolve_capability_packs("unknown").tools == KERNEL_TOOLS

    # Negative: provider identity + vague HIGH-RISK keywords cannot widen the
    # surface. coding stays at its standard 15-tool set, no high-risk pack.
    guarded = resolve_capability_packs(
        "coding",
        keyword_hints=("dispatch", "experiment", "maintain"),
        provider_identity="claude-code",
    )
    assert set(guarded.packs).isdisjoint(HIGH_RISK_PACKS)
    assert guarded.packs == ("kernel", "verification", "code_navigation")
    assert guarded.tool_count == 15
    denied = [g for g in guarded.grants if g.source == "denied"]
    denied_packs = {g.pack for g in denied}
    assert {"dispatch", "experimentation", "run_maintenance"} <= denied_packs
    assert any("provider identity" in g.reason for g in denied)

    # Positive control: an explicit phase rule / operator grant MAY grant a
    # high-risk pack (guard is specific to provider/keyword, not blanket).
    phase = resolve_capability_packs("coding", phase_pack_grants=("dispatch",))
    assert "dispatch" in phase.packs
    assert all(phase.explanations[t] for t in CAPABILITY_PACKS["dispatch"])
    operator = resolve_capability_packs("unknown", operator_pack_grants=("experimentation",))
    assert "experimentation" in operator.packs
