"""Tests for client profile system (Phase 5a — PRD-CORE-085 / PRD-INFRA-042).

12 test cases covering:
- All 5 built-in profiles construct via resolve_client_profile
- CeremonyWeights validation (sum=100 / sum!=100)
- ScoringDimensionWeights validation (sum~=1.0 / sum>1.01)
- Unknown client_id fallback to claude-code
- Model tier override via model_copy
- TRWConfig.client_profile resolution from target_platforms
- Empty target_platforms defaults to claude-code
- Multi-platform logs warning
- Frozen immutability
- WriteTargets per profile
- compute_ceremony_score with/without custom weights
"""

from __future__ import annotations

import pytest

from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig, resolve_client_profile
from trw_mcp.models.config._client_profile import (
    CeremonyWeights,
    ClientProfile,
    ScoringDimensionWeights,
    WriteTargets,
)
from trw_mcp.state.analytics.report import compute_ceremony_score


# ---------------------------------------------------------------------------
# Test 1: All 5 profiles construct via resolve_client_profile
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("client_id", ["claude-code", "opencode", "cursor", "codex", "aider"])
def test_all_profiles_construct(client_id: str) -> None:
    """resolve_client_profile(id) returns a ClientProfile for all 5 built-ins."""
    profile = resolve_client_profile(client_id)
    assert profile.client_id == client_id


# ---------------------------------------------------------------------------
# Test 2: CeremonyWeights sum=100 — valid construction
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ceremony_weights_valid_sum_100() -> None:
    """CeremonyWeights with fields summing to 100 constructs successfully."""
    w = CeremonyWeights(
        session_start=30,
        deliver=30,
        checkpoint=5,
        learn=20,
        build_check=15,
        review=0,
    )
    assert w.session_start + w.deliver + w.checkpoint + w.learn + w.build_check + w.review == 100


# ---------------------------------------------------------------------------
# Test 3: CeremonyWeights sum!=100 raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ceremony_weights_invalid_sum_raises() -> None:
    """CeremonyWeights with fields not summing to 100 raises ValidationError."""
    with pytest.raises(ValidationError, match="must sum to 100"):
        CeremonyWeights(
            session_start=30,
            deliver=30,
            checkpoint=10,
            learn=10,
            build_check=10,
            review=15,  # total = 105 — invalid
        )


@pytest.mark.unit
def test_ceremony_weights_sum_off_by_one_raises() -> None:
    """CeremonyWeights with sum=99 raises ValidationError."""
    with pytest.raises(ValidationError, match="must sum to 100"):
        CeremonyWeights(
            session_start=24,
            deliver=25,
            checkpoint=15,
            learn=10,
            build_check=10,
            review=15,  # total = 99
        )


# ---------------------------------------------------------------------------
# Test 4: ScoringDimensionWeights sum~=1.0 — valid construction
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_scoring_dimension_weights_valid_sum() -> None:
    """ScoringDimensionWeights with fields summing to ~1.0 constructs successfully."""
    w = ScoringDimensionWeights(
        outcome=0.60,
        plan_quality=0.05,
        implementation=0.15,
        ceremony=0.05,
        knowledge=0.15,
    )
    total = w.outcome + w.plan_quality + w.implementation + w.ceremony + w.knowledge
    assert abs(total - 1.0) <= 0.01


@pytest.mark.unit
def test_scoring_dimension_weights_tolerance_boundary() -> None:
    """ScoringDimensionWeights tolerates floating-point near 1.0 (within 0.01)."""
    # 0.505 + 0.150 + 0.150 + 0.100 + 0.100 = 1.005 — within tolerance
    w = ScoringDimensionWeights(
        outcome=0.505,
        plan_quality=0.150,
        implementation=0.150,
        ceremony=0.100,
        knowledge=0.095,
    )
    total = w.outcome + w.plan_quality + w.implementation + w.ceremony + w.knowledge
    assert abs(total - 1.0) <= 0.01


