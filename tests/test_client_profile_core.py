"""Core client profile tests."""

from __future__ import annotations

from unittest.mock import patch

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


@pytest.mark.unit
@pytest.mark.parametrize(
    "client_id",
    ["claude-code", "opencode", "cursor-ide", "cursor-cli", "codex", "aider"],
)
def test_all_profiles_construct(client_id: str) -> None:
    """resolve_client_profile(id) returns a ClientProfile for all built-ins."""
    profile = resolve_client_profile(client_id)
    assert profile.client_id == client_id


@pytest.mark.unit
@pytest.mark.parametrize(
    "client_id",
    ["claude-code", "opencode", "cursor-ide", "cursor-cli", "codex", "copilot", "gemini", "aider"],
)
def test_nudge_density_profile_defaults(client_id: str) -> None:
    """PRD-CORE-146 FR04: no built-in profile opts into a nudge_density today."""
    profile = resolve_client_profile(client_id)
    assert profile.nudge_density is None


@pytest.mark.unit
def test_ceremony_weights_valid_sum_100() -> None:
    """CeremonyWeights with fields summing to 100 constructs successfully."""
    weights = CeremonyWeights(
        session_start=30,
        deliver=30,
        checkpoint=5,
        learn=20,
        build_check=15,
        review=0,
    )
    assert (
        weights.session_start
        + weights.deliver
        + weights.checkpoint
        + weights.learn
        + weights.build_check
        + weights.review
        == 100
    )


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
            review=15,
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
            review=15,
        )


@pytest.mark.unit
def test_scoring_dimension_weights_valid_sum() -> None:
    """ScoringDimensionWeights with fields summing to ~1.0 constructs successfully."""
    weights = ScoringDimensionWeights(
        outcome=0.60,
        plan_quality=0.05,
        implementation=0.15,
        ceremony=0.05,
        knowledge=0.15,
    )
    total = weights.outcome + weights.plan_quality + weights.implementation + weights.ceremony + weights.knowledge
    assert abs(total - 1.0) <= 0.01


@pytest.mark.unit
def test_scoring_dimension_weights_tolerance_boundary() -> None:
    """ScoringDimensionWeights tolerates floating-point near 1.0 (within 0.01)."""
    weights = ScoringDimensionWeights(
        outcome=0.505,
        plan_quality=0.150,
        implementation=0.150,
        ceremony=0.100,
        knowledge=0.095,
    )
    total = weights.outcome + weights.plan_quality + weights.implementation + weights.ceremony + weights.knowledge
    assert abs(total - 1.0) <= 0.01


@pytest.mark.unit
def test_scoring_dimension_weights_over_limit_raises() -> None:
    """ScoringDimensionWeights with sum > 1.01 raises ValidationError."""
    with pytest.raises(ValidationError, match=r"must sum to ~1\.0"):
        ScoringDimensionWeights(
            outcome=0.60,
            plan_quality=0.20,
            implementation=0.20,
            ceremony=0.10,
            knowledge=0.10,
        )


@pytest.mark.unit
def test_unknown_client_id_falls_back_to_claude_code() -> None:
    """resolve_client_profile('windsurf') falls back to claude-code."""
    with patch("trw_mcp.models.config._profiles.logger") as mock_logger:
        profile = resolve_client_profile("windsurf")

    assert profile.client_id == "claude-code"
    mock_logger.warning.assert_called_once_with(
        "unknown_client_id_fallback",
        client_id="windsurf",
        fallback="claude-code",
    )


@pytest.mark.unit
def test_model_tier_override_local_large_adjusts_context() -> None:
    """resolve_client_profile('opencode', 'local-large') returns context=128k via model_copy."""
    profile = resolve_client_profile("opencode", model_tier="local-large")
    assert profile.context_window_tokens == 128_000
    assert profile.instruction_max_lines == 350


@pytest.mark.unit
def test_model_tier_override_does_not_mutate_registry() -> None:
    """Model tier override returns a NEW profile — does not mutate the registry entry."""
    original = resolve_client_profile("opencode")
    overridden = resolve_client_profile("opencode", model_tier="local-large")
    assert original.context_window_tokens == 32_000
    assert overridden.context_window_tokens == 128_000


