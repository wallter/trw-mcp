"""F-01 — Profile model surface enumeration + optionality (PRD-HPO-PROF-001 FR-1).

Companion to ``test_profile_model.py``: this file pins the FR-1 surface
contract from the audit's perspective — the surface is EXACTLY the 10 documented
override keys, every field is optional, and an unknown 11th key fails closed
with a ValidationError. Pure logic, no filesystem I/O.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.profile import PROFILE_SURFACE_KEYS, Profile


def test_surface_keys_enumerate_exactly_the_ten_model_fields() -> None:
    """FR-1: PROFILE_SURFACE_KEYS is exactly the 10 override fields on Profile.

    ``env`` is a deliberate validation-context field and is NOT part of the
    override surface, so the surface enumeration must equal the model's
    override fields with ``env`` excluded.
    """
    assert len(PROFILE_SURFACE_KEYS) == 10
    model_override_fields = set(Profile.model_fields) - {"env"}
    assert set(PROFILE_SURFACE_KEYS) == model_override_fields
    # No duplicates in the tuple.
    assert len(set(PROFILE_SURFACE_KEYS)) == len(PROFILE_SURFACE_KEYS)


def test_every_surface_key_is_optional_and_defaults_none() -> None:
    """FR-1: an empty Profile validates; each surface key defaults to None."""
    profile = Profile()
    for key in PROFILE_SURFACE_KEYS:
        field = Profile.model_fields[key]
        assert not field.is_required(), f"{key} must be optional"
        assert getattr(profile, key) is None


def test_unknown_eleventh_key_raises_validation_error() -> None:
    """FR-1: an unknown 11th key is rejected (extra='forbid'), not silently dropped."""
    payload: dict[str, object] = dict.fromkeys(PROFILE_SURFACE_KEYS)
    payload["an_eleventh_unknown_key"] = "x"
    with pytest.raises(ValidationError) as exc:
        Profile.model_validate(payload)
    assert "an_eleventh_unknown_key" in str(exc.value)


def test_env_is_not_a_surface_override_key() -> None:
    """FR-9: ``env`` is a validation-context field, excluded from the surface."""
    assert "env" not in PROFILE_SURFACE_KEYS
    assert "env" in Profile.model_fields