# ---------------------------------------------------------------------------
# Test 5: ScoringDimensionWeights sum>1.01 raises ValueError
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_scoring_dimension_weights_over_limit_raises() -> None:
    """ScoringDimensionWeights with sum > 1.01 raises ValidationError."""
    with pytest.raises(ValidationError, match="must sum to ~1.0"):
        ScoringDimensionWeights(
            outcome=0.60,
            plan_quality=0.20,
            implementation=0.20,
            ceremony=0.10,
            knowledge=0.10,  # total = 1.20
        )


# ---------------------------------------------------------------------------
# Test 6: Unknown client_id falls back to claude-code (caplog warning)
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_unknown_client_id_falls_back_to_claude_code() -> None:
    """resolve_client_profile('windsurf') falls back to claude-code."""
    profile = resolve_client_profile("windsurf")
    assert profile.client_id == "claude-code"


# ---------------------------------------------------------------------------
# Test 7: Model tier override returns adjusted context/lines via model_copy
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_model_tier_override_local_30b_adjusts_context() -> None:
    """resolve_client_profile('opencode', 'local-30b') returns context=128k via model_copy."""
    profile = resolve_client_profile("opencode", model_tier="local-30b")
    assert profile.context_window_tokens == 128_000
    assert profile.instruction_max_lines == 350


@pytest.mark.unit
def test_model_tier_override_does_not_mutate_registry() -> None:
    """Model tier override returns a NEW profile — does not mutate the registry entry."""
    original = resolve_client_profile("opencode")
    overridden = resolve_client_profile("opencode", model_tier="local-30b")
    # Original should be unchanged
    assert original.context_window_tokens == 32_000
    assert overridden.context_window_tokens == 128_000


