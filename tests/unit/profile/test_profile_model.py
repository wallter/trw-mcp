"""FR-1 — Profile schema surface tests (PRD-HPO-PROF-001).

The Profile model accepts exactly the 10 documented override keys, all
optional, and rejects unknown keys with a ValidationError.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.profile import PROFILE_SURFACE_KEYS, Profile


def test_profile_schema_keys_exact_ten() -> None:
    """FR-1: the surface declares exactly the 10 documented override keys."""
    assert len(PROFILE_SURFACE_KEYS) == 10
    assert set(PROFILE_SURFACE_KEYS) == {
        "ceremony_tier",
        "phase_enabled_set",
        "allowed_tools_by_phase",
        "recall_policy",
        "checkpoint_cadence",
        "review_threshold",
        "confidence_bands",
        "build_check_scope",
        "cost_budget_usd",
        "token_budget",
    }
    # All 10 surface keys are real fields on the model.
    for key in PROFILE_SURFACE_KEYS:
        assert key in Profile.model_fields


def test_profile_fields_all_optional() -> None:
    """FR-1: an empty profile validates (every override key is optional)."""
    profile = Profile()
    for key in PROFILE_SURFACE_KEYS:
        assert getattr(profile, key) is None


def test_profile_rejects_unknown_key_raises_validation_error() -> None:
    """FR-1: an unknown key raises ValidationError (extra='forbid')."""
    with pytest.raises(ValidationError):
        Profile.model_validate({"not_a_real_key": "x"})


def test_profile_accepts_all_surface_keys() -> None:
    """FR-1: a profile setting every surface key validates and round-trips."""
    profile = Profile.model_validate(
        {
            "ceremony_tier": "STANDARD",
            "phase_enabled_set": ["IMPLEMENT", "DELIVER"],
            "allowed_tools_by_phase": {"IMPLEMENT": ["trw_learn"]},
            "recall_policy": {"k": 5, "min_impact": 0.7, "rerank_strategy": "mmr"},
            "checkpoint_cadence": "aggressive",
            "review_threshold": "COMPREHENSIVE",
            "confidence_bands": {"high": 0.9, "medium": 0.6, "low": 0.3},
            "build_check_scope": "full",
            "cost_budget_usd": 12.5,
            "token_budget": 200000,
        }
    )
    assert profile.ceremony_tier == "STANDARD"
    assert profile.recall_policy is not None
    assert profile.recall_policy.k == 5
    assert profile.confidence_bands is not None
    assert profile.confidence_bands.high == 0.9


def test_profile_rejects_bad_literal_value() -> None:
    """FR-1: a value outside the Literal set raises ValidationError."""
    with pytest.raises(ValidationError):
        Profile.model_validate({"ceremony_tier": "ULTRA"})
