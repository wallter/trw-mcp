"""PRD-INFRA-132 FR03: verify ClientProfile carries feedback_skill metadata.

The field defaults to "trw-feedback". Profiles MAY override with None to
opt out, in which case the bootstrapper writes only the llms.txt link with
no skill invocation. Light-mode gating (FR02) is enforced elsewhere -- this
test only validates the schema field exposure and the default-by-omission
pattern across every built-in profile.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config._client_profile import ClientProfile
from trw_mcp.models.config._profiles import _PROFILES


def test_default_feedback_skill_is_trw_feedback() -> None:
    """A bare ClientProfile() has feedback_skill = 'trw-feedback'."""
    profile = ClientProfile(client_id="test", display_name="Test")
    assert profile.feedback_skill == "trw-feedback"


def test_feedback_skill_accepts_none_for_opt_out() -> None:
    """A profile MAY set feedback_skill=None to opt out of skill install."""
    profile = ClientProfile(client_id="test", display_name="Test", feedback_skill=None)
    assert profile.feedback_skill is None


def test_feedback_skill_accepts_custom_string() -> None:
    """Custom skill names parse cleanly (future-proofing for variants)."""
    profile = ClientProfile(client_id="test", display_name="Test", feedback_skill="trw-feedback-custom")
    assert profile.feedback_skill == "trw-feedback-custom"


@pytest.mark.parametrize("client_id", sorted(_PROFILES.keys()))
def test_every_builtin_profile_has_feedback_skill(client_id: str) -> None:
    """Every built-in profile carries a valid feedback_skill value.

    Either it inherits the default ('trw-feedback') or has an explicit
    string / None override. No profile MAY raise ValidationError.
    """
    profile = _PROFILES[client_id]
    # Field exists on the model and is one of the valid value classes.
    assert hasattr(profile, "feedback_skill")
    value = profile.feedback_skill
    assert value is None or isinstance(value, str)


def test_default_builtin_profile_inherits_default() -> None:
    """The flagship claude-code profile inherits the 'trw-feedback' default.

    No explicit override exists in _profiles.py, so the model default
    must flow through unchanged.
    """
    assert _PROFILES["claude-code"].feedback_skill == "trw-feedback"


def test_all_builtin_profiles_parse_without_error() -> None:
    """Importing _PROFILES already constructed every profile; if any had
    an invalid feedback_skill value, this module would have raised at
    import time. Assert the dict is populated as a smoke check."""
    assert len(_PROFILES) >= 8
    for profile in _PROFILES.values():
        # Re-validate via model_dump round-trip to catch silent field drops.
        dumped = profile.model_dump()
        assert "feedback_skill" in dumped