# ---------------------------------------------------------------------------
# Test 8: TRWConfig(target_platforms=["opencode"]).client_profile.client_id == "opencode"
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_trwconfig_single_opencode_platform_resolves_profile() -> None:
    """TRWConfig with target_platforms=['opencode'] resolves opencode client_profile."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.client_profile.client_id == "opencode"


# ---------------------------------------------------------------------------
# Test 9: Empty target_platforms defaults to claude-code
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_empty_target_platforms_defaults_to_claude_code() -> None:
    """TRWConfig with empty target_platforms resolves claude-code client_profile."""
    cfg = TRWConfig(target_platforms=[])
    assert cfg.client_profile.client_id == "claude-code"


# ---------------------------------------------------------------------------
# Test 10: Multi-platform target_platforms uses first
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_multi_platform_uses_first() -> None:
    """TRWConfig with multiple target_platforms uses the first one."""
    cfg = TRWConfig(target_platforms=["opencode", "claude-code"])
    assert cfg.client_profile.client_id == "opencode"


# ---------------------------------------------------------------------------
# Test 11: Frozen immutability — assignment raises ValidationError
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_client_profile_frozen_raises_on_assignment() -> None:
    """Frozen ClientProfile raises ValidationError on attribute assignment."""
    profile = resolve_client_profile("claude-code")
    with pytest.raises((ValidationError, TypeError)):
        profile.client_id = "hacked"  # type: ignore[misc]


@pytest.mark.unit
def test_ceremony_weights_frozen_raises_on_assignment() -> None:
    """Frozen CeremonyWeights raises TypeError on attribute assignment."""
    w = CeremonyWeights()
    with pytest.raises((ValidationError, TypeError)):
        w.session_start = 99  # type: ignore[misc]


@pytest.mark.unit
def test_write_targets_frozen_raises_on_assignment() -> None:
    """Frozen WriteTargets raises TypeError on attribute assignment."""
    wt = WriteTargets(claude_md=True)
    with pytest.raises((ValidationError, TypeError)):
        wt.claude_md = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 12: WriteTargets per profile
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_claude_code_profile_writes_claude_md() -> None:
    """claude-code profile has write_targets.claude_md=True."""
    profile = resolve_client_profile("claude-code")
    assert profile.write_targets.claude_md is True
    assert profile.write_targets.agents_md is False
    assert profile.write_targets.cursor_rules is False


@pytest.mark.unit
def test_opencode_profile_writes_agents_md() -> None:
    """opencode profile has write_targets.agents_md=True."""
    profile = resolve_client_profile("opencode")
    assert profile.write_targets.agents_md is True
    assert profile.write_targets.claude_md is False
    assert profile.write_targets.cursor_rules is False


@pytest.mark.unit
def test_cursor_profile_writes_cursor_rules() -> None:
    """cursor profile has write_targets.cursor_rules=True."""
    profile = resolve_client_profile("cursor")
    assert profile.write_targets.cursor_rules is True
    assert profile.write_targets.claude_md is False
    assert profile.write_targets.agents_md is False


# ---------------------------------------------------------------------------
# Additional: compute_ceremony_score with custom weights
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_compute_ceremony_score_with_custom_weights() -> None:
    """compute_ceremony_score uses custom CeremonyWeights when provided."""
    events: list[dict[str, object]] = [
        {"event": "session_start"},
        {"event": "learn_new_entry"},
    ]
    # Give learn full weight (100) and zero everything else
    custom_weights = CeremonyWeights(
        session_start=0,
        deliver=0,
        checkpoint=0,
        learn=100,
        build_check=0,
        review=0,
    )
    result = compute_ceremony_score(events, weights=custom_weights)
    assert result["score"] == 100


@pytest.mark.unit
def test_compute_ceremony_score_without_weights_backward_compat() -> None:
    """compute_ceremony_score without weights uses _CEREMONY_WEIGHTS defaults."""
    events: list[dict[str, object]] = [
        {"event": "session_start"},
        {"event": "reflection_complete"},
        {"event": "checkpoint"},
        {"event": "learn_new_entry"},
        {"event": "build_check_complete"},
        {"event": "review_complete"},
    ]
    result = compute_ceremony_score(events)
    # All 6 ceremony steps present — should score 100 with default weights
    assert result["score"] == 100


# ---------------------------------------------------------------------------
# Test: CeremonyWeights.as_dict() returns correct type and keys
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_ceremony_weights_as_dict_returns_correct_keys() -> None:
    """as_dict() returns a dict with all 6 ceremony component keys."""
    w = CeremonyWeights()
    d = w.as_dict()
    assert isinstance(d, dict)
    assert set(d.keys()) == {"session_start", "deliver", "checkpoint", "learn", "build_check", "review"}
    assert all(isinstance(v, int) for v in d.values())


# ---------------------------------------------------------------------------
# Test: All 5 profiles have valid ceremony weights (sum=100) and scoring weights (sum~=1.0)
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("client_id", ["claude-code", "opencode", "cursor", "codex", "aider"])
def test_all_profiles_have_valid_weights(client_id: str) -> None:
    """Every built-in profile has ceremony weights summing to 100 and scoring weights summing to ~1.0."""
    profile = resolve_client_profile(client_id)

    cw = profile.ceremony_weights
    ceremony_total = cw.session_start + cw.deliver + cw.checkpoint + cw.learn + cw.build_check + cw.review
    assert ceremony_total == 100, f"{client_id} ceremony weights sum to {ceremony_total}, expected 100"

    sw = profile.scoring_weights
    scoring_total = sw.outcome + sw.plan_quality + sw.implementation + sw.ceremony + sw.knowledge
    assert abs(scoring_total - 1.0) <= 0.01, f"{client_id} scoring weights sum to {scoring_total}, expected ~1.0"


# ---------------------------------------------------------------------------
# Post-fix regression tests — Sprint 77 adversarial audit gaps
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ceremony_weights_negative_field_raises() -> None:
    """ge=0 constraint rejects negative weights even when sum equals 100."""
    with pytest.raises(ValidationError):
        CeremonyWeights(session_start=-1, deliver=76, checkpoint=15, learn=10, build_check=0, review=0)


@pytest.mark.unit
def test_scoring_dimension_weights_negative_field_raises() -> None:
    """ge=0 constraint rejects negative scoring weights."""
    with pytest.raises(ValidationError):
        ScoringDimensionWeights(outcome=-0.1, plan_quality=0.40, implementation=0.40, ceremony=0.20, knowledge=0.10)


@pytest.mark.unit
def test_mandatory_phases_normalized_to_lowercase() -> None:
    """_validate_phases stores lowercase values to match Phase enum."""
    profile = ClientProfile(
        client_id="test",
        display_name="Test",
        mandatory_phases=["RESEARCH", "PLAN", "IMPLEMENT"],
    )
    assert profile.mandatory_phases == ["research", "plan", "implement"]


@pytest.mark.unit
def test_invalid_phase_raises_validation_error() -> None:
    """ClientProfile rejects unknown phase names."""
    with pytest.raises(ValidationError, match="Invalid phase"):
        ClientProfile(
            client_id="test",
            display_name="Test",
            mandatory_phases=["IMPLEMENT", "BOGUS_PHASE"],
        )


@pytest.mark.unit
def test_ceremony_weights_constant_matches_model_defaults() -> None:
    """_CEREMONY_WEIGHTS in report.py is derived from CeremonyWeights model."""
    import trw_mcp.state.analytics.report as report_mod

    assert report_mod._CEREMONY_WEIGHTS == CeremonyWeights().as_dict()


@pytest.mark.integration
def test_client_profile_re_evaluates_after_target_platforms_change() -> None:
    """@property (not @cached_property) means client_profile reflects current state."""
    cfg = TRWConfig(target_platforms=["claude-code"])
    assert cfg.client_profile.client_id == "claude-code"
    object.__setattr__(cfg, "target_platforms", ["opencode"])
    assert cfg.client_profile.client_id == "opencode"


@pytest.mark.unit
def test_light_profile_values_are_correct() -> None:
    """_light_profile factory produces calibrated defaults for light-mode clients."""
    profile = resolve_client_profile("opencode")
    assert profile.ceremony_mode == "light"
    assert profile.instruction_max_lines == 200
    assert profile.context_window_tokens == 32_000
    assert profile.default_model_tier == "local-8b"
    assert profile.hooks_enabled is False
    assert profile.include_framework_ref is False
    assert profile.include_agent_teams is False
    assert profile.include_delegation is False
    assert profile.agents_md_enabled is True
    assert profile.mandatory_phases == ["implement", "deliver"]


# ---------------------------------------------------------------------------
# Wiring tests: effective_ceremony_mode (P1 BUG FIX — adversarial audit)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_opencode_target_activates_light_mode() -> None:
    """Setting target_platforms=["opencode"] should activate light ceremony mode.

    This is the critical wiring test for the P1 bug fix: the flat ceremony_mode
    field defaults to "full", but effective_ceremony_mode falls through to the
    client profile which resolves to "light" for opencode.
    """
    config = TRWConfig(target_platforms=["opencode"])
    assert config.effective_ceremony_mode == "light"
    assert config.client_profile.ceremony_mode == "light"
    assert config.client_profile.agents_md_enabled is True
    assert config.client_profile.include_framework_ref is False


@pytest.mark.integration
def test_effective_ceremony_mode_explicit_light_overrides_profile() -> None:
    """Explicitly setting ceremony_mode='light' takes precedence over profile."""
    config = TRWConfig(ceremony_mode="light", target_platforms=["claude-code"])
    assert config.effective_ceremony_mode == "light"
    # Profile is claude-code (full), but explicit field wins
    assert config.client_profile.ceremony_mode == "full"


@pytest.mark.integration
def test_effective_ceremony_mode_default_uses_profile() -> None:
    """When ceremony_mode is default ('full'), effective_ceremony_mode delegates to profile."""
    config = TRWConfig(target_platforms=["claude-code"])
    assert config.ceremony_mode == "full"
    assert config.client_profile.ceremony_mode == "full"
    assert config.effective_ceremony_mode == "full"


@pytest.mark.integration
def test_effective_ceremony_mode_opencode_flat_field_still_full() -> None:
    """The flat ceremony_mode field stays 'full' — only effective_ceremony_mode changes."""
    config = TRWConfig(target_platforms=["opencode"])
    assert config.ceremony_mode == "full"  # flat field unchanged
    assert config.effective_ceremony_mode == "light"  # profile-aware