@pytest.mark.integration
def test_trwconfig_single_opencode_platform_resolves_profile() -> None:
    """TRWConfig with target_platforms=['opencode'] resolves opencode client_profile."""
    cfg = TRWConfig(target_platforms=["opencode"])
    assert cfg.client_profile.client_id == "opencode"


@pytest.mark.integration
def test_empty_target_platforms_defaults_to_claude_code() -> None:
    """TRWConfig with empty target_platforms resolves claude-code client_profile."""
    cfg = TRWConfig(target_platforms=[])
    assert cfg.client_profile.client_id == "claude-code"


@pytest.mark.integration
def test_multi_platform_uses_first() -> None:
    """TRWConfig with multiple target_platforms uses the first one."""
    cfg = TRWConfig(target_platforms=["opencode", "claude-code"])
    assert cfg.client_profile.client_id == "opencode"


@pytest.mark.unit
def test_client_profile_frozen_raises_on_assignment() -> None:
    """Frozen ClientProfile raises ValidationError on attribute assignment."""
    profile = resolve_client_profile("claude-code")
    with pytest.raises((ValidationError, TypeError)):
        profile.client_id = "hacked"  # type: ignore[misc]


@pytest.mark.unit
def test_ceremony_weights_frozen_raises_on_assignment() -> None:
    """Frozen CeremonyWeights raises TypeError on attribute assignment."""
    weights = CeremonyWeights()
    with pytest.raises((ValidationError, TypeError)):
        weights.session_start = 99  # type: ignore[misc]


@pytest.mark.unit
def test_write_targets_frozen_raises_on_assignment() -> None:
    """Frozen WriteTargets raises TypeError on attribute assignment."""
    write_targets = WriteTargets(claude_md=True)
    with pytest.raises((ValidationError, TypeError)):
        write_targets.claude_md = False  # type: ignore[misc]


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
def test_cursor_ide_profile_writes_cursor_rules() -> None:
    """cursor-ide profile has write_targets.cursor_rules=True and agents_md=True."""
    profile = resolve_client_profile("cursor-ide")
    assert profile.write_targets.cursor_rules is True
    assert profile.write_targets.agents_md is True
    assert profile.write_targets.claude_md is False


@pytest.mark.unit
def test_compute_ceremony_score_with_custom_weights() -> None:
    """compute_ceremony_score uses custom CeremonyWeights when provided."""
    events: list[dict[str, object]] = [{"event": "session_start"}, {"event": "learn_new_entry"}]
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
    assert result["score"] == 100


@pytest.mark.unit
def test_ceremony_weights_as_dict_returns_correct_keys() -> None:
    """as_dict() returns a dict with all 6 ceremony component keys."""
    weights = CeremonyWeights()
    values = weights.as_dict()
    assert isinstance(values, dict)
    assert set(values.keys()) == {"session_start", "deliver", "checkpoint", "learn", "build_check", "review"}
    assert all(isinstance(value, int) for value in values.values())


@pytest.mark.unit
@pytest.mark.parametrize(
    "client_id",
    ["claude-code", "opencode", "cursor-ide", "cursor-cli", "codex", "aider"],
)
def test_all_profiles_have_valid_weights(client_id: str) -> None:
    """Every built-in profile has ceremony weights summing to 100 and scoring weights summing to ~1.0."""
    profile = resolve_client_profile(client_id)
    ceremony_total = (
        profile.ceremony_weights.session_start
        + profile.ceremony_weights.deliver
        + profile.ceremony_weights.checkpoint
        + profile.ceremony_weights.learn
        + profile.ceremony_weights.build_check
        + profile.ceremony_weights.review
    )
    assert ceremony_total == 100, f"{client_id} ceremony weights sum to {ceremony_total}, expected 100"

    scoring_total = (
        profile.scoring_weights.outcome
        + profile.scoring_weights.plan_quality
        + profile.scoring_weights.implementation
        + profile.scoring_weights.ceremony
        + profile.scoring_weights.knowledge
    )
    assert abs(scoring_total - 1.0) <= 0.01, f"{client_id} scoring weights sum to {scoring_total}, expected ~1.0"


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
    assert profile.default_model_tier == "local-small"
    assert profile.hooks_enabled is False
    assert profile.include_framework_ref is False
    assert not hasattr(profile, "include_agent" + "_teams")
    assert profile.include_delegation is False
    assert profile.agents_md_enabled is True
    assert profile.mandatory_phases == ["implement", "deliver"]
