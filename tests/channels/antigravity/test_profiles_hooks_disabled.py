"""Tests: antigravity-cli profile has hooks_enabled=False (FR01, P0-04).

PRD-DIST-2404 FR01 / audit P0-04.
"""

from __future__ import annotations


def test_antigravity_cli_profile_hooks_disabled() -> None:
    """FR01: _PROFILES['antigravity-cli'].hooks_enabled must be False."""
    from trw_mcp.models.config._profiles import _PROFILES

    profile = _PROFILES.get("antigravity-cli")
    assert profile is not None, "antigravity-cli profile not found in _PROFILES"
    assert profile.hooks_enabled is False, (
        f"Expected hooks_enabled=False for antigravity-cli, got {profile.hooks_enabled!r}"
    )
